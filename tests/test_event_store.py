"""Tests for event_store.py — SHA-256 hash chain."""
import pytest
from src.event_store import EventStore


def test_append_returns_seq():
    with EventStore(":memory:") as store:
        seq = store.append("test", {"a": 1}, "txn1")
        assert seq == 1


def test_chain_verifies_on_clean_log():
    with EventStore(":memory:") as store:
        store.append("ingest", {"amount": 100}, "t1")
        store.append("rule_match", {"a": "t1", "b": "t2"}, "t1")
        store.append("state_transition", {"to": "SUCCESS"}, "t1")
        ok, broken = store.verify_chain()
        assert ok
        assert broken is None


def test_tampering_detected():
    with EventStore(":memory:") as store:
        store.append("ingest", {"amount": 100}, "t1")
        store.append("rule_match", {"a": "t1", "b": "t2"}, "t1")
        store.append("state_transition", {"to": "SUCCESS"}, "t1")
        # Tamper with event 2
        store.tamper_for_testing(2, {"a": "t1", "b": "FAKE"})
        ok, broken = store.verify_chain()
        assert not ok
        assert broken == 2


def test_get_events_for_txn_filters():
    with EventStore(":memory:") as store:
        store.append("a", {}, "t1")
        store.append("b", {}, "t2")
        store.append("c", {}, "t1")
        events = store.get_events_for_txn("t1")
        assert len(events) == 2
        assert all(e["txn_id"] == "t1" for e in events)


def test_chain_grows_deterministically():
    with EventStore(":memory:") as store:
        for i in range(10):
            store.append("test", {"i": i}, f"t{i}")
        ok, _ = store.verify_chain()
        assert ok


def test_empty_chain_verifies():
    with EventStore(":memory:") as store:
        ok, broken = store.verify_chain()
        assert ok
        assert broken is None


def test_events_have_unique_hashes():
    """Different events → different hashes."""
    with EventStore(":memory:") as store:
        s1 = store.append("type_a", {"x": 1}, "t1")
        s2 = store.append("type_b", {"x": 2}, "t2")
        events = store.get_all_events()
        assert events[0]["hash"] != events[1]["hash"]


def test_prev_hash_links_consecutive():
    """Event N's prev_hash = Event N-1's hash."""
    with EventStore(":memory:") as store:
        store.append("a", {}, "t1")
        store.append("b", {}, "t2")
        events = store.get_all_events()
        assert events[1]["prev_hash"] == events[0]["hash"]