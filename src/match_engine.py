"""Probabilistic match engine.

Day 1's rule engine fires on exact-match signals. Anything outside
those signals falls through to here. This engine scores candidates
and ranks them; only matches above a confidence threshold count.

Scoring dimensions:
  - Amount agreement (exact / near-exact / none)
  - Date proximity (same day / ±3 days / ±7 days / none)
  - Reference containment (one ref contains the other)
  - Counterparty agreement (last4 / VPA / account overlap)

This is NOT fuzzy string matching. It's feature-scored ranking with
a hard threshold. If the threshold isn't cleared, the transaction
goes to the exception queue, not to a guessed match.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from itertools import product
from typing import Iterable

from src.data_model import (
    NormalizedTxn, MatchDecision, Source, TxnDirection, MatchSource,
)


@dataclass(frozen=True)
class MatchConfig:
    """Scoring weights and acceptance thresholds.

    These are constants, not config-file values. Changing them should
    require re-running the eval suite and inspecting the confusion
    matrix. Don't drift.
    """
    # Weights (sum to ~1.0 in nominal scoring)
    w_amount_exact: float = 0.45
    w_amount_near: float = 0.25
    w_date_same_day: float = 0.20
    w_date_within_3: float = 0.10
    w_date_within_7: float = 0.05
    w_ref_containment: float = 0.15
    w_counterparty: float = 0.10

    # Acceptance: minimum score to be considered a match
    accept_threshold: float = 0.55
    # And must have at least one STRONG signal (amount exact OR ref containment)
    require_strong: bool = True

    def __post_init__(self):
        if self.accept_threshold < 0.3 or self.accept_threshold > 0.9:
            raise ValueError("accept_threshold out of plausible range")


@dataclass
class ScoredCandidate:
    txn_a: NormalizedTxn
    txn_b: NormalizedTxn
    score: float
    has_strong_signal: bool
    score_breakdown: dict = field(default_factory=dict)


def _amount_score(a: int, b: int) -> tuple[float, bool]:
    """Returns (score, is_strong). Strong = exact paisa match."""
    if a == b:
        return 1.0, True
    # Within 1% = "near exact" (handles minor fee/tax drift)
    if a > 0 and b > 0:
        ratio = abs(a - b) / max(a, b)
        if ratio <= 0.01:
            return 0.6, False
    return 0.0, False


def _date_score(ts_a, ts_b, cfg: MatchConfig) -> float:
    """Closer dates score higher. Beyond 7 days = 0."""
    delta = abs((ts_a - ts_b).total_seconds())
    days = delta / 86400.0
    if days <= 0.5:
        return cfg.w_date_same_day
    if days <= 3:
        return cfg.w_date_within_3
    if days <= 7:
        return cfg.w_date_within_7
    return 0.0


def _ref_score(ref_a: str, ref_b: str) -> tuple[float, bool]:
    """Reference containment. Strong = one fully contains the other."""
    if not ref_a or not ref_b:
        return 0.0, False
    a, b = ref_a.lower(), ref_b.lower()
    if a == b:
        return 1.0, True
    if a in b or b in a:
        return 0.7, True
    # Common suffix (last 6 chars) — handles truncation across systems
    if len(a) >= 6 and len(b) >= 6 and a[-6:] == b[-6:]:
        return 0.3, False
    return 0.0, False


def _counterparty_score(cp_a: str, cp_b: str) -> float:
    """Counterparty must agree on a masked identifier."""
    if not cp_a or not cp_b:
        return 0.0
    if cp_a == cp_b:
        return 1.0
    # Extract last4 from each
    last4_a = cp_a.split("****")[-1] if "****" in cp_a else cp_a[-4:]
    last4_b = cp_b.split("****")[-1] if "****" in cp_b else cp_b[-4:]
    if last4_a and last4_b and last4_a == last4_b:
        return 0.5
    return 0.0


def score_pair(a: NormalizedTxn, b: NormalizedTxn, cfg: MatchConfig) -> ScoredCandidate:
    """Score one pair of transactions."""
    if a.txn_id == b.txn_id:
        # Same transaction seen in two sources — perfect match by definition
        return ScoredCandidate(
            txn_a=a, txn_b=b,
            score=1.0, has_strong_signal=True,
            score_breakdown={"self_match": 1.0},
        )

    amount_s, amount_strong = _amount_score(a.amount_paise, b.amount_paise)
    date_s = _date_score(a.timestamp, b.timestamp, cfg)
    ref_s, ref_strong = _ref_score(a.reference, b.reference)
    cp_s = _counterparty_score(a.counterparty, b.counterparty)

    # Compose weights
    score = 0.0
    if amount_strong:
        score += cfg.w_amount_exact
    elif amount_s > 0:
        score += cfg.w_amount_near
    score += date_s
    score += ref_s * cfg.w_ref_containment
    score += cp_s * cfg.w_counterparty

    has_strong = amount_strong or ref_strong

    return ScoredCandidate(
        txn_a=a, txn_b=b,
        score=round(score, 4),
        has_strong_signal=has_strong,
        score_breakdown={
            "amount": round(amount_s, 4),
            "amount_strong": amount_strong,
            "date": round(date_s, 4),
            "ref": round(ref_s, 4),
            "ref_strong": ref_strong,
            "counterparty": round(cp_s, 4),
        },
    )


def find_candidate_universe(
    txns: Iterable[NormalizedTxn],
) -> list[tuple[NormalizedTxn, NormalizedTxn]]:
    """Pair transactions across different sources only.

    Two same-source transactions rarely represent the same underlying
    payment event; pairing them wastes work. Only cross-source pairs
    are candidates.

    To avoid O(N²) blowup, we bucket by amount (rounded to nearest
    ₹100) and only pair within/between adjacent buckets. This is a
    coarse pre-filter, not a correctness step.
    """
    txns = list(txns)
    by_amount_bucket: dict[int, list[NormalizedTxn]] = {}
    for t in txns:
        bucket = t.amount_paise // 10000  # nearest ₹100
        by_amount_bucket.setdefault(bucket, []).append(t)

    pairs = []
    buckets = sorted(by_amount_bucket.keys())
    for i, bucket_a in enumerate(buckets):
        for bucket_b in buckets[i:]:
            list_a = by_amount_bucket[bucket_a]
            list_b = by_amount_bucket[bucket_b] if bucket_a != bucket_b else list_a
            for a, b in product(list_a, list_b):
                if a.txn_id >= b.txn_id:  # avoid duplicate unordered pairs
                    continue
                if a.source == b.source:
                    continue
                # Different directions (credit vs debit) — rarely the same event
                if a.direction != b.direction:
                    continue
                pairs.append((a, b))
    return pairs


class MatchEngine:
    """Probabilistic match engine. Use after RuleEngine for unmatched txns."""

    def __init__(self, config: MatchConfig | None = None):
        self.config = config or MatchConfig()

    def match(
        self,
        txns: list[NormalizedTxn],
    ) -> tuple[list[MatchDecision], list[NormalizedTxn]]:
        """Returns (decisions, still_unmatched).

        Each transaction is matched at most once. Greedy best-first
        assignment: sort candidates by score descending, take each in
        order, mark both sides as used if accepted.
        """
        candidates: list[ScoredCandidate] = []
        for a, b in find_candidate_universe(txns):
            candidates.append(score_pair(a, b, self.config))

        candidates.sort(key=lambda c: c.score, reverse=True)

        used_ids: set[str] = set()
        decisions: list[MatchDecision] = []

        for cand in candidates:
            if cand.txn_a.txn_id in used_ids or cand.txn_b.txn_id in used_ids:
                continue
            if cand.score < self.config.accept_threshold:
                continue
            if self.config.require_strong and not cand.has_strong_signal:
                continue

            used_ids.add(cand.txn_a.txn_id)
            used_ids.add(cand.txn_b.txn_id)

            decisions.append(MatchDecision(
                txn_id_a=cand.txn_a.txn_id,
                txn_id_b=cand.txn_b.txn_id,
                matched=True,
                source=MatchSource.PROBABILISTIC,
                reasons=[f"score={cand.score:.3f}"],
                score=cand.score,
                score_breakdown=cand.score_breakdown,
            ))

        unmatched = [t for t in txns if t.txn_id not in used_ids]
        return decisions, unmatched