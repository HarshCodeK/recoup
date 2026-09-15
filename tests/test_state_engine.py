"""Tests for state_engine.py."""
import pytest
from src.state_engine import is_allowed, transition, is_terminal, classify_webhook_outcome, InvalidTransition
from src.data_model import TransactionState


def test_initiated_to_pending_allowed():
    assert is_allowed(TransactionState.INITIATED, TransactionState.PENDING)


def test_initiated_to_success_not_allowed():
    assert not is_allowed(TransactionState.INITIATED, TransactionState.SUCCESS)


def test_unknown_to_success_not_allowed():
    """UNKNOWN must go through RECONCILING before SUCCESS."""
    assert not is_allowed(TransactionState.UNKNOWN, TransactionState.SUCCESS)
    assert is_allowed(TransactionState.UNKNOWN, TransactionState.RECONCILING)


def test_unknown_to_reconciling_allowed():
    assert is_allowed(TransactionState.UNKNOWN, TransactionState.RECONCILING)


def test_reconciling_to_success_allowed():
    assert is_allowed(TransactionState.RECONCILING, TransactionState.SUCCESS)


def test_success_is_terminal():
    assert is_terminal(TransactionState.SUCCESS)
    assert is_terminal(TransactionState.FAILED)


def test_pending_is_not_terminal():
    assert not is_terminal(TransactionState.PENDING)


def test_transition_returns_target():
    assert transition(TransactionState.INITIATED, TransactionState.PENDING) == TransactionState.PENDING


def test_invalid_transition_raises():
    with pytest.raises(InvalidTransition):
        transition(TransactionState.SUCCESS, TransactionState.PENDING)


def test_same_state_transition_is_noop():
    """Same-state transition is allowed (idempotent)."""
    assert transition(TransactionState.PENDING, TransactionState.PENDING) == TransactionState.PENDING


def test_classify_webhook_captured():
    assert classify_webhook_outcome("captured", TransactionState.PENDING) == TransactionState.SUCCESS


def test_classify_webhook_failed():
    assert classify_webhook_outcome("failed", TransactionState.PENDING) == TransactionState.FAILED


def test_classify_webhook_timeout_to_unknown():
    assert classify_webhook_outcome("timeout", TransactionState.PENDING) == TransactionState.UNKNOWN


def test_classify_webhook_unknown_status_to_hold():
    assert classify_webhook_outcome("weird_thing", TransactionState.PENDING) == TransactionState.HOLD


def test_cannot_skip_unknown_to_success():
    """The whole point of UNKNOWN: never blind-confirm a SUCCESS."""
    with pytest.raises(InvalidTransition):
        classify_webhook_outcome("captured", TransactionState.UNKNOWN)