"""Tests for recovery.py — recovery action proposals."""
import pytest
from datetime import datetime
from src.recovery import propose_recovery, RECOVERY_PLAYBOOK
from src.data_model import NormalizedTxn, Source, TxnDirection, ExceptionReason, Diagnosis, DiagnosisClass


def _txn(amount=1000_00):
    return NormalizedTxn(
        txn_id="t1", source=Source.UPI, direction=TxnDirection.CREDIT,
        amount_paise=amount, currency="INR",
        timestamp=datetime(2026, 6, 1, 10, 0),
        reference="ord_42",
    )


def test_propose_for_duplicate_fire_always_requires_human():
    action, policy = propose_recovery(_txn(), ExceptionReason.DUPLICATE_FIRE)
    assert action.action_type == "flag_for_refund"
    assert action.requires_human_approval is True


def test_propose_for_amount_mismatch_uses_reference_lookup():
    action, policy = propose_recovery(_txn(), ExceptionReason.AMOUNT_MISMATCH)
    assert action.action_type == "lookup_reference"
    assert action.requires_human_approval is True


def test_propose_for_missing_txn_can_auto_execute():
    action, policy = propose_recovery(_txn(), ExceptionReason.MISSING_TXN)
    assert action.action_type == "replay_gateway"
    assert policy.allowed is True


def test_propose_for_standalone_recurring_no_action():
    action, policy = propose_recovery(_txn(), ExceptionReason.STANDALONE_RECURRING)
    assert action.action_type == "no_action"


def test_all_exception_reasons_have_playbook():
    for reason in ExceptionReason:
        assert reason in RECOVERY_PLAYBOOK


def test_recovery_action_idempotency_key_set():
    action, _ = propose_recovery(_txn(), ExceptionReason.MISSING_TXN)
    assert action.idempotency_key
    assert len(action.idempotency_key) >= 8