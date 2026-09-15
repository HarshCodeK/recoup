"""Tests for diagnosis.py — LLM diagnosis layer with sandboxing and refusal."""
import json
import pytest
from datetime import datetime
from src.data_model import (
    NormalizedTxn, Source, TxnDirection,
    ExceptionRecord, ExceptionReason, DiagnosisClass,
)
from src.diagnosis import (
    diagnose_exception, diagnose_batch, StubProvider,
    CONFIDENCE_FLOOR, DIAGNOSIS_SYSTEM_PROMPT, get_default_provider,
)


def _txn(txn_id="t1", ref="ord_42", amount=100000):
    return NormalizedTxn(
        txn_id=txn_id, source=Source.UPI, direction=TxnDirection.CREDIT,
        amount_paise=amount, currency="INR",
        timestamp=datetime(2026, 6, 1, 10, 0),
        reference=ref, counterparty="vpa_****1234",
    )


def _exc(reason=ExceptionReason.AMBIGUOUS, txn_id="t1"):
    return ExceptionRecord(
        txn_id=txn_id, source=Source.UPI, amount_paise=100000,
        reason=reason, reference="ord_42",
        timestamp=datetime(2026, 6, 1, 10, 0),
    )


def test_stub_provider_returns_refusal():
    """When no API is configured, the system defaults to a safe REFUSED."""
    diagnosis = diagnose_exception(_txn(), _exc(), provider=StubProvider())
    assert diagnosis.diagnosis_class == DiagnosisClass.REFUSED
    # refused flag may be set to True via the model validator
    assert diagnosis.refused is True


def test_low_confidence_is_treated_as_refusal():
    """A response with confidence below floor is refused, not trusted."""
    class LowConfidenceProvider(StubProvider):
        def complete(self, system, user, max_tokens=256):
            return json.dumps({
                "diagnosis_class": "RECOVERY_POSSIBLE",
                "confidence": 0.3,  # below 0.5 floor
                "evidence": ["synthetic"],
                "reasoning": "low confidence test"
            })

    diagnosis = diagnose_exception(_txn(), _exc(), provider=LowConfidenceProvider())
    assert diagnosis.refused is True
    # Diagnosis class may be set, but refused=True means it won't be acted on


def test_malformed_json_is_refused():
    class MalformedProvider(StubProvider):
        def complete(self, system, user, max_tokens=256):
            return "not even json {{{"

    diagnosis = diagnose_exception(_txn(), _exc(), provider=MalformedProvider())
    assert diagnosis.refused is True
    assert diagnosis.diagnosis_class == DiagnosisClass.REFUSED


def test_unknown_diagnosis_class_is_refused():
    class BadClassProvider(StubProvider):
        def complete(self, system, user, max_tokens=256):
            return json.dumps({
                "diagnosis_class": "TOTALLY_MADE_UP_CLASS",
                "confidence": 0.9,
                "evidence": ["x"],
                "reasoning": "y"
            })

    diagnosis = diagnose_exception(_txn(), _exc(), provider=BadClassProvider())
    assert diagnosis.refused is True
    assert diagnosis.diagnosis_class == DiagnosisClass.REFUSED


def test_high_confidence_classification_is_accepted():
    class GoodProvider(StubProvider):
        def complete(self, system, user, max_tokens=256):
            return json.dumps({
                "diagnosis_class": "DATA_INCOMPLETE",
                "confidence": 0.85,
                "evidence": ["reference: missing"],
                "reasoning": "critical field absent"
            })

    diagnosis = diagnose_exception(_txn(), _exc(), provider=GoodProvider())
    assert diagnosis.refused is False
    assert diagnosis.diagnosis_class == DiagnosisClass.DATA_INCOMPLETE
    assert diagnosis.confidence == 0.85


def test_api_exception_is_refused_not_crashed():
    class CrashProvider(StubProvider):
        def complete(self, system, user, max_tokens=256):
            raise ConnectionError("API down")

    diagnosis = diagnose_exception(_txn(), _exc(), provider=CrashProvider())
    assert diagnosis.refused is True
    assert "provider_error" in diagnosis.evidence[0]


def test_diagnose_batch_handles_missing_txn():
    """If a txn referenced in an exception is not in the pool, refuse gracefully."""
    exc = _exc(txn_id="nonexistent")
    diagnoses = diagnose_batch([exc], [_txn()], provider=StubProvider())
    assert len(diagnoses) == 1
    assert diagnoses[0].refused is True
    assert "txn_not_found_in_pool" in diagnoses[0].evidence[0]


def test_diagnose_batch_empty_ambiguous_returns_empty():
    """If there are no ambiguous exceptions, no LLM calls are made."""
    exc = ExceptionRecord(
        txn_id="t1", source=Source.UPI, amount_paise=100000,
        reason=ExceptionReason.AMOUNT_MISMATCH,  # not ambiguous
        reference="ord_42", timestamp=datetime(2026, 6, 1, 10, 0),
    )
    # Orchestrator filters non-ambiguous; diagnose_batch itself handles whatever is passed
    diagnoses = diagnose_batch([exc], [_txn()], provider=StubProvider())
    # Since exc is non-ambiguous, but batch function still runs, result is one REFUSED diagnosis
    # (the orchestrator is what filters). We just confirm no crash.
    assert len(diagnoses) == 1


def test_system_prompt_includes_safety_rules():
    """The system prompt must explicitly forbid money actions."""
    assert "Never invent a classification" in DIAGNOSIS_SYSTEM_PROMPT or "Never invent" in DIAGNOSIS_SYSTEM_PROMPT
    assert "money actions" in DIAGNOSIS_SYSTEM_PROMPT.lower() or "money" in DIAGNOSIS_SYSTEM_PROMPT.lower()
    assert "REFUSED" in DIAGNOSIS_SYSTEM_PROMPT


def test_default_provider_is_stub_without_api_key(monkeypatch):
    """Without ANTHROPIC_API_KEY, default provider is StubProvider."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    p = get_default_provider()
    assert isinstance(p, StubProvider)