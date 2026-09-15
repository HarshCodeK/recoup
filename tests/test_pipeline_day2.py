"""End-to-end Day 2 test: full pipeline on the synthetic dataset."""
from fixtures.generate_dataset import generate
from src.orchestrator import run_pipeline
from src.diagnosis import StubProvider


def test_full_pipeline_runs_without_error():
    txns, ground_truth = generate(total_transactions=300, seed=42)
    result = run_pipeline(txns, llm_provider=StubProvider())
    assert result.report.total == 300


def test_pipeline_matches_at_least_10_percent():
    """Pipeline resolves at least 10% of transactions deterministically.

    Note: the rule engine alone gets ~16-18% on this dataset because
    most transactions are intentionally heterogeneous (cross-source
    without explicit join keys). The probabilistic engine adds a few
    more. The 87%+ headline number that goes in the README is
    measured AFTER adding LLM diagnosis + recovery — that's the
    evaluated end-to-end system, not just rule matching.
    """
    txns, _ = generate(total_transactions=300, seed=42)
    result = run_pipeline(txns, llm_provider=StubProvider())
    assert result.report.auto_match_rate >= 10.0, (
        f"Auto-match rate too low: {result.report.auto_match_rate:.1f}%"
    )


def test_pipeline_classifies_all_exceptions():
    """Every unmatched transaction gets a reason code."""
    txns, _ = generate(total_transactions=300, seed=42)
    result = run_pipeline(txns, llm_provider=StubProvider())
    assert result.report.exceptions == len(result.exceptions)
    assert all(e.reason for e in result.exceptions)


def test_pipeline_refuses_when_no_llm():
    """StubProvider returns REFUSED for every ambiguous exception."""
    txns, _ = generate(total_transactions=300, seed=42)
    result = run_pipeline(txns, llm_provider=StubProvider())
    if result.diagnoses:
        # All diagnoses from the stub provider should be REFUSED
        refused_count = sum(1 for d in result.diagnoses if d.refused)
        assert refused_count == len(result.diagnoses)


def test_pipeline_deterministic_across_runs():
    """Same seed = same report numbers."""
    txns1, _ = generate(total_transactions=300, seed=42)
    txns2, _ = generate(total_transactions=300, seed=42)
    r1 = run_pipeline(txns1, llm_provider=StubProvider())
    r2 = run_pipeline(txns2, llm_provider=StubProvider())
    assert r1.report.rule_matched == r2.report.rule_matched
    assert r1.report.prob_matched == r2.report.prob_matched
    assert r1.report.exceptions == r2.report.exceptions


def test_pipeline_handles_empty_input():
    """No transactions = empty report, no crashes."""
    result = run_pipeline([], llm_provider=StubProvider())
    assert result.report.total == 0
    assert result.report.exceptions == 0
    assert result.report.auto_match_rate == 0.0