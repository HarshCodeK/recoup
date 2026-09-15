"""Exception classifier.

After both rule engine and probabilistic match engine run, anything
left over is an exception. This module classifies *why* each one
couldn't be matched, using deterministic signals.

The classification is conservative — when uncertain, it returns
AMBIGUOUS and routes to LLM diagnosis (which itself can refuse).
We never guess a financial reason.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable

from src.data_model import (
    NormalizedTxn, ExceptionReason, ExceptionRecord,
    Source,
)


# Heuristics — ordered from most-specific to most-general.
# Each function returns the reason if it matches, else None.
# The first non-None result wins.


def _has_close_match_but_amount_differs(
    txn: NormalizedTxn,
    candidates: list[NormalizedTxn],
    amount_tolerance_pct: float = 0.02,
) -> bool:
    """There's a near-match on reference/date but amount differs by >2%."""
    if not txn.reference:
        return False
    ref = txn.reference.lower()
    for other in candidates:
        if other.txn_id == txn.txn_id:
            continue
        other_ref = (other.reference or "").lower()
        if ref not in other_ref and other_ref not in ref:
            continue
        # Date within 3 days
        if abs((txn.timestamp - other.timestamp).total_seconds()) > 3 * 86400:
            continue
        # Amount differs by more than tolerance
        if txn.amount_paise == 0 or other.amount_paise == 0:
            continue
        diff_pct = abs(txn.amount_paise - other.amount_paise) / max(txn.amount_paise, other.amount_paise)
        if diff_pct > amount_tolerance_pct:
            return True
    return False


def _has_duplicate_within_window(
    txn: NormalizedTxn,
    candidates: list[NormalizedTxn],
    window_hours: int = 24,
) -> bool:
    """Another transaction with same amount + reference fired within window."""
    if not txn.reference:
        return False
    ref = txn.reference.lower()
    cutoff = txn.timestamp + timedelta(hours=window_hours)
    for other in candidates:
        if other.txn_id == txn.txn_id:
            continue
        if other.timestamp > cutoff and other.timestamp >= txn.timestamp:
            continue
        if other.amount_paise != txn.amount_paise:
            continue
        other_ref = (other.reference or "").lower()
        if ref in other_ref or other_ref in ref:
            return True
    return False


def _has_timing_drift(
    txn: NormalizedTxn,
    candidates: list[NormalizedTxn],
    drift_days: int = 7,
) -> bool:
    """A matching reference exists but timestamp differs by >7 days."""
    if not txn.reference:
        return False
    ref = txn.reference.lower()
    for other in candidates:
        if other.txn_id == txn.txn_id:
            continue
        other_ref = (other.reference or "").lower()
        if ref not in other_ref and other_ref not in ref:
            continue
        if txn.amount_paise != other.amount_paise:
            continue
        delta_days = abs((txn.timestamp - other.timestamp).total_seconds()) / 86400
        if drift_days < delta_days <= 30:
            return True
    return False


def _missing_reference(txn: NormalizedTxn) -> bool:
    """No reference at all — can't be cross-referenced."""
    return not txn.reference or not txn.reference.strip()


def _has_any_partial_signal(
    txn: NormalizedTxn,
    candidates: list[NormalizedTxn],
) -> bool:
    """Some matching reference exists somewhere, just not strongly enough to match."""
    if not txn.reference:
        return False
    ref = txn.reference.lower()
    for other in candidates:
        if other.txn_id == txn.txn_id:
            continue
        other_ref = (other.reference or "").lower()
        if ref in other_ref or other_ref in ref:
            return True
    return False


def classify_exception(
    txn: NormalizedTxn,
    pool: Iterable[NormalizedTxn],
) -> ExceptionReason:
    """Classify one transaction against the full pool.

    Deterministic and order-stable. Returns one of the canonical
    ExceptionReason values. AMBIGUOUS is the default fallback when
    no specific signal fires.
    """
    candidates = list(pool)

    # Standalone recurring charges (subscriptions) don't need cross-source
    # matches — they ARE the source of truth.
    if txn.source == Source.SUBSCRIPTION:
        return ExceptionReason.STANDALONE_RECURRING

    # Most specific first
    if _has_duplicate_within_window(txn, candidates):
        return ExceptionReason.DUPLICATE_FIRE
    if _has_close_match_but_amount_differs(txn, candidates):
        return ExceptionReason.AMOUNT_MISMATCH
    if _has_timing_drift(txn, candidates):
        return ExceptionReason.TIMING_WINDOW
    if _missing_reference(txn):
        return ExceptionReason.MISSING_REFERENCE
    if _has_any_partial_signal(txn, candidates):
        return ExceptionReason.AMOUNT_MISMATCH  # partial signal without clean match
    return ExceptionReason.AMBIGUOUS


def classify_batch(
    unmatched: list[NormalizedTxn],
    pool: list[NormalizedTxn] | None = None,
) -> list[ExceptionRecord]:
    """Classify a batch of unmatched transactions.

    Each exception is tagged with a reason. The pool used for
    classification defaults to the unmatched batch itself (intra-batch
    signals), but can be widened to include matched transactions too.
    """
    if pool is None:
        pool = unmatched
    records = []
    for txn in unmatched:
        reason = classify_exception(txn, pool)
        records.append(ExceptionRecord(
            txn_id=txn.txn_id,
            source=txn.source,
            amount_paise=txn.amount_paise,
            reason=reason,
            reference=txn.reference,
            timestamp=txn.timestamp,
        ))
    return records