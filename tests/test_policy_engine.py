"""Tests for policy_engine.py — hard invariants."""
import pytest
from datetime import datetime
from src.policy_engine import evaluate_recovery, DEFAULT_CONFIG
from src.data_model import NormalizedTxn, Source, TxnDirection, ExceptionReason


def _txn(amount_paise, txn_id="t1"):
    return NormalizedTxn(
        txn_id=txn_id, source=Source.UPI, direction=TxnDirection.CREDIT,
        amount_paise=amount_paise, currency="INR",
        timestamp=datetime(2026, 6, 1, 10, 0),
        reference="ord_42",
    )


def test_amount_below_floor_blocked():
    txn = _txn(50_00)  # ₹50
    decision = evaluate_recovery(txn, ExceptionReason.MISSING_TXN)
    assert not decision.allowed
    assert "floor" in decision.reason.lower()


def test_amount_above_cap_escalated():
    txn = _txn(20_000_00)  # ₹20,000
    decision = evaluate_recovery(txn, ExceptionReason.MISSING_TXN)
    assert not decision.allowed
    assert decision.action == "escalate"


def test_duplicate_fire_never_auto_recovered():
    txn = _txn(1000_00)  # ₹1,000 (within bounds)
    decision = evaluate_recovery(txn, ExceptionReason.DUPLICATE_FIRE)
    assert not decision.allowed
    assert decision.requires_human


def test_max_attempts_escalated():
    txn = _txn(1000_00)
    decision = evaluate_recovery(txn, ExceptionReason.MISSING_TXN, prior_attempts=3)
    assert not decision.allowed
    assert "max_attempts" in decision.reason.lower()


def test_valid_recovery_allowed():
    txn = _txn(1000_00)
    decision = evaluate_recovery(txn, ExceptionReason.MISSING_TXN)
    assert decision.allowed
    assert decision.action == "recover"


def test_idempotency_key_is_deterministic():
    txn = _txn(1000_00)
    d1 = evaluate_recovery(txn, ExceptionReason.MISSING_TXN)
    d2 = evaluate_recovery(txn, ExceptionReason.MISSING_TXN)
    assert d1.idempotency_key == d2.idempotency_key


def test_idempotency_key_differs_per_action():
    txn = _txn(1000_00)
    d1 = evaluate_recovery(txn, ExceptionReason.MISSING_TXN)
    d2 = evaluate_recovery(txn, ExceptionReason.TIMING_WINDOW)
    assert d1.idempotency_key != d2.idempotency_key


def test_idempotency_key_differs_per_attempt():
    txn = _txn(1000_00)
    d1 = evaluate_recovery(txn, ExceptionReason.MISSING_TXN, prior_attempts=0)
    d2 = evaluate_recovery(txn, ExceptionReason.MISSING_TXN, prior_attempts=1)
    assert d1.idempotency_key != d2.idempotency_key