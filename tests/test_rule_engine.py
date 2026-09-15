"""Tests for rule_engine.py — deterministic matching."""
import pytest
from datetime import datetime
from src.rule_engine import RuleEngine
from src.data_model import NormalizedTxn, Source, TxnDirection, MatchSource


def _txn(txn_id, source=Source.UPI, amount=100000, ts=None, ref="", counterparty=""):
    return NormalizedTxn(
        txn_id=txn_id, source=source, direction=TxnDirection.CREDIT,
        amount_paise=amount, currency="INR",
        timestamp=ts or datetime(2026, 6, 1, 10, 0),
        reference=ref or None, counterparty=counterparty or None,
    )


def test_rule1_exact_id_match_cross_source():
    """Same txn_id appearing in two different sources = match."""
    a = _txn("abc123", source=Source.UPI)
    b = _txn("abc123", source=Source.INVOICE)
    engine = RuleEngine()
    decisions, _ = engine.match([a, b])
    matched = [d for d in decisions if d.matched]
    assert len(matched) == 1
    assert "R1:exact_id" in matched[0].reasons


def test_rule1_same_source_no_match():
    """Same txn_id in same source is not a meaningful match."""
    a = _txn("abc123", source=Source.UPI)
    b = _txn("abc123", source=Source.UPI)
    engine = RuleEngine()
    decisions, _ = engine.match([a, b])
    matched = [d for d in decisions if d.matched]
    assert matched == []


def test_rule2_reference_and_amount_match():
    ts = datetime(2026, 6, 1, 10, 0)
    a = _txn("upi_1", source=Source.UPI, ref="order_abc", counterparty="vpa_****1111")
    b = _txn("inv_1", source=Source.INVOICE, ref="order_abc", counterparty="cust_****1111")
    engine = RuleEngine()
    decisions, _ = engine.match([a, b])
    matched = [d for d in decisions if d.matched]
    assert len(matched) == 1
    assert "R2:reference+amount" in matched[0].reasons


def test_rule2_reference_match_but_amount_differs_no_match():
    ts = datetime(2026, 6, 1, 10, 0)
    a = _txn("upi_1", source=Source.UPI, amount=100000, ref="ord_42")
    b = _txn("inv_1", source=Source.INVOICE, amount=95000, ref="ord_42")
    engine = RuleEngine()
    decisions, _ = engine.match([a, b])
    matched = [d for d in decisions if d.matched]
    assert matched == []


def test_rule3_amount_date_counterparty_match():
    ts = datetime(2026, 6, 1, 10, 0)
    a = _txn("upi_1", source=Source.UPI, ref=None, counterparty="vpa_****1111")
    b = _txn("inv_1", source=Source.INVOICE, ref=None, counterparty="vpa_****1111")
    engine = RuleEngine()
    decisions, _ = engine.match([a, b])
    matched = [d for d in decisions if d.matched]
    assert len(matched) == 1
    assert "R3:amount+date+counterparty" in matched[0].reasons


def test_opposite_directions_do_not_match():
    """A credit and a debit are not the same payment event."""
    ts = datetime(2026, 6, 1, 10, 0)
    a = NormalizedTxn(txn_id="t1", source=Source.UPI, direction=TxnDirection.CREDIT,
                       amount_paise=100000, currency="INR", timestamp=ts,
                       reference="ord_1", counterparty="vpa_****1111")
    b = NormalizedTxn(txn_id="t2", source=Source.INVOICE, direction=TxnDirection.DEBIT,
                       amount_paise=100000, currency="INR", timestamp=ts,
                       reference="ord_1", counterparty="vpa_****1111")
    engine = RuleEngine()
    decisions, _ = engine.match([a, b])
    matched = [d for d in decisions if d.matched]
    assert matched == []


def test_no_match_returns_unmatched():
    a = _txn("upi_1", source=Source.UPI, ref="x")
    b = _txn("inv_1", source=Source.INVOICE, ref="y")
    engine = RuleEngine()
    decisions, unmatched = engine.match([a, b])
    assert all(not d.matched for d in decisions)
    assert len(unmatched) == 2


def test_unmatched_returned_separately():
    ts = datetime(2026, 6, 1, 10, 0)
    matched_a = _txn("upi_1", source=Source.UPI, ref="ord_42")
    matched_b = _txn("inv_1", source=Source.INVOICE, ref="ord_42")
    orphan = _txn("orphan_1", source=Source.UPI, ref="ord_unique")
    engine = RuleEngine()
    decisions, unmatched = engine.match([matched_a, matched_b, orphan])
    matched_ids = set()
    for d in decisions:
        if d.matched:
            matched_ids.add(d.txn_id_a)
            matched_ids.add(d.txn_id_b)
    assert "orphan_1" in {t.txn_id for t in unmatched}
    assert matched_ids == {"upi_1", "inv_1"}


def test_rule_engine_is_deterministic():
    ts = datetime(2026, 6, 1, 10, 0)
    txns = [
        _txn("a", source=Source.UPI, ref="ord_1", counterparty="vpa_****1111"),
        _txn("b", source=Source.INVOICE, ref="ord_1", counterparty="vpa_****1111"),
    ]
    e = RuleEngine()
    d1, u1 = e.match(txns)
    d2, u2 = e.match(txns)
    assert len(d1) == len(d2)
    assert len(u1) == len(u2)
    assert [d.matched for d in d1] == [d.matched for d in d2]