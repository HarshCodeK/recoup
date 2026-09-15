"""Tests for normalizer.py — verify each source normalizes correctly."""
import pytest
from datetime import datetime
from src.normalizer import (
    normalize, normalize_upi, normalize_card,
    normalize_bank_transfer, normalize_subscription, normalize_invoice,
)
from src.data_model import Source, TxnDirection


def test_normalize_upi_credit():
    payload = {
        "txn_id": "u1",
        "type": "credit",
        "amount": 1500.50,
        "timestamp": "2026-06-01T10:30:00",
        "upi_txn_ref": "upi_abc123",
        "vpa": "alice@upi",
    }
    t = normalize_upi(payload)
    assert t.txn_id == "u1"
    assert t.source == Source.UPI
    assert t.direction == TxnDirection.CREDIT
    assert t.amount_paise == 150050   # rupees * 100, no float drift
    assert t.reference == "upi_abc123"
    assert t.counterparty == "alice@upi"


def test_normalize_card_captured():
    payload = {
        "txn_id": "c1",
        "status": "captured",
        "amount": 2500,
        "timestamp": "2026-06-01T11:00:00",
        "card_id": "card_9876",
        "card_last4": "4242",
    }
    t = normalize_card(payload)
    assert t.source == Source.CARD
    assert t.direction == TxnDirection.CREDIT
    assert t.amount_paise == 250000
    assert t.counterparty == "card_****4242"   # masked


def test_normalize_bank_transfer():
    payload = {
        "txn_id": "b1",
        "amount": 50000,
        "timestamp": "2026-06-01T12:00:00",
        "utr": "utr_xyz789",
        "remitter_account_last4": "1234",
    }
    t = normalize_bank_transfer(payload)
    assert t.source == Source.BANK_TRANSFER
    assert t.amount_paise == 5000000
    assert t.reference == "utr_xyz789"
    assert t.counterparty == "acct_****1234"


def test_normalize_subscription():
    payload = {
        "txn_id": "s1",
        "amount": 999,
        "timestamp": "2026-06-01T13:00:00",
        "subscription_id": "SUB12345",
        "customer_id": "cust_0042",
    }
    t = normalize_subscription(payload)
    assert t.source == Source.SUBSCRIPTION
    assert t.reference == "SUB12345"
    assert t.counterparty == "cust_0042"


def test_normalize_invoice():
    payload = {
        "txn_id": "i1",
        "amount": 7500,
        "timestamp": "2026-06-01T14:00:00",
        "invoice_number": "INV00100",
        "customer_id": "cust_0099",
    }
    t = normalize_invoice(payload)
    assert t.source == Source.INVOICE
    assert t.reference == "INV00100"


def test_normalize_dispatch():
    """The normalize(source, payload) dispatch routes correctly."""
    payload = {"txn_id": "u1", "type": "credit", "amount": 100, "timestamp": "2026-06-01"}
    t = normalize(Source.UPI, payload)
    assert t.source == Source.UPI


def test_amount_handling_no_float_drift():
    """100.05 rupees must become 10005 paise, not 10004.99999."""
    payload = {"txn_id": "u1", "type": "credit", "amount": 100.05, "timestamp": "2026-06-01"}
    t = normalize_upi(payload)
    assert t.amount_paise == 10005


def test_timestamp_unix_epoch():
    payload = {"txn_id": "u1", "type": "credit", "amount": 100, "timestamp": 1717200000}
    t = normalize_upi(payload)
    assert isinstance(t.timestamp, datetime)


def test_unparseable_timestamp_raises():
    with pytest.raises(ValueError):
        normalize_upi({"txn_id": "u1", "type": "credit", "amount": 100, "timestamp": "not-a-date"})