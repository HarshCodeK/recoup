"""Append-only event log with SHA-256 hash chain.

Every state transition, every policy decision, every recovery action
is logged as an immutable event. Each event's hash includes the
previous event's hash, forming a tamper-evident chain.

Storage: SQLite (single file, no external deps).
"""
from __future__ import annotations
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.data_model import TransactionState


class EventType(str):
    """Event type constants."""
    INGEST = "ingest"
    RULE_MATCH = "rule_match"
    PROB_MATCH = "prob_match"
    EXCEPTION_CLASSIFIED = "exception_classified"
    LLM_DIAGNOSIS = "llm_diagnosis"
    POLICY_DECISION = "policy_decision"
    STATE_TRANSITION = "state_transition"
    RECOVERY_PROPOSED = "recovery_proposed"
    RECOVERY_EXECUTED = "recovery_executed"
    TAMPER_DETECTED = "tamper_detected"


class EventStore:
    """SQLite-backed append-only event log with SHA-256 hash chain."""

    def __init__(self, db_path: str | Path = ":memory:"):
        self.db_path = str(db_path)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self):
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                txn_id TEXT,
                event_type TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                hash TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        self._conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @staticmethod
    def _canonical_json(payload: dict) -> str:
        """Canonical JSON: sorted keys, no whitespace, deterministic."""
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)

    def _last_hash(self) -> str:
        """Get the hash of the most recent event, or GENESIS_HASH if empty."""
        row = self._conn.execute(
            "SELECT hash FROM events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return "0" * 64  # Genesis hash
        return row[0]

    def append(
        self,
        event_type: str,
        payload: dict,
        txn_id: Optional[str] = None,
    ) -> int:
        """Append a new event. Returns the seq number."""
        prev_hash = self._last_hash()
        # Store txn_id inside the payload too so the hash includes it
        full_payload = dict(payload)
        if txn_id is not None:
            full_payload["_txn_id"] = txn_id
        payload_to_hash = {
            "event_type": event_type,
            "txn_id": txn_id,
            "payload": full_payload,
            "prev_hash": prev_hash,
        }
        canonical = self._canonical_json(payload_to_hash)
        h = hashlib.sha256(canonical.encode()).hexdigest()
        created_at = datetime.utcnow().isoformat() + "Z"

        cur = self._conn.execute(
            "INSERT INTO events (txn_id, event_type, prev_hash, hash, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (txn_id, event_type, prev_hash, h, json.dumps(payload, default=str), created_at),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_events_for_txn(self, txn_id: str) -> list[dict]:
        """Get all events for one transaction, in order."""
        rows = self._conn.execute(
            "SELECT seq, txn_id, event_type, prev_hash, hash, payload_json, created_at FROM events WHERE txn_id = ? ORDER BY seq ASC",
            (txn_id,),
        ).fetchall()
        return [
            {
                "seq": r[0],
                "txn_id": r[1],
                "event_type": r[2],
                "prev_hash": r[3],
                "hash": r[4],
                "payload": json.loads(r[5]),
                "created_at": r[6],
            }
            for r in rows
        ]

    def get_all_events(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT seq, txn_id, event_type, prev_hash, hash, payload_json, created_at FROM events ORDER BY seq ASC"
        ).fetchall()
        return [
            {
                "seq": r[0],
                "txn_id": r[1],
                "event_type": r[2],
                "prev_hash": r[3],
                "hash": r[4],
                "payload": json.loads(r[5]),
                "created_at": r[6],
            }
            for r in rows
        ]

    def verify_chain(self) -> tuple[bool, Optional[int]]:
        """Verify the entire hash chain. Returns (ok, broken_at_seq).

        If broken_at_seq is not None, that's the seq number where the
        first inconsistency was detected.
        """
        rows = self._conn.execute(
            "SELECT seq, txn_id, event_type, prev_hash, hash, payload_json FROM events ORDER BY seq ASC"
        ).fetchall()

        prev_hash = "0" * 64
        for r in rows:
            seq, txn_id, event_type, stored_prev, stored_hash, payload_str = r
            payload = json.loads(payload_str)
            # Rebuild canonical payload the same way append() does
            full_payload = dict(payload)
            if txn_id is not None:
                full_payload["_txn_id"] = txn_id
            payload_to_hash = {
                "event_type": event_type,
                "txn_id": txn_id,
                "payload": full_payload,
                "prev_hash": prev_hash,
            }
            canonical = self._canonical_json(payload_to_hash)
            computed = hashlib.sha256(canonical.encode()).hexdigest()
            if stored_prev != prev_hash or stored_hash != computed:
                return False, seq
            prev_hash = stored_hash
        return True, None

    def tamper_for_testing(self, seq: int, new_payload: dict):
        """DANGEROUS: Modify an event's payload to simulate tampering.

        Used by tests to verify the chain detects modifications.
        NOT exposed in any non-test code path.
        """
        self._conn.execute(
            "UPDATE events SET payload_json = ? WHERE seq = ?",
            (json.dumps(new_payload, default=str), seq),
        )
        self._conn.commit()