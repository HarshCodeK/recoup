"""Source-specific normalizers.

Each source has its own quirks. The normalizer's job is to strip those
quirks away and produce a NormalizedTxn. The downstream rule/match
engines never see the raw source shape — only the normalized one.
"""
from __future__ import annotations
from datetime import datetime
from typing import Any

from .data_model import NormalizedTxn, Source, TxnDirection


def _to_paise(amount: float | int) -> int:
    """Convert rupees (float or int) to integer paise safely.

    Convention: source payloads express amounts in RUPEES (Razorpay
    convention). The normalizer is the single point where we convert
    to integer paise for internal storage. This avoids float drift in
    every downstream comparison and computation.
    """
    # Always convert from rupees → paise. Avoid the "if int, already paise"
    # trap that silently double-counted in the first build.
    return int(round(float(amount) * 100))


def _parse_ts(value: Any) -> datetime:
    """Parse timestamp from common string formats; return UTC."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        # Unix epoch seconds
        from datetime import timezone
        return datetime.fromtimestamp(value, tz=timezone.utc).replace(tzinfo=None)
    s = str(value).strip()
    # Try ISO 8601 first
    try:
        return datetime.fromisoformat(s.replace("Z", ""))
    except ValueError:
        pass
    # Try common Razorpay formats
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unparseable timestamp: {value!r}")


def normalize_upi(payload: dict) -> NormalizedTxn:
    """Normalize a UPI transaction (Razorpay UPI intent/collect)."""
    return NormalizedTxn(
        txn_id=payload["txn_id"],
        source=Source.UPI,
        direction=TxnDirection.CREDIT if payload.get("type") == "credit" else TxnDirection.DEBIT,
        amount_paise=_to_paise(payload["amount"]),
        currency=payload.get("currency", "INR"),
        timestamp=_parse_ts(payload["timestamp"]),
        reference=payload.get("upi_txn_ref") or payload.get("reference"),
        counterparty=payload.get("vpa") or payload.get("counterparty"),
        raw_payload=dict(payload),
    )


def normalize_card(payload: dict) -> NormalizedTxn:
    """Normalize a Card transaction (Razorpay Card payment)."""
    return NormalizedTxn(
        txn_id=payload["txn_id"],
        source=Source.CARD,
        direction=TxnDirection.CREDIT if payload.get("status") == "captured" else TxnDirection.DEBIT,
        amount_paise=_to_paise(payload["amount"]),
        currency=payload.get("currency", "INR"),
        timestamp=_parse_ts(payload["timestamp"]),
        reference=payload.get("card_id") or payload.get("reference"),
        counterparty=payload.get("card_last4") and f"card_****{payload['card_last4']}",
        raw_payload=dict(payload),
    )


def normalize_bank_transfer(payload: dict) -> NormalizedTxn:
    """Normalize a Bank Transfer (Razorpay Smart Collect / NEFT/RTGS/IMPS)."""
    return NormalizedTxn(
        txn_id=payload["txn_id"],
        source=Source.BANK_TRANSFER,
        direction=TxnDirection.CREDIT,
        amount_paise=_to_paise(payload["amount"]),
        currency=payload.get("currency", "INR"),
        timestamp=_parse_ts(payload["timestamp"]),
        reference=payload.get("utr") or payload.get("reference"),
        counterparty=payload.get("remitter_account_last4") and f"acct_****{payload['remitter_account_last4']}",
        raw_payload=dict(payload),
    )


def normalize_subscription(payload: dict) -> NormalizedTxn:
    """Normalize a Subscription charge (Razorpay Subscriptions)."""
    return NormalizedTxn(
        txn_id=payload["txn_id"],
        source=Source.SUBSCRIPTION,
        direction=TxnDirection.CREDIT,
        amount_paise=_to_paise(payload["amount"]),
        currency=payload.get("currency", "INR"),
        timestamp=_parse_ts(payload["timestamp"]),
        reference=payload.get("subscription_id") or payload.get("reference"),
        counterparty=payload.get("customer_id"),
        raw_payload=dict(payload),
    )


def normalize_invoice(payload: dict) -> NormalizedTxn:
    """Normalize an Invoice record (Razorpay Invoices)."""
    return NormalizedTxn(
        txn_id=payload["txn_id"],
        source=Source.INVOICE,
        direction=TxnDirection.CREDIT,  # invoices represent expected credit
        amount_paise=_to_paise(payload["amount"]),
        currency=payload.get("currency", "INR"),
        timestamp=_parse_ts(payload["timestamp"]),
        reference=payload.get("invoice_number") or payload.get("reference"),
        counterparty=payload.get("customer_id"),
        raw_payload=dict(payload),
    )


NORMALIZERS = {
    Source.UPI: normalize_upi,
    Source.CARD: normalize_card,
    Source.BANK_TRANSFER: normalize_bank_transfer,
    Source.SUBSCRIPTION: normalize_subscription,
    Source.INVOICE: normalize_invoice,
}


def normalize(source: Source, payload: dict) -> NormalizedTxn:
    """Dispatch to the right normalizer for the given source."""
    return NORMALIZERS[source](payload)