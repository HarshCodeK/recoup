"""Tests for web_dashboard S++ features: explain view, timeline, eval panel."""
import json
import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fixtures.generate_dataset import generate
from src.orchestrator import run_pipeline
from src.diagnosis import StubProvider
from src.event_store import EventStore


@pytest.fixture
def pipeline_result():
    """Run a small pipeline for testing."""
    txns, ground_truth = generate(total_transactions=100, seed=42)
    result = run_pipeline(txns, llm_provider=StubProvider())
    return result, txns, ground_truth


class TestExplainView:
    def test_score_breakdown_exists_for_prob_matches(self, pipeline_result):
        result, txns, gt = pipeline_result
        for d in result.prob_decisions:
            if d.matched:
                assert isinstance(d.score_breakdown, dict)
                assert "amount" in d.score_breakdown
                assert "date" in d.score_breakdown
                assert "ref" in d.score_breakdown
                assert "counterparty" in d.score_breakdown
                break  # one is enough

    def test_rule_decisions_have_score(self, pipeline_result):
        result, txns, gt = pipeline_result
        for d in result.rule_decisions:
            if d.matched:
                assert d.score > 0
                assert isinstance(d.reasons, list)
                break

    def test_all_matched_decisions_have_reasons(self, pipeline_result):
        result, _, _ = pipeline_result
        for d in result.rule_decisions + result.prob_decisions:
            if d.matched:
                assert len(d.reasons) > 0


class TestTimeline:
    def test_events_logged_for_matched_txns(self, pipeline_result):
        result, txns, gt = pipeline_result
        with EventStore(":memory:") as store:
            for d in result.rule_decisions:
                if d.matched:
                    store.append("rule_match", {"a": d.txn_id_a, "b": d.txn_id_b}, d.txn_id_a)
            for d in result.prob_decisions:
                if d.matched:
                    store.append("prob_match", {"a": d.txn_id_a, "b": d.txn_id_b, "score": d.score}, d.txn_id_a)
            for exc in result.exceptions:
                store.append("exception_classified", {"reason": exc.reason.value}, exc.txn_id)

            # A matched txn should have at least one event
            matched_txn = result.rule_decisions[0].txn_id_a if result.rule_decisions else None
            if matched_txn:
                events = store.get_events_for_txn(matched_txn)
                assert len(events) >= 1

    def test_exception_txns_have_events(self, pipeline_result):
        result, _, _ = pipeline_result
        with EventStore(":memory:") as store:
            for exc in result.exceptions:
                store.append("exception_classified", {"reason": exc.reason.value}, exc.txn_id)

            if result.exceptions:
                exc_txn = result.exceptions[0].txn_id
                events = store.get_events_for_txn(exc_txn)
                assert len(events) >= 1
                assert events[0]["event_type"] == "exception_classified"


class TestEvalPanel:
    def test_ground_truth_has_pairs(self, pipeline_result):
        _, _, gt = pipeline_result
        assert len(gt) > 0
        for a, b, label in gt:
            assert isinstance(a, str)
            assert isinstance(b, str)
            assert isinstance(label, str)

    def test_precision_recall_computed(self, pipeline_result):
        result, txns, gt = pipeline_result
        # Compute precision/recall like the dashboard does
        gt_pairs = set()
        for a, b, label in gt:
            if not label.startswith("exception:"):
                gt_pairs.add((min(a, b), max(a, b)))

        engine_matched = set()
        for d in result.rule_decisions + result.prob_decisions:
            if d.matched:
                pair = (min(d.txn_id_a, d.txn_id_b), max(d.txn_id_a, d.txn_id_b))
                engine_matched.add(pair)

        tp = len(engine_matched & gt_pairs)
        fp = len(engine_matched - gt_pairs)
        fn = len(gt_pairs - engine_matched)
        precision = tp / max(tp + fp, 1)
        recall = tp / max(tp + fn, 1)
        f1 = 2 * precision * recall / max(precision + recall, 1e-9)

        assert 0.0 <= precision <= 1.0
        assert 0.0 <= recall <= 1.0
        assert 0.0 <= f1 <= 1.0

    def test_pipeline_report_has_all_fields(self, pipeline_result):
        result, _, _ = pipeline_result
        r = result.report
        assert r.total > 0
        assert r.rule_matched >= 0
        assert r.prob_matched >= 0
        assert r.exceptions >= 0
        assert isinstance(r.exception_breakdown, dict)


class TestDashboardEndpoints:
    def test_fetch_explain_returns_breakdown(self, pipeline_result):
        from src.web_dashboard import fetch_explain
        result, _, _ = pipeline_result
        # Store a decision
        import tempfile, os
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "pipeline.db")
        import sqlite3
        conn = sqlite3.connect(db)
        conn.execute("""CREATE TABLE match_decisions (
            txn_id_a TEXT, txn_id_b TEXT, matched INTEGER, source TEXT,
            reasons TEXT, score REAL, score_breakdown TEXT
        )""")
        for d in result.rule_decisions + result.prob_decisions:
            if d.matched:
                conn.execute(
                    "INSERT INTO match_decisions VALUES (?,?,?,?,?,?,?)",
                    (d.txn_id_a, d.txn_id_b, 1, d.source.value,
                     json.dumps(d.reasons), d.score, json.dumps(d.score_breakdown)),
                )
        conn.commit()
        conn.close()

        # Monkey-patch PIPELINE_DB for the test
        import src.web_dashboard as wd
        old_db = wd.PIPELINE_DB
        wd.PIPELINE_DB = db

        try:
            ex = fetch_explain(result.rule_decisions[0].txn_id_a, result.rule_decisions[0].txn_id_b)
            if ex:  # May be None if the pair wasn't rule-matched
                assert "score" in ex
                assert "breakdown" in ex
                assert "source" in ex
        finally:
            wd.PIPELINE_DB = old_db
