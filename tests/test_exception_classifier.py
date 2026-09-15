"""Tests for exception_classifier.py — why-didn't-this-match classification."""
import pytest
from datetime import datetime, timedelta
from src.data_model import NormalizedTxn, Source, TxnDirection, ExceptionReason
from src.exception_classifier import (
    classify_exception, classify_batch,
    _has_duplicate_within_window, _has_close_match_but_amount_differs,
    _has_timing_drift, _missing_reference,
)


def _txn(txn_id, source, amount, ts, ref="", counterparty=""):
    return NormalizedTxn(
        txn_id=txn_id, source=source, direction=TxnDirection.CREDIT,
        amount_paise=amount, currency="INR", timestamp=ts,
        reference=ref, counterparty=counterparty,
    )


def test_classifies_duplicate_fire():
    ts = datetime(2026, 6, 1, 10, 0)
    a = _txn("upi_1", Source.UPI, 100000, ts, ref="order_abc")
    b = _txn("upi_2", Source.UPI, 100000, ts + timedelta(hours=1), ref="abc")  # same ref, same amount, 1h later
    reason = classify_exception(a, [a, b])
    assert reason == ExceptionReason.DUPLICATE_FIRE


def test_classifies_amount_mismatch():
    ts = datetime(2026, 6, 1, 10, 0)
    target = _txn("upi_1", Source.UPI, 100000, ts, ref="ord_42")
    near = _txn("inv_1", Source.INVOICE, 95000, ts, ref="ord_42")  # 5% diff
    reason = classify_exception(target, [target, near])
    assert reason == ExceptionReason.AMOUNT_MISMATCH


def test_classifies_timing_drift():
    ts = datetime(2026, 6, 1, 10, 0)
    target = _txn("upi_1", Source.UPI, 100000, ts, ref="ord_42")
    drifted = _txn("bank_1", Source.BANK_TRANSFER, 100000, ts + timedelta(days=15), ref="ord_42")
    reason = classify_exception(target, [target, drifted])
    assert reason == ExceptionReason.TIMING_WINDOW


def test_classifies_missing_reference():
    ts = datetime(2026, 6, 1, 10, 0)
    target = _txn("upi_1", Source.UPI, 100000, ts, ref="")
    reason = classify_exception(target, [target])
    assert reason == ExceptionReason.MISSING_REFERENCE


def test_classifies_ambiguous_when_no_signals():
    ts = datetime(2026, 6, 1, 10, 0)
    target = _txn("upi_1", Source.UPI, 100000, ts, ref="xyz_unique")
    reason = classify_exception(target, [target])
    assert reason == ExceptionReason.AMBIGUOUS


def test_classify_batch_returns_records():
    ts = datetime(2026, 6, 1, 10, 0)
    txns = [
        _txn("a", Source.UPI, 100000, ts, ref="ord_1"),
        _txn("b", Source.UPI, 200000, ts, ref=""),
    ]
    records = classify_batch(txns)
    assert len(records) == 2
    assert all(r.txn_id for r in records)
    assert all(r.reason for r in records)


def test_classifier_priority_duplicate_first():
    """Duplicate signal takes precedence — but only when amounts actually match."""
    ts = datetime(2026, 6, 1, 10, 0)
    target = _txn("upi_1", Source.UPI, 100000, ts, ref="order_abc")
    # Another with same ref, SAME amount, 2h later — that's a duplicate
    dup = _txn("upi_2", Source.UPI, 100000, ts + timedelta(hours=2), ref="abc")
    reason = classify_exception(target, [target, dup])
    assert reason == ExceptionReason.DUPLICATE_FIRE


def test_classifier_amount_mismatch_wins_over_duplicate_when_amount_differs():
    """If amount differs, it's amount_mismatch, not duplicate (duplicate requires exact amount)."""
    ts = datetime(2026, 6, 1, 10, 0)
    target = _txn("upi_1", Source.UPI, 100000, ts, ref="order_abc")
    near_dup = _txn("upi_2", Source.UPI, 99900, ts + timedelta(hours=2), ref="abc")
    reason = classify_exception(target, [target, near_dup])
    assert reason == ExceptionReason.AMOUNT_MISMATCH