"""Day 1 demo: generate dataset, run rule engine, show output.

This is the proof-of-concept demo. Run with: python demo_day1.py
"""
from collections import Counter
from fixtures.generate_dataset import generate
from src.rule_engine import RuleEngine


def main():
    print("=" * 70)
    print("RECOUP — Day 1 Demo: Multi-Source Reconciliation (Rule Engine Core)")
    print("=" * 70)
    print()

    # Generate synthetic dataset (deterministic, seed=42)
    print("Step 1: Generating synthetic dataset...")
    txns, ground_truth = generate(total_transactions=300, seed=42)
    print(f"  Generated {len(txns)} transactions across 5 sources")
    print(f"  Ground truth pairs: {len(ground_truth)}")
    print()

    # Show source distribution
    source_counts = Counter(t.source.value for t in txns)
    print("  Source distribution:")
    for src, n in sorted(source_counts.items()):
        print(f"    {src:15s}  {n:4d} txns")
    print()

    # Run rule engine
    print("Step 2: Running deterministic rule engine...")
    engine = RuleEngine()
    decisions, unmatched = engine.match(txns)
    matched_decisions = [d for d in decisions if d.matched]
    print(f"  Rules fired: {len(matched_decisions)} matches")
    print(f"  Unmatched (will go to match engine Day 2): {len(unmatched)}")
    print()

    # Show match rule distribution
    rule_counts = Counter()
    for d in matched_decisions:
        for reason in d.reasons:
            rule_counts[reason] += 1
    print("  Rule firings:")
    for rule, n in sorted(rule_counts.items()):
        print(f"    {rule:35s}  {n:4d}")
    print()

    # Ground truth summary
    print("Step 3: Ground truth (what SHOULD match):")
    gt_outcomes = Counter(g[2] for g in ground_truth)
    for outcome, n in sorted(gt_outcomes.items()):
        print(f"    {outcome:35s}  {n:4d}")
    print()

    # Honest limitations
    print("=" * 70)
    print("HONEST LIMITATIONS (Day 1):")
    print("- Only deterministic rules. No fuzzy/probabilistic matching yet (Day 2).")
    print("- No exception classification yet (Day 2).")
    print("- No LLM diagnosis layer yet (Day 2).")
    print("- No MCP server yet (Day 3).")
    print("- No evaluation pipeline yet (Day 4).")
    print("- Synthetic data only. Real Razorpay test-mode adapter not built.")
    print("=" * 70)


if __name__ == "__main__":
    main()