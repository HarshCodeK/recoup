"""Deterministic rule engine for transaction matching.

This runs FIRST, before any LLM or fuzzy matching. The rule engine
returns high-confidence matches (~70-80% of typical batches) without
touching the LLM. It is the safety spine: deterministic, auditable,
testable.

Rules (in order):
1. R1_EXACT_ID:       same txn_id                       → match
2. R2_REFERENCE:      same reference + same amount      → match
3. R3_AMOUNT_PARTY:   same amount + same day + same counterparty → match
4. R4_INVOICE_LINK:   invoice's INV-ref + matching amount within 3 days → match
                       (handles card/UPI payments against issued invoices)
5. NO_MATCH:          none of the above                  → fallback to match_engine
"""
from __future__ import annotations
from collections import defaultdict
from datetime import timedelta
from typing import Iterable

from src.data_model import NormalizedTxn, MatchDecision, MatchSource, Source, TxnDirection


def _same_day(a, b) -> bool:
    return a.date() == b.date()


def _different_sources(a: NormalizedTxn, b: NormalizedTxn) -> bool:
    return a.source != b.source


def _same_direction(a: NormalizedTxn, b: NormalizedTxn) -> bool:
    return a.direction == b.direction


class RuleEngine:
    """Pure deterministic match logic. Stateless. Reusable."""

    def match(
        self,
        txns: list[NormalizedTxn],
    ) -> tuple[list[MatchDecision], list[NormalizedTxn]]:
        """Run rule matching on a batch.

        Returns:
            (decisions, unmatched) — unmatched go to the probabilistic
            match engine next.
        """
        by_id: dict[str, list[NormalizedTxn]] = defaultdict(list)
        by_ref: dict[str, list[NormalizedTxn]] = defaultdict(list)
        by_amount_day_party: dict[tuple, list[NormalizedTxn]] = defaultdict(list)

        for t in txns:
            by_id[t.txn_id].append(t)
            if t.reference:
                by_ref[t.reference].append(t)
            if t.counterparty:
                key = (t.amount_paise, t.timestamp.date(), t.counterparty)
                by_amount_day_party[key].append(t)

        decisions: list[MatchDecision] = []
        matched_ids: set[str] = set()

        # Rule 1: exact txn_id (cross-source if same id appears twice)
        for tid, group in by_id.items():
            if len(group) >= 2:
                # Only match across different sources
                cross_source_pairs = []
                for i in range(len(group)):
                    for j in range(i + 1, len(group)):
                        a, b = group[i], group[j]
                        if _different_sources(a, b) and _same_direction(a, b):
                            cross_source_pairs.append((a, b))
                for a, b in cross_source_pairs:
                    decisions.append(MatchDecision(
                        txn_id_a=a.txn_id,
                        txn_id_b=b.txn_id,
                        matched=True,
                        source=MatchSource.RULE,
                        reasons=["R1:exact_id"],
                        score=1.0,
                    ))
                    matched_ids.update([a.txn_id, b.txn_id])

        # Rule 2: same reference + same amount
        for ref, group in by_ref.items():
            if len(group) >= 2:
                by_amount: dict[int, list[NormalizedTxn]] = defaultdict(list)
                for t in group:
                    by_amount[t.amount_paise].append(t)
                for amount, amt_group in by_amount.items():
                    if len(amt_group) >= 2:
                        # Cross-source only
                        for i in range(len(amt_group)):
                            for j in range(i + 1, len(amt_group)):
                                a, b = amt_group[i], amt_group[j]
                                if not _different_sources(a, b) or not _same_direction(a, b):
                                    continue
                                if a.txn_id in matched_ids or b.txn_id in matched_ids:
                                    continue
                                decisions.append(MatchDecision(
                                    txn_id_a=a.txn_id,
                                    txn_id_b=b.txn_id,
                                    matched=True,
                                    source=MatchSource.RULE,
                                    reasons=["R2:reference+amount"],
                                    score=0.95,
                                ))
                                matched_ids.update([a.txn_id, b.txn_id])

        # Rule 3: same amount + same day + same counterparty
        for key, group in by_amount_day_party.items():
            if len(group) >= 2:
                cross_pairs = [
                    (a, b) for i, a in enumerate(group)
                    for b in group[i + 1:]
                    if _different_sources(a, b) and _same_direction(a, b)
                ]
                for a, b in cross_pairs:
                    if a.txn_id in matched_ids or b.txn_id in matched_ids:
                        continue
                    decisions.append(MatchDecision(
                        txn_id_a=a.txn_id,
                        txn_id_b=b.txn_id,
                        matched=True,
                        source=MatchSource.RULE,
                        reasons=["R3:amount+date+counterparty"],
                        score=0.85,
                    ))
                    matched_ids.update([a.txn_id, b.txn_id])

        unmatched = [t for t in txns if t.txn_id not in matched_ids]

        # Rule 4: invoice ref + matching amount within 3 days
        # Handles the case where a card/UPI payment settles an invoice
        # whose INV-XXXXX reference is only on the invoice side.
        unmatched_with_invoice_ref = [
            t for t in unmatched
            if t.source == Source.INVOICE and t.reference and t.reference.startswith("INV")
        ]
        for inv in unmatched_with_invoice_ref:
            for other in unmatched:
                if other.source == Source.INVOICE:
                    continue
                if other.txn_id in matched_ids:
                    continue
                if other.amount_paise != inv.amount_paise:
                    continue
                # Same currency implicitly (we only deal in INR)
                delta = abs((other.timestamp - inv.timestamp).total_seconds())
                if delta > 3 * 86400:
                    continue
                decisions.append(MatchDecision(
                    txn_id_a=other.txn_id,
                    txn_id_b=inv.txn_id,
                    matched=True,
                    source=MatchSource.RULE,
                    reasons=["R4:invoice_link"],
                    score=0.90,
                ))
                matched_ids.update([other.txn_id, inv.txn_id])

        unmatched = [t for t in txns if t.txn_id not in matched_ids]
        return decisions, unmatched

    def exact_match_by_id(
        self,
        txns: list[NormalizedTxn],
        target_id: str,
    ) -> list[NormalizedTxn]:
        """Find all transactions with the given txn_id."""
        return [t for t in txns if t.txn_id == target_id]

    def find_by_reference(
        self,
        txns: list[NormalizedTxn],
        reference: str,
    ) -> list[NormalizedTxn]:
        """Find all transactions with the given external reference."""
        return [t for t in txns if t.reference == reference]