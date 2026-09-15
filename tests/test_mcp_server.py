"""Tests for mcp_server.py — JSON-RPC over stdio."""
import json
import os
import pytest

import src.mcp_server as mcp_mod
from src.mcp_server import handle_request


@pytest.fixture(autouse=True)
def isolated_audit_db(tmp_path, monkeypatch):
    """Each test gets its own SQLite audit DB so the singleton
    doesn't leak state between tests."""
    db = tmp_path / "audit.db"
    monkeypatch.setenv("RECOUP_AUDIT_DB", str(db))
    # Reset the module-level singleton so a fresh EventStore opens against
    # the tmp db. _close_store() also runs on atexit, but we want a clean
    # slate within the test.
    mcp_mod._store = None
    mcp_mod._last_batch_exceptions = []
    yield
    mcp_mod._close_store()


def _call(tool, arguments=None, req_id=1):
    return handle_request({
        "jsonrpc": "2.0", "id": req_id,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments or {}},
    })


def test_tools_list_returns_5_tools():
    req = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    resp = handle_request(req)
    assert resp["id"] == 1
    assert "result" in resp
    assert len(resp["result"]["tools"]) == 5


def test_reconcile_batch_tool():
    resp = _call("reconcile_batch", {"dataset_size": 50})
    result = resp["result"]
    assert result["total"] == 50
    assert "auto_match_rate_pct" in result
    assert "exception_breakdown" in result
    assert result["audit_chain_ok"] is True


def test_get_exceptions_tool_returns_cached_after_batch():
    # Run a batch first so _last_batch_exceptions is populated
    _call("reconcile_batch", {"dataset_size": 50})
    resp = _call("get_exceptions")
    result = resp["result"]
    assert "count" in result
    assert "by_reason" in result
    assert "sample" in result


def test_get_exceptions_tool_runs_default_if_cold_start():
    # Cold start (no prior batch) — tool should still work, run a default batch
    resp = _call("get_exceptions")
    result = resp["result"]
    assert result["count"] >= 0
    assert "by_reason" in result


def test_replay_txn_after_batch_returns_real_events():
    """End-to-end: reconcile, then replay a txn that the audit recorded."""
    batch_resp = _call("reconcile_batch", {"dataset_size": 80})
    assert batch_resp["result"]["audit_chain_ok"] is True

    # Pick a txn_id that should have been persisted
    sample = batch_resp["result"]["exception_breakdown"]
    # Get an actual recorded txn_id from the event store directly
    store = mcp_mod._get_store()
    events = store.get_all_events()
    assert len(events) > 0, "no events recorded in audit db"
    recorded_txn = next(
        (e["txn_id"] for e in events if e["txn_id"]), None
    )
    assert recorded_txn, "no txn_id-tagged events recorded"

    replay = _call("replay_txn", {"txn_id": recorded_txn})
    result = replay["result"]
    assert result["txn_id"] == recorded_txn
    assert result["events_count"] > 0
    assert result["source"] == "persisted_event_store"
    # Each event in the timeline must carry a hash and a prev_hash
    for ev in result["timeline"]:
        assert "hash" in ev
        assert "prev_hash" in ev
        assert len(ev["hash"]) == 64  # sha256 hex


def test_replay_txn_with_no_prior_batch_is_honest():
    """Calling replay before any batch should NOT fabricate data."""
    resp = _call("replay_txn", {"txn_id": "does_not_exist"})
    result = resp["result"]
    assert result["timeline"] == []
    assert result.get("note") == "no_events_recorded"


def test_replay_txn_requires_txn_id():
    resp = _call("replay_txn", {})
    # No txn_id → tool returns its own error shape (not a JSON-RPC error)
    result = resp["result"]
    assert "error" in result
    assert "required" in result["error"].lower()


def test_propose_recovery_tool_allowed():
    resp = _call("propose_recovery", {
        "txn_id": "t1", "reason": "missing_txn", "amount_paise": 100000,
    })
    result = resp["result"]
    assert result["policy_allowed"] is True


def test_propose_recovery_tool_blocked_by_policy():
    resp = _call("propose_recovery", {
        "txn_id": "t1", "reason": "missing_txn", "amount_paise": 50,
    })
    result = resp["result"]
    assert result["policy_allowed"] is False
    assert "floor" in result["policy_reason"].lower()


def test_verify_audit_chain_after_real_batch():
    _call("reconcile_batch", {"dataset_size": 60})
    resp = _call("verify_audit_chain")
    result = resp["result"]
    assert result["chain_ok"] is True
    assert result["events_recorded"] > 0
    assert "events_by_type" in result


def test_audit_events_persist_across_calls():
    """The singleton EventStore means events written in one call
    are visible in a later call."""
    first = _call("reconcile_batch", {"dataset_size": 50})
    events_after_first = first["result"]["audit_chain_hash"]

    # A second call (different tool, same singleton) should not lose those
    verify = _call("verify_audit_chain")
    recorded = verify["result"]["events_recorded"]
    assert recorded > 0
    # chain hash from first batch should equal verify's last_seq hash chain
    second = _call("reconcile_batch", {"dataset_size": 30})
    events_after_second = second["result"]["events_recorded"]
    assert events_after_second > recorded  # more events accumulated


def test_unknown_tool_returns_error():
    resp = _call("nonexistent")
    assert "error" in resp
    assert resp["error"]["code"] == -32601


def test_unknown_method_returns_error():
    resp = handle_request({"jsonrpc": "2.0", "id": 9, "method": "foo/bar", "params": {}})
    assert "error" in resp