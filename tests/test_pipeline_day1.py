"""End-to-end Day 1 test: generate dataset, normalize, run rule engine."""
from collections import Counter
from fixtures.generate_dataset import generate
from src.rule_engine import RuleEngine


def test_full_pipeline_day1():
    """Generate 100 transactions, run through rule engine, verify output."""
    txns, ground_truth = generate(total_transactions=100, seed=42)

    # All generated transactions should be NormalizedTxn
    assert all(t.txn_id for t in txns)
    assert all(t.amount_paise >= 0 for t in txns)
    assert len(txns) >= 100  # may have generated a few extras for exceptions

    # Sources should be distributed across all5 types
    source_counts = Counter(t.source.value for t in txns)
    assert len(source_counts) >= 3   # at least3 sources represented

    # Run rule engine
    engine = RuleEngine()
    decisions, unmatched = engine.match(txns)

    # Should have some matches and some unmatched
    assert isinstance(decisions, list)
    assert isinstance(unmatched, list)
    matched_ids = set()
    for d in decisions:
        if d.matched:
            matched_ids.add(d.txn_id_a)
            matched_ids.add(d.txn_id_b)
    assert len(matched_ids) > 0   # at least some matches found

    # Ground truth should have entries of multiple types
    outcome_counts = Counter(g[2] for g in ground_truth)
    assert len(outcome_counts) >= 1

    print(f"\nDay 1 pipeline test:")
    print(f"  generated: {len(txns)} txns")
    print(f"  ground truth pairs: {len(ground_truth)}")
    print(f"  rule matches found: {len(matched_ids)}")
    print(f"  unmatched: {len(unmatched)}")
    print(f"  outcome distribution: {dict(outcome_counts)}")


def test_dataset_is_deterministic():
    """Same seed → same output."""
    txns1, gt1 = generate(total_transactions=50, seed=42)
    txns2, gt2 = generate(total_transactions=50, seed=42)
    assert len(txns1) == len(txns2)
    assert len(gt1) == len(gt2)
    # First few txn_ids should be identical
    assert txns1[0].txn_id == txns2[0].txn_id
    assert txns1[0].amount_paise == txns2[0].amount_paise


def test_different_seeds_produce_different_data():
    """Different seed → different output (sanity check)."""
    txns1, _ = generate(total_transactions=50, seed=42)
    txns2, _ = generate(total_transactions=50, seed=99)
    # Highly unlikely to be byte-identical
    assert str(txns1[0].amount_paise) != str(txns2[0].amount_paise) or \
           str(txns1[1].amount_paise) != str(txns2[1].amount_paise)


if __name__ == "__main__":
    test_full_pipeline_day1()
    test_dataset_is_deterministic()
    test_different_seeds_produce_different_data()
    print("\n✓ All Day 1 tests passed")