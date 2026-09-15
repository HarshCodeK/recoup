"""End-to-end evaluation pipeline.

Runs the full reconciliation + exception classification + LLM
diagnosis + recovery proposal pipeline on a 3000-transaction synthetic
dataset. Computes headline metrics for the pitch.

Reproducible: seed=42 → same numbers every time.
"""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass, field

from fixtures.generate_dataset import generate
from src.orchestrator import run_pipeline
from src.diagnosis import StubProvider
from src.recovery import propose_recovery
from src.event_store import EventStore
from src.data_model import NormalizedTxn, ExceptionReason, Source


@dataclass
class EvalReport:
    dataset_size: int = 0
    rule_matched: int = 0
    prob_matched: int = 0
    auto_matched: int = 0
    auto_match_rate_pct: float = 0.0
    exceptions: int = 0
    exception_rate_pct: float = 0.0
    exception_breakdown: dict[str, int] = field(default_factory=dict)
    recovery_proposed: int = 0
    recovery_allowed_by_policy: int = 0
    recovery_blocked_by_policy: int = 0
    recovery_blocked_reasons: dict[str, int] = field(default_factory=dict)
    audit_events_recorded: int = 0
    audit_chain_ok: bool = True
    audit_chain_broken_at: int | None = None
    ground_truth_match_pairs: int = 0
    precision_estimate: float = 0.0
    recall_estimate: float = 0.0
    f1_estimate: float = 0.0


def run_evaluation(dataset_size: int = 3000, seed: int = 42) -> EvalReport:
    """Run end-to-end evaluation. Reproducible by seed."""
    txns, ground_truth = generate(total_transactions=dataset_size, seed=seed)
    result = run_pipeline(txns, llm_provider=StubProvider())

    # Build txn lookup for recovery proposals
    txn_by_id = {t.txn_id: t for t in txns}

    recovery_proposed = 0
    recovery_allowed = 0
    recovery_blocked = 0
    blocked_reasons: Counter = Counter()

    for exc in result.exceptions:
        txn = txn_by_id.get(exc.txn_id)
        if txn is None:
            continue
        # Subscriptions don't need recovery
        if exc.reason == ExceptionReason.STANDALONE_RECURRING:
            continue
        action, policy = propose_recovery(txn, exc.reason)
        recovery_proposed += 1
        if policy.allowed:
            recovery_allowed += 1
        else:
            recovery_blocked += 1
            blocked_reasons[policy.reason.split(" ")[0]] += 1

    # Record audit events
    with EventStore(":memory:") as store:
        for d in result.rule_decisions:
            if d.matched:
                store.append("rule_match", {"a": d.txn_id_a, "b": d.txn_id_b, "score": d.score}, d.txn_id_a)
        for d in result.prob_decisions:
            if d.matched:
                store.append("prob_match", {"a": d.txn_id_a, "b": d.txn_id_b, "score": d.score}, d.txn_id_a)
        for exc in result.exceptions:
            store.append("exception_classified", {"reason": exc.reason.value}, exc.txn_id)
        for d in result.diagnoses:
            store.append("llm_diagnosis", {"class": d.diagnosis_class.value, "refused": d.refused}, d.txn_id)
        chain_ok, broken_at = store.verify_chain()
        event_count = len(store.get_all_events())

    # Compute precision/recall against ground truth
    matched_ids = set()
    for d in result.rule_decisions:
        if d.matched:
            matched_ids.add(d.txn_id_a)
            matched_ids.add(d.txn_id_b)
    for d in result.prob_decisions:
        if d.matched:
            matched_ids.add(d.txn_id_a)
            matched_ids.add(d.txn_id_b)

    gt_match_pairs = [(a, b) for (a, b, outcome) in ground_truth if outcome == "match"]
    gt_match_ids = set()
    for a, b in gt_match_pairs:
        gt_match_ids.add(a)
        gt_match_ids.add(b)

    if gt_match_ids:
        tp = len(matched_ids & gt_match_ids)
        fp = len(matched_ids - gt_match_ids)
        fn = len(gt_match_ids - matched_ids)
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    else:
        precision = recall = f1 = 0.0

    auto_matched = result.report.rule_matched + result.report.prob_matched
    return EvalReport(
        dataset_size=dataset_size,
        rule_matched=result.report.rule_matched,
        prob_matched=result.report.prob_matched,
        auto_matched=auto_matched,
        auto_match_rate_pct=round((auto_matched / dataset_size * 100) if dataset_size else 0.0, 2),
        exceptions=result.report.exceptions,
        exception_rate_pct=round((result.report.exceptions / dataset_size * 100) if dataset_size else 0.0, 2),
        exception_breakdown=result.report.exception_breakdown,
        recovery_proposed=recovery_proposed,
        recovery_allowed_by_policy=recovery_allowed,
        recovery_blocked_by_policy=recovery_blocked,
        recovery_blocked_reasons=dict(blocked_reasons),
        audit_events_recorded=event_count,
        audit_chain_ok=chain_ok,
        audit_chain_broken_at=broken_at,
        ground_truth_match_pairs=len(gt_match_pairs),
        precision_estimate=round(precision, 3),
        recall_estimate=round(recall, 3),
        f1_estimate=round(f1, 3),
    )


def format_report(report: EvalReport) -> str:
    """Format the eval report for the pitch/README."""
    lines = [
        "=" * 70,
        "RECOUP — End-to-End Evaluation Report",
        "=" * 70,
        "",
        f"Dataset:                 {report.dataset_size} transactions (seed=42, 5 sources)",
        f"Ground truth match pairs: {report.ground_truth_match_pairs}",
        "",
        "─── Auto-Match (rule + probabilistic engines) ───",
        f"Rule matched:            {report.rule_matched}",
        f"Probabilistic matched:   {report.prob_matched}",
        f"Total auto-matched:      {report.auto_matched}",
        f"Auto-match rate:         {report.auto_match_rate_pct}%",
        "",
        "─── Exceptions (deterministic classification) ───",
        f"Exceptions:              {report.exceptions}",
        f"Exception rate:          {report.exception_rate_pct}%",
    ]
    if report.exception_breakdown:
        lines.append("By reason:")
        for reason, n in sorted(report.exception_breakdown.items(), key=lambda x: -x[1]):
            lines.append(f"  {reason:25s}  {n}")
    lines.extend([
        "",
        "─── Recovery (policy-gated) ───",
        f"Recovery proposed:       {report.recovery_proposed}",
        f"Allowed by policy:       {report.recovery_allowed_by_policy}",
        f"Blocked by policy:       {report.recovery_blocked_by_policy}",
    ])
    if report.recovery_blocked_reasons:
        lines.append("Blocked reasons:")
        for reason, n in sorted(report.recovery_blocked_reasons.items(), key=lambda x: -x[1]):
            lines.append(f"  {reason:25s}  {n}")
    lines.extend([
        "",
        "─── Precision / Recall vs Ground Truth ───",
        f"Precision:               {report.precision_estimate:.3f}",
        f"Recall:                  {report.recall_estimate:.3f}",
        f"F1:                      {report.f1_estimate:.3f}",
        "",
        "─── Audit Trail (SHA-256 hash-chained) ───",
        f"Events recorded:         {report.audit_events_recorded}",
        f"Chain integrity:         {'OK' if report.audit_chain_ok else f'BROKEN at seq {report.audit_chain_broken_at}'}",
        "",
        "Reproducibility: same seed (42) → same numbers every run.",
        "Command: PYTHONPATH=. python eval.py",
        "=" * 70,
    ])
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    size = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    report = run_evaluation(dataset_size=size)
    print(format_report(report))