"""Live LLM smoke test for Recoup.

Runs the diagnosis layer against a real model through the configured
OpenAI-compatible gateway (default: kilo → poolside/laguna-s-2.1:free).

Skips automatically if OPENAI_BASE_URL/OPENAI_API_KEY/OPENAI_MODEL are
not set. Use this to verify the wired provider end-to-end without
touching the offline test suite.

    PYTHONPATH=. python scripts/live_llm_smoke.py
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.diagnosis import OpenAICompatibleProvider, diagnose_exception
from src.data_model import (
    ExceptionRecord, ExceptionReason, NormalizedTxn, Source, TxnDirection,
)


def main() -> int:
    if not (os.environ.get("OPENAI_BASE_URL") and
            os.environ.get("OPENAI_API_KEY") and
            os.environ.get("OPENAI_MODEL")):
        print("Live LLM smoke skipped: set OPENAI_BASE_URL/OPENAI_API_KEY/OPENAI_MODEL.")
        print("See .env.example.")
        return 0

    provider = OpenAICompatibleProvider()
    print(f"Provider: {type(provider).__name__} @ {provider.model}\n")

    cases = [
        ("txn_a", 5000,    "UPI/AB",        "alice@upi",  "low-value, real reference"),
        ("txn_b", 1234500, "CARD/XYZ",      "bob@bank",   "high-value, suspect duplicate"),
        ("txn_c", 99,      None,            "carol@wlt",  "missing reference, data incomplete"),
    ]
    classified = refused = 0
    for tid, amt, ref, cp, label in cases:
        txn = NormalizedTxn(
            txn_id=tid, source=Source.UPI, direction=TxnDirection.CREDIT,
            amount_paise=amt, currency="INR",
            timestamp=datetime(2026, 8, 30, 12, 0, 0),
            reference=ref, counterparty=cp,
        )
        exc = ExceptionRecord(
            txn_id=tid, source=Source.UPI, amount_paise=amt,
            reason=ExceptionReason.AMBIGUOUS,
            timestamp=datetime.utcnow(),
            detail=label, reference=ref,
        )
        diag = diagnose_exception(txn, exc, [txn], provider=provider)
        print(f"  {tid:6s}  class={diag.diagnosis_class.value:25s} "
              f"conf={diag.confidence:.2f}  refused={diag.refused}  [{label}]")
        if diag.refused:
            refused += 1
        else:
            classified += 1

    print(f"\n  {classified} classified, {refused} honestly refused (of {len(cases)})")
    print("  Smoke test OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())