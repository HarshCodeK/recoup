"""MCP server exposing 5 tools for AI agents.

JSON-RPC 2.0 over stdio. Each tool is a thin facade over the
deterministic reconciliation engine. The LLM (in the agent) calls
these tools; the tools return typed results.

Tools exposed:
  1. reconcile_batch   — ingest + match a batch of transactions
  2. get_exceptions    — list exceptions from the last batch
  3. replay_txn        — get full event timeline for one transaction
  4. propose_recovery  — propose a recovery action for an exception
  5. verify_audit_chain — verify the event log's hash chain

The LLM does NOT have authority over money. These tools inform the
agent; they don't execute refunds. Recovery actions are PROPOSALS
that humans (or downstream automation) approve.

State persistence: the MCP server uses a singleton EventStore backed
by a real on-disk SQLite file (default: ./.recoup/audit.db) so that
audit events and exception records persist across calls. The store
is opened lazily on first use and closed on process exit.
"""
from __future__ import annotations

import atexit
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

from fixtures.generate_dataset import generate
from src.orchestrator import run_pipeline
from src.event_store import EventStore
from src.recovery import propose_recovery
from src.data_model import NormalizedTxn, ExceptionReason


# ---------------------------------------------------------------------------
# Singleton EventStore — persistence across MCP calls
# ---------------------------------------------------------------------------

_STORE_PATH = Path(
    os.environ.get("RECOUP_AUDIT_DB") or "./.recoup/audit.db"
).resolve()
_STORE_LOCK = threading.Lock()
_store: EventStore | None = None
_last_batch_exceptions: list[dict] = []  # populated by reconcile_batch


def _get_store() -> EventStore:
    """Return the process-wide EventStore, opening it on first use."""
    global _store
    if _store is None:
        with _STORE_LOCK:
            if _store is None:
                _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
                _store = EventStore(_STORE_PATH)
                atexit.register(_close_store)
    return _store


def _close_store():
    global _store
    with _STORE_LOCK:
        if _store is not None:
            _store.close()
            _store = None


# ---------------------------------------------------------------------------
# JSON-RPC plumbing
# ---------------------------------------------------------------------------


def _read_message() -> dict | None:
    """Read one JSON-RPC message from stdin (newline-delimited)."""
    line = sys.stdin.readline()
    if not line:
        return None
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _send_message(msg: dict):
    """Write one JSON-RPC message to stdout."""
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def _ok(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def tool_reconcile_batch(arguments: dict) -> dict:
    """Run the reconciliation pipeline on a batch.

    Args:
        arguments: {
            "transactions": list of NormalizedTxn-shaped dicts,
            "dataset_size": int (optional, default 300 — used if transactions empty)
        }
    """
    raw_txns = arguments.get("transactions", [])
    if not raw_txns:
        # Generate synthetic dataset for demo
        size = int(arguments.get("dataset_size", 300))
        txns, _ = generate(total_transactions=size, seed=42)
    else:
        txns = [NormalizedTxn(**t) for t in raw_txns]

    # Run pipeline
    result = run_pipeline(txns)

    # Persist audit events to the singleton store (real persistence,
    # not a fresh in-memory store every call).
    store = _get_store()
    for d in result.rule_decisions:
        if d.matched:
            store.append(
                "rule_match",
                {"a": d.txn_id_a, "b": d.txn_id_b, "score": d.score},
                d.txn_id_a,
            )
    for d in result.prob_decisions:
        if d.matched:
            store.append(
                "prob_match",
                {"a": d.txn_id_a, "b": d.txn_id_b, "score": d.score},
                d.txn_id_a,
            )
    for exc in result.exceptions:
        store.append(
            "exception_classified",
            {"reason": exc.reason.value, "amount": exc.amount_paise},
            exc.txn_id,
        )

    chain_ok, _ = store.verify_chain()
    chain_hash = store._last_hash()

    # Cache exceptions so get_exceptions can return them without rerunning
    global _last_batch_exceptions
    _last_batch_exceptions = [
        {
            "txn_id": e.txn_id,
            "source": e.source.value,
            "amount_paise": e.amount_paise,
            "reason": e.reason.value,
            "reference": e.reference,
        }
        for e in result.exceptions
    ]

    return {
        "total": result.report.total,
        "auto_matched": result.report.rule_matched + result.report.prob_matched,
        "auto_match_rate_pct": round(result.report.auto_match_rate, 2),
        "exceptions": result.report.exceptions,
        "exception_breakdown": result.report.exception_breakdown,
        "events_recorded": sum(1 for _ in store.get_all_events()),
        "audit_chain_ok": chain_ok,
        "audit_chain_hash": chain_hash,
        "audit_db": str(_STORE_PATH),
    }


def tool_get_exceptions(arguments: dict) -> dict:
    """Return the exception list from the last batch run.

    Reads from the cached batch (populated by tool_reconcile_batch).
    Falls back to a fresh pipeline run only if no batch has been seen
    yet in this process — this keeps the tool idempotent and prevents
    fake "regenerate each call" behavior from leaking.
    """
    global _last_batch_exceptions
    if not _last_batch_exceptions:
        # No prior batch — run one with default size so the tool still works
        # in isolation (used in single-call MCP smoke tests).
        size = int(arguments.get("dataset_size", 300))
        txns, _ = generate(total_transactions=size, seed=42)
        result = run_pipeline(txns)
        _last_batch_exceptions = [
            {
                "txn_id": e.txn_id,
                "source": e.source.value,
                "amount_paise": e.amount_paise,
                "reason": e.reason.value,
                "reference": e.reference,
            }
            for e in result.exceptions
        ]

    return {
        "count": len(_last_batch_exceptions),
        "by_reason": _reason_breakdown(_last_batch_exceptions),
        "sample": [
            {
                "txn_id": e["txn_id"],
                "source": e["source"],
                "amount_inr": e["amount_paise"] / 100,
                "reason": e["reason"],
                "reference": e["reference"],
            }
            for e in _last_batch_exceptions[:10]
        ],
    }


def _reason_breakdown(excs: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in excs:
        r = e["reason"]
        counts[r] = counts.get(r, 0) + 1
    return counts


def tool_replay_txn(arguments: dict) -> dict:
    """Replay the full decision timeline for one transaction.

    Queries the real event store for events belonging to txn_id.
    This replaces the previous hardcoded synthesized timeline.

    Args:
        arguments: {"txn_id": str}
    """
    txn_id = arguments.get("txn_id", "")
    if not txn_id:
        return {"error": "txn_id required"}

    store = _get_store()
    events = store.get_events_for_txn(txn_id)

    if not events:
        return {
            "txn_id": txn_id,
            "timeline": [],
            "note": "no_events_recorded",
            "hint": (
                "run tool_reconcile_batch first, or check whether the "
                "txn_id belongs to a batch whose events were recorded "
                "in a different audit db"
            ),
        }

    timeline = [
        {
            "seq": e["seq"],
            "event": e["event_type"],
            "prev_hash": e["prev_hash"],
            "hash": e["hash"],
            "payload": e["payload"],
            "created_at": e["created_at"],
        }
        for e in events
    ]
    return {
        "txn_id": txn_id,
        "timeline": timeline,
        "events_count": len(timeline),
        "first_seq": timeline[0]["seq"],
        "last_seq": timeline[-1]["seq"],
        "source": "persisted_event_store",
    }


def tool_propose_recovery(arguments: dict) -> dict:
    """Propose a recovery action for one exception.

    Args:
        arguments: {
            "txn_id": str,
            "reason": str (ExceptionReason value),
            "amount_paise": int,
            "reference": str (optional),
            "source": str (optional)
        }
    """
    from src.data_model import Source, TxnDirection
    from datetime import datetime

    try:
        reason = ExceptionReason(arguments.get("reason", "ambiguous"))
    except ValueError:
        reason = ExceptionReason.AMBIGUOUS
    try:
        source = Source(arguments.get("source", "upi"))
    except ValueError:
        source = Source.UPI
    txn = NormalizedTxn(
        txn_id=arguments.get("txn_id", ""),
        source=source,
        direction=TxnDirection.CREDIT,
        amount_paise=int(arguments.get("amount_paise", 100000)),
        currency="INR",
        timestamp=datetime.utcnow(),
        reference=arguments.get("reference"),
    )
    action, policy = propose_recovery(txn, reason)
    return {
        "action_type": action.action_type,
        "description": action.description,
        "idempotency_key": action.idempotency_key,
        "requires_human_approval": action.requires_human_approval,
        "policy_allowed": policy.allowed,
        "policy_reason": policy.reason,
    }


def tool_verify_audit_chain(arguments: dict) -> dict:
    """Verify the integrity of the persisted event log's hash chain."""
    store = _get_store()
    ok, broken_at = store.verify_chain()
    all_events = store.get_all_events()
    by_type: dict[str, int] = {}
    for e in all_events:
        by_type[e["event_type"]] = by_type.get(e["event_type"], 0) + 1
    return {
        "chain_ok": ok,
        "broken_at_seq": broken_at,
        "events_recorded": len(all_events),
        "events_by_type": by_type,
        "audit_db": str(_STORE_PATH),
        "first_seq": all_events[0]["seq"] if all_events else None,
        "last_seq": all_events[-1]["seq"] if all_events else None,
    }


TOOLS = {
    "reconcile_batch": tool_reconcile_batch,
    "get_exceptions": tool_get_exceptions,
    "replay_txn": tool_replay_txn,
    "propose_recovery": tool_propose_recovery,
    "verify_audit_chain": tool_verify_audit_chain,
}


def handle_request(req: dict) -> dict:
    """Dispatch one JSON-RPC request."""
    req_id = req.get("id")
    method = req.get("method")
    params = req.get("params", {})

    if method == "tools/list":
        return _ok(
            req_id,
            {
                "tools": [
                    {"name": name, "description": "see README"}
                for name in TOOLS.keys()
                ]
            },
        )
    elif method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        if tool_name not in TOOLS:
            return _err(req_id, -32601, f"unknown tool: {tool_name}")
        try:
            result = TOOLS[tool_name](arguments)
            return _ok(req_id, result)
        except Exception as e:
            return _err(req_id, -32603, f"tool execution failed: {e}")
    else:
        return _err(req_id, -32601, f"unknown method: {method}")


def serve():
    """Main loop: read JSON-RPC from stdin, write to stdout."""
    while True:
        req = _read_message()
        if req is None:
            break
        response = handle_request(req)
        _send_message(response)


if __name__ == "__main__":
    try:
        serve()
    finally:
        _close_store()