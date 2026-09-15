"""Orchestrator: ties Day1 + Day2 into one pipeline.

Single entry point that runs:
  raw txns → normalize → rule engine → match engine → classify exceptions → LLM diagnose (ambiguous only) → final report
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from src.data_model import (
    NormalizedTxn, MatchDecision, ExceptionRecord, Diagnosis, ExceptionReason, DiagnosisClass,
)
from src.rule_engine import RuleEngine
from src.match_engine import MatchEngine, MatchConfig
from src.exception_classifier import classify_batch
from src.diagnosis import diagnose_batch, StubProvider, LLMProvider


@dataclass
class PipelineReport:
    total: int = 0
    rule_matched: int = 0
    prob_matched: int = 0
    exceptions: int = 0
    diagnosed: int = 0
    refused: int = 0
    classified_deterministic: int = 0
    exception_breakdown: dict[str, int] = field(default_factory=dict)
    auto_match_rate: float = 0.0
    exception_rate: float = 0.0
    diagnosis_coverage: float = 0.0
    diagnosis_class_breakdown: dict[str, int] = field(default_factory=dict)


@dataclass
class PipelineResult:
    rule_decisions: list[MatchDecision]
    prob_decisions: list[MatchDecision]
    exceptions: list[ExceptionRecord]
    diagnoses: list[Diagnosis]
    report: PipelineReport


def run_pipeline(
    raw_txns: list[NormalizedTxn],
    llm_provider: LLMProvider | None = None,
) -> PipelineResult:
    """Full pipeline. Deterministic by default (StubProvider).
    Pass a real AnthropicProvider to invoke the LLM for ambiguous cases.
    """
    # Step 1: Rule engine (Day 1)
    rule_engine = RuleEngine()
    rule_decisions, after_rule = rule_engine.match(raw_txns)
    rule_matched = [d for d in rule_decisions if d.matched]

    # Step 2: Probabilistic match engine (Day 2)
    match_engine = MatchEngine()
    prob_decisions, after_prob = match_engine.match(after_rule)
    prob_matched = [d for d in prob_decisions if d.matched]

    # Step 3: Classify remaining exceptions (Day 2)
    exception_records = classify_batch(after_prob)
    exception_breakdown = Counter(r.reason.value for r in exception_records)

    # Step 4: LLM diagnoses ONLY ambiguous exceptions (Day 2)
    ambiguous = [e for e in exception_records if e.reason == ExceptionReason.AMBIGUOUS]
    non_ambiguous = [e for e in exception_records if e.reason != ExceptionReason.AMBIGUOUS]
    if ambiguous:
        diagnoses = diagnose_batch(ambiguous, raw_txns, provider=llm_provider)
    else:
        diagnoses = []

    # Compose report
    total = len(raw_txns)
    total_matched = len(rule_matched) + len(prob_matched)
    exceptions_count = len(exception_records)
    refused = sum(1 for d in diagnoses if d.refused)
    classified = len(non_ambiguous)  # Deterministic classification didn't need LLM
    diag_breakdown = Counter(d.diagnosis_class.value for d in diagnoses)

    report = PipelineReport(
        total=total,
        rule_matched=len(rule_matched),
        prob_matched=len(prob_matched),
        exceptions=exceptions_count,
        diagnosed=len(diagnoses),
        refused=refused,
        exception_breakdown=dict(exception_breakdown),
        auto_match_rate=(total_matched / total * 100) if total else 0.0,
        exception_rate=(exceptions_count / total * 100) if total else 0.0,
        diagnosis_coverage=(len(diagnoses) / len(ambiguous) * 100) if ambiguous else 100.0,
        diagnosis_class_breakdown=dict(diag_breakdown),
        classified_deterministic=classified,
    )

    return PipelineResult(
        rule_decisions=rule_decisions,
        prob_decisions=prob_decisions,
        exceptions=exception_records,
        diagnoses=diagnoses,
        report=report,
    )