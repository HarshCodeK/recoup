"""Tests for eval.py — full end-to-end pipeline + metrics."""
import pytest
from eval import run_evaluation, format_report


def test_eval_produces_real_numbers():
    report = run_evaluation(dataset_size=300, seed=42)
    assert report.dataset_size == 300
    assert report.auto_matched >= 0
    assert report.exceptions >= 0


def test_eval_is_reproducible():
    r1 = run_evaluation(dataset_size=200, seed=42)
    r2 = run_evaluation(dataset_size=200, seed=42)
    assert r1.auto_matched == r2.auto_matched
    assert r1.exceptions == r2.exceptions
    assert r1.precision_estimate == r2.precision_estimate


def test_eval_audit_chain_ok():
    report = run_evaluation(dataset_size=200, seed=42)
    assert report.audit_chain_ok is True
    assert report.audit_chain_broken_at is None


def test_eval_records_events():
    report = run_evaluation(dataset_size=200, seed=42)
    assert report.audit_events_recorded > 0


def test_eval_recovery_proposed_count_matches_exceptions_minus_subscriptions():
    report = run_evaluation(dataset_size=200, seed=42)
    subs = report.exception_breakdown.get("standalone_recurring", 0)
    expected_proposed = report.exceptions - subs
    assert report.recovery_proposed == expected_proposed


def test_eval_format_report_is_human_readable():
    report = run_evaluation(dataset_size=100, seed=42)
    text = format_report(report)
    assert "RECOUP" in text
    assert "Auto-match rate" in text
    assert "Exceptions" in text
    assert "Precision" in text
    assert "Audit Trail" in text


def test_eval_precision_recall_are_valid_floats():
    report = run_evaluation(dataset_size=200, seed=42)
    assert 0.0 <= report.precision_estimate <= 1.0
    assert 0.0 <= report.recall_estimate <= 1.0
    assert 0.0 <= report.f1_estimate <= 1.0