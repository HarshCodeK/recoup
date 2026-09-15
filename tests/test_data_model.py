"""Tests for data_model.py."""
import pytest
from datetime import datetime
from src.data_model import (
    NormalizedTxn, Source, TxnDirection, TransactionState,
    MatchDecision, MatchSource,
    ExceptionReason, ExceptionRecord,
    Diagnosis, DiagnosisClass,
)


def test_normalized_txn_minimal():
    t = NormalizedTxn(
        txn_id="t1", source=Source.UPI, direction=TxnDirection.CREDIT,
        amount_paise=100000, currency="INR",
        timestamp=datetime(2026, 6, 1, 10, 0),
    )
    assert t.state == TransactionState.PENDING
    assert t.reference is None


def test_amount_must_be_non_negative():
    with pytest.raises(Exception):
        NormalizedTxn(
            txn_id="t1", source=Source.UPI, direction=TxnDirection.CREDIT,
            amount_paise=-1, currency="INR",
            timestamp=datetime(2026, 6, 1, 10, 0),
        )


def test_currency_must_be_3_chars():
    with pytest.raises(Exception):
        NormalizedTxn(
            txn_id="t1", source=Source.UPI, direction=TxnDirection.CREDIT,
            amount_paise=100, currency="IN",  # too short
            timestamp=datetime(2026, 6, 1, 10, 0),
        )


def test_match_decision_minimal():
    d = MatchDecision(
        txn_id_a="t1", txn_id_b="t2",
        matched=True, source=MatchSource.RULE,
    )
    assert d.score == 0.0
    assert d.reasons == []


def test_exception_record_required_fields():
    r = ExceptionRecord(
        txn_id="t1", source=Source.UPI, amount_paise=100000,
        reason=ExceptionReason.AMOUNT_MISMATCH,
        timestamp=datetime(2026, 6, 1, 10, 0),
    )
    assert r.needs_human is True
    assert r.auto_recoverable is False


def test_diagnosis_refused_requires_refused_class():
    d = Diagnosis(
        txn_id="t1", diagnosis_class=DiagnosisClass.REFUSED,
        confidence=0.0, refused=True,
    )
    assert d.refused is True


def test_diagnosis_classified_is_not_refused():
    d = Diagnosis(
        txn_id="t1", diagnosis_class=DiagnosisClass.DATA_INCOMPLETE,
        confidence=0.85, refused=False,
    )
    assert d.refused is False