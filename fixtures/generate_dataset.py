"""Deterministic synthetic dataset generator.

Generates realistic Razorpay-shaped transactions across5 sources with
known ground truth. The seed (default 42) makes every run identical,
which is essential for the reproducible evaluation bar.

Outputs: (transactions, ground_truth_pairs) where ground_truth_pairs
list which transactions SHOULD match.
"""
from __future__ import annotations
import random
from datetime import datetime, timedelta
from typing import Any

from src.data_model import NormalizedTxn, Source, TxnDirection
from src.normalizer import (
    normalize_upi, normalize_card, normalize_bank_transfer,
    normalize_subscription, normalize_invoice,
)


# Realistic Razorpay amount ranges (in rupees), per source
AMOUNT_RANGES = {
    Source.UPI: (50_00, 50_000_00),         # ₹50 to ₹50,000
    Source.CARD: (100_00, 200_000_00),       # ₹100 to ₹2,00,000
    Source.BANK_TRANSFER: (1000_00, 1_000_000_00),  # ₹1,000 to ₹10,00,000
    Source.SUBSCRIPTION: (99_00, 9_999_00), # ₹99 to ₹9,999
    Source.INVOICE: (500_00, 100_000_00),   # ₹500 to ₹1,00,000
}

INVOICE_REFERENCE_PREFIX = "INV"
SUBSCRIPTION_PREFIX = "SUB"


def _paise_to_rupees(p: int) -> float:
    return p / 100.0


def _random_amount(rng: random.Random, source: Source) -> int:
    low_paise, high_paise = AMOUNT_RANGES[source]
    # Pick from common "round" amounts + some noise
    rupee_options = [99, 199, 299, 499, 999, 1499, 1999, 4999, 9999, 19999, 49999, 99999]
    if rng.random() < 0.4:
        rupees = rng.choice(rupee_options)
        return int(rupees * 100)
    low_rupees = low_paise // 100
    high_rupees = high_paise // 100
    rupees = rng.randint(low_rupees, high_rupees)
    return rupees * 100


def _random_timestamp(rng: random.Random, base: datetime) -> datetime:
    delta = timedelta(
        days=rng.randint(0, 89),
        hours=rng.randint(0, 23),
        minutes=rng.randint(0, 59),
        seconds=rng.randint(0, 59),
    )
    return base + delta


def generate(
    total_transactions: int = 3000,
    seed: int = 42,
    exception_rate: float = 0.13,
) -> tuple[list[NormalizedTxn], list[tuple[str, str, str]]]:
    """Generate a synthetic dataset with known ground truth.

    Args:
        total_transactions: total records to generate
        seed: random seed (deterministic)
        exception_rate: fraction that should be intentionally un-matchable
            or exception-inducing

    Returns:
        (transactions, ground_truth_pairs)
        ground_truth_pairs: list of (txn_a_id, txn_b_id, expected_outcome)
            where expected_outcome ∈ {"match", "exception:amount_mismatch",
            "exception:missing_reference", "exception:duplicate_fire",
            "exception:timing_window", "exception:missing_txn",
            "exception:ambiguous"}
    """
    rng = random.Random(seed)
    base_time = datetime(2026, 6, 1, 0, 0, 0)

    transactions: list[NormalizedTxn] = []
    ground_truth: list[tuple[str, str, str]] = []

    # Source distribution: realistic for Indian D2C merchants
    source_dist = [
        (Source.UPI, 0.35),
        (Source.CARD, 0.25),
        (Source.BANK_TRANSFER, 0.10),
        (Source.SUBSCRIPTION, 0.20),
        (Source.INVOICE, 0.10),
    ]

    def pick_source() -> Source:
        r = rng.random()
        cumulative = 0.0
        for s, p in source_dist:
            cumulative += p
            if r <= cumulative:
                return s
        return Source.UPI

    # Phase 1: generate "legitimate" transactions that should match
    # Strategy: ~half the txns are "real payments" (UPI/Card/Bank),
    # half are "expected receipts" (Invoice) or "recurring" (Subscription).
    # Cross-source matches: invoice paid via UPI/Card → match pair.

    # Generate invoice records first (these are the "anchor" records)
    num_invoices = int(total_transactions * 0.20)
    invoice_records: list[NormalizedTxn] = []
    for i in range(num_invoices):
        inv_num = f"{INVOICE_REFERENCE_PREFIX}{1000 + i:05d}"
        payload = {
            "txn_id": f"inv_{i:05d}",
            "amount": _paise_to_rupees(_random_amount(rng, Source.INVOICE)),
            "currency": "INR",
            "timestamp": _random_timestamp(rng, base_time).isoformat(),
            "invoice_number": inv_num,
            "customer_id": f"cust_{rng.randint(1, 500):04d}",
        }
        t = normalize_invoice(payload)
        invoice_records.append(t)
        transactions.append(t)

    # Generate subscription records (recurring, no cross-source match needed)
    num_subs = int(total_transactions * 0.20)
    for i in range(num_subs):
        sub_id = f"{SUBSCRIPTION_PREFIX}{2000 + i:05d}"
        payload = {
            "txn_id": f"sub_{i:05d}",
            "amount": _paise_to_rupees(_random_amount(rng, Source.SUBSCRIPTION)),
            "currency": "INR",
            "timestamp": _random_timestamp(rng, base_time).isoformat(),
            "subscription_id": sub_id,
            "customer_id": f"cust_{rng.randint(1, 500):04d}",
        }
        t = normalize_subscription(payload)
        transactions.append(t)

    # Generate UPI/Card/Bank records, with intentional cross-source matches
    # against some invoices (60% of invoices get paid via UPI/Card)
    num_payment_records = total_transactions - len(transactions)

    for i in range(num_payment_records):
        source = pick_source()
        # 60% chance: this payment matches an existing invoice
        if rng.random() < 0.60 and invoice_records:
            invoice = rng.choice(invoice_records)
            # Build a payment that matches via R2 (reference+amount)
            # or R4 (invoice link). Both ref and same-amount-date.
            payload: dict[str, Any] = {
                "txn_id": f"pay_{i:05d}",
                "amount": _paise_to_rupees(invoice.amount_paise),
                "currency": "INR",
                "timestamp": invoice.timestamp.isoformat(),
                "customer_id": invoice.counterparty,
            }
            if source == Source.UPI:
                payload["upi_txn_ref"] = invoice.reference
                payload["type"] = "credit"
                payload["vpa"] = f"user{rng.randint(1, 1000)}@upi"
                t = normalize_upi(payload)
            elif source == Source.CARD:
                payload["status"] = "captured"
                payload["card_last4"] = f"{rng.randint(1000, 9999)}"
                # Cards don't carry invoice ref — match via R4 (amount within 3 days)
                t = normalize_card(payload)
            else:
                payload["utr"] = invoice.reference
                t = normalize_bank_transfer(payload)
            ground_truth.append((t.txn_id, invoice.txn_id, "match"))
            transactions.append(t)
            invoice_records.remove(invoice)  # consumed
            continue

        # Otherwise: standalone payment record (no match expected)
        amount = _random_amount(rng, source)
        ts = _random_timestamp(rng, base_time)
        if source == Source.UPI:
            payload = {
                "txn_id": f"upi_{i:05d}",
                "amount": _paise_to_rupees(amount),
                "currency": "INR",
                "timestamp": ts.isoformat(),
                "upi_txn_ref": f"upi_ref_{rng.randint(100000, 999999)}",
                "vpa": f"user{rng.randint(1, 1000)}@upi",
                "type": "credit",
            }
            t = normalize_upi(payload)
        elif source == Source.CARD:
            payload = {
                "txn_id": f"card_{i:05d}",
                "amount": _paise_to_rupees(amount),
                "currency": "INR",
                "timestamp": ts.isoformat(),
                "card_id": f"card_{rng.randint(10000, 99999)}",
                "card_last4": f"{rng.randint(1000, 9999)}",
                "status": "captured",
            }
            t = normalize_card(payload)
        else:
            payload = {
                "txn_id": f"bank_{i:05d}",
                "amount": _paise_to_rupees(amount),
                "currency": "INR",
                "timestamp": ts.isoformat(),
                "utr": f"utr_{rng.randint(100000, 999999)}",
                "remitter_account_last4": f"{rng.randint(1000, 9999)}",
            }
            t = normalize_bank_transfer(payload)
        transactions.append(t)

    # Phase 2: inject intentional exceptions (~exception_rate of total)
    num_exceptions = int(len(transactions) * exception_rate)
    exceptions_to_inject = min(num_exceptions, len(transactions) // 3)

    exception_kinds = [
        ("amount_mismatch", 0.25),
        ("missing_reference", 0.20),
        ("duplicate_fire", 0.15),
        ("timing_window", 0.15),
        ("missing_txn", 0.10),
        ("ambiguous", 0.15),
    ]

    for _ in range(exceptions_to_inject):
        r = rng.random()
        cumulative = 0.0
        chosen_kind = "amount_mismatch"
        for kind, p in exception_kinds:
            cumulative += p
            if r <= cumulative:
                chosen_kind = kind
                break

        if not transactions:
            break

        target = rng.choice(transactions)

        if chosen_kind == "amount_mismatch":
            # Create a duplicate of target with a different amount
            new_payload = dict(target.raw_payload)
            new_payload["txn_id"] = f"{target.txn_id}_amtmm"
            new_payload["amount"] = _paise_to_rupees(target.amount_paise) + rng.choice([-50, 50, -100, 100])
            try:
                from .normalizer import normalize
                t2 = normalize(target.source, new_payload)
                transactions.append(t2)
                ground_truth.append((target.txn_id, t2.txn_id, "exception:amount_mismatch"))
            except Exception:
                pass

        elif chosen_kind == "missing_reference":
            # Strip reference from a copy
            new_payload = dict(target.raw_payload)
            new_payload["txn_id"] = f"{target.txn_id}_noref"
            for key in ["upi_txn_ref", "reference", "invoice_number", "subscription_id", "card_id", "utr"]:
                new_payload.pop(key, None)
            try:
                from .normalizer import normalize
                t2 = normalize(target.source, new_payload)
                transactions.append(t2)
                ground_truth.append((target.txn_id, t2.txn_id, "exception:missing_reference"))
            except Exception:
                pass

        elif chosen_kind == "duplicate_fire":
            # Same txn_id and amount — but rule engine catches this as exact match
            # Instead, we mark this as expected exception:duplicate_fire
            new_payload = dict(target.raw_payload)
            new_payload["txn_id"] = f"{target.txn_id}_dup"
            try:
                from .normalizer import normalize
                t2 = normalize(target.source, new_payload)
                transactions.append(t2)
                ground_truth.append((target.txn_id, t2.txn_id, "exception:duplicate_fire"))
            except Exception:
                pass

        elif chosen_kind == "timing_window":
            # Same ref + amount but 5+ days apart
            new_payload = dict(target.raw_payload)
            new_payload["txn_id"] = f"{target.txn_id}_late"
            old_ts = target.timestamp
            new_ts = old_ts + timedelta(days=rng.randint(5, 15))
            new_payload["timestamp"] = new_ts.isoformat()
            try:
                from .normalizer import normalize
                t2 = normalize(target.source, new_payload)
                transactions.append(t2)
                ground_truth.append((target.txn_id, t2.txn_id, "exception:timing_window"))
            except Exception:
                pass

        elif chosen_kind == "ambiguous":
            # Multiple plausible candidates — record only that this is ambiguous
            new_payload = dict(target.raw_payload)
            new_payload["txn_id"] = f"{target.txn_id}_amb"
            new_payload["amount"] = _paise_to_rupees(target.amount_paise + rng.randint(-500, 500) * 100)
            new_payload["timestamp"] = (target.timestamp + timedelta(days=rng.randint(2, 8))).isoformat()
            try:
                from .normalizer import normalize
                t2 = normalize(target.source, new_payload)
                transactions.append(t2)
                ground_truth.append((target.txn_id, t2.txn_id, "exception:ambiguous"))
            except Exception:
                pass

        elif chosen_kind == "missing_txn":
            # Standalone transaction with no counterpart — mark as exception:missing_txn
            ground_truth.append((target.txn_id, target.txn_id, "exception:missing_txn"))

    return transactions, ground_truth


if __name__ == "__main__":
    txns, gt = generate(3000, seed=42)
    print(f"Generated {len(txns)} transactions, {len(gt)} ground-truth pairs")
    from collections import Counter
    print("Outcome distribution:", Counter(g[2] for g in gt))