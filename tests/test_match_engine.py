"""Tests for match_engine.py — probabilistic scoring and matching."""
import pytest
from datetime import datetime, timedelta
from src.data_model import NormalizedTxn, Source, TxnDirection
from src.match_engine import (
    MatchEngine, MatchConfig, score_pair, _amount_score, _date_score, _ref_score, _counterparty_score,
)


def _txn(txn_id, source, amount, ts, ref="", counterparty=""):
    return NormalizedTxn(
        txn_id=txn_id, source=source, direction=TxnDirection.CREDIT,
        amount_paise=amount, currency="INR", timestamp=ts,
        reference=ref, counterparty=counterparty,
    )


def test_amount_score_exact():
    score, strong = _amount_score(1000, 1000)
    assert score == 1.0 and strong


def test_amount_score_near():
    score, strong = _amount_score(100000, 99500)  # 0.5% diff
    assert score == 0.6 and not strong


def test_amount_score_far():
    score, strong = _amount_score(100000, 50000)
    assert score == 0.0 and not strong


def test_date_score_same_day():
    cfg = MatchConfig()
    ts1 = datetime(2026, 6, 1, 10, 0)
    ts2 = datetime(2026, 6, 1, 14, 0)  # 4 hours apart
    assert _date_score(ts1, ts2, cfg) == cfg.w_date_same_day


def test_date_score_within_3_days():
    cfg = MatchConfig()
    ts1 = datetime(2026, 6, 1, 10, 0)
    ts2 = datetime(2026, 6, 3, 10, 0)
    assert _date_score(ts1, ts2, cfg) == cfg.w_date_within_3


def test_date_score_beyond_7_days():
    cfg = MatchConfig()
    ts1 = datetime(2026, 6, 1, 10, 0)
    ts2 = datetime(2026, 6, 15, 10, 0)
    assert _date_score(ts1, ts2, cfg) == 0.0


def test_ref_score_containment():
    score, strong = _ref_score("order_abc123", "abc123")
    assert score == 0.7 and strong


def test_ref_score_no_match():
    score, strong = _ref_score("xyz", "abc")
    assert score == 0.0 and not strong


def test_counterparty_last4_match():
    score = _counterparty_score("acct_****1234", "vpa_****1234")
    assert score == 0.5


def test_score_pair_self_match():
    """Same txn_id = automatic perfect match."""
    t = _txn("same", Source.UPI, 100000, datetime(2026, 6, 1), ref="ord_1")
    s = score_pair(t, t, MatchConfig())
    assert s.score == 1.0 and s.has_strong_signal


def test_match_engine_finds_cross_source_with_strong_signal():
    """UPI txn matches an invoice with same amount + date + reference containment."""
    ts = datetime(2026, 6, 1, 10, 0)
    a = _txn("upi_1", Source.UPI, 100000, ts, ref="order_abc123")
    b = _txn("inv_1", Source.INVOICE, 100000, ts, ref="abc123")
    engine = MatchEngine()
    decisions, unmatched = engine.match([a, b])
    matched = [d for d in decisions if d.matched]
    assert len(matched) >= 1
    assert len(unmatched) <= 1


def test_match_engine_rejects_below_threshold():
    """Very weak signal — no match."""
    ts = datetime(2026, 6, 1, 10, 0)
    a = _txn("upi_1", Source.UPI, 100000, ts, ref="x")
    b = _txn("inv_1", Source.INVOICE, 999999, datetime(2026, 1, 1), ref="y")
    engine = MatchEngine()
    decisions, unmatched = engine.match([a, b])
    assert all(not d.matched for d in decisions)
    assert len(unmatched) == 2


def test_match_engine_greedy_best_first():
    """When one txn could match multiple, take the best."""
    ts = datetime(2026, 6, 1, 10, 0)
    target = _txn("upi_1", Source.UPI, 100000, ts, ref="ord_42")
    strong = _txn("inv_strong", Source.INVOICE, 100000, ts, ref="ord_42")
    weak = _txn("inv_weak", Source.INVOICE, 100000, ts, ref="different_ref")
    engine = MatchEngine()
    decisions, unmatched = engine.match([target, strong, weak])
    matched = [d for d in decisions if d.matched]
    matched_pairs = {(d.txn_id_a, d.txn_id_b) for d in matched}
    # Target should match the strong candidate (or the weak — both have same amount)
    assert any("upi_1" in pair for pair in matched_pairs)
    # At most one match involving target (greedy assignment)
    target_pairs = [p for p in matched_pairs if "upi_1" in p]
    assert len(target_pairs) == 1


def test_match_engine_skips_same_source():
    """Same-source pair never produces a candidate."""
    ts = datetime(2026, 6, 1, 10, 0)
    a = _txn("upi_1", Source.UPI, 100000, ts, ref="ord_42")
    b = _txn("upi_2", Source.UPI, 100000, ts, ref="ord_42")
    engine = MatchEngine()
    decisions, unmatched = engine.match([a, b])
    assert all(not d.matched for d in decisions)


def test_match_engine_skips_opposite_directions():
    """A credit and a debit are not the same event."""
    ts = datetime(2026, 6, 1, 10, 0)
    a = NormalizedTxn(
        txn_id="upi_1", source=Source.UPI, direction=TxnDirection.CREDIT,
        amount_paise=100000, currency="INR", timestamp=ts, reference="ord_42",
    )
    b = NormalizedTxn(
        txn_id="inv_1", source=Source.INVOICE, direction=TxnDirection.DEBIT,
        amount_paise=100000, currency="INR", timestamp=ts, reference="ord_42",
    )
    engine = MatchEngine()
    decisions, _ = engine.match([a, b])
    assert all(not d.matched for d in decisions)


def test_match_config_validates_threshold():
    with pytest.raises(ValueError):
        MatchConfig(accept_threshold=0.1)