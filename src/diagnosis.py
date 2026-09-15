"""LLM diagnosis layer.

Called ONLY for ambiguous exceptions (those the deterministic
classifier couldn't pin a specific reason to). The LLM's job is
to read structured evidence and return a typed DiagnosisClass —
never a money action.

Critical invariants:
  1. No tool use. No function calling. No filesystem/network access.
  2. Output is typed (Pydantic). Malformed JSON → escalated, never guessed.
  3. Low confidence → REFUSED, never silently acted on.
  4. Falls back gracefully if API is unavailable.

The LLM can refuse. That's not a failure — that's the system
working correctly.
"""
from __future__ import annotations

import json
import os
from typing import Optional

from src.data_model import (
    NormalizedTxn, Diagnosis, DiagnosisClass, ExceptionRecord,
)


# System prompt — the LLM's only allowed output is a typed classification.
# This is enforced by prompt + schema validation + post-call review.
DIAGNOSIS_SYSTEM_PROMPT = """You are a financial-reconciliation triage classifier.

You receive structured evidence about one unresolved transaction.
Your job: classify WHY it could not be matched against other sources.

ALLOWED OUTPUT CLASSES (pick exactly one, or REFUSE):
  - AMBIGUOUS_AMOUNT           : the amount is plausible but doesn't match exactly
  - AMBIGUOUS_REFERENCE        : a similar reference exists but doesn't match exactly
  - TIMING_OUT_OF_BOUNDS       : the timing is outside normal settlement windows
  - CURRENCY_DRIFT             : currency conversion rate variation is the likely cause
  - DATA_INCOMPLETE            : critical fields (reference, counterparty) are missing
  - DUPLICATE_SUSPECTED        : the same transaction may have fired twice
  - RECOVERY_POSSIBLE          : looks like a recoverable merchant-side error
  - RECOVERY_NOT_POSSIBLE      : cannot be recovered through normal channels
  - REFUSED                    : you cannot determine the cause from the evidence

RULES:
  1. Never invent a classification. If uncertain, output REFUSED.
  2. Confidence is your honest self-rating, 0.0 to 1.0.
  3. Evidence must cite ONLY fields present in the input. Do not fabricate.
  4. Do not propose money actions. Only classify why.

OUTPUT FORMAT (strict JSON, no prose):
{
  "diagnosis_class": "one of the above",
  "confidence": 0.0 to 1.0,
  "evidence": ["field1: value1", "field2: value2"],
  "reasoning": "one short sentence"
}
"""


def _build_user_prompt(txn: NormalizedTxn, exception: ExceptionRecord, pool_context: list[dict]) -> str:
    """Build the structured input the LLM sees.

    pool_context is a bounded, sanitized sample of other transactions
    — never raw PII, never card numbers, never full customer names.
    """
    txn_summary = {
        "txn_id": txn.txn_id,
        "source": txn.source.value,
        "amount_paise": txn.amount_paise,
        "currency": txn.currency,
        "reference": txn.reference,
        "counterparty_masked": txn.counterparty,  # already masked at normalize time
        "timestamp": txn.timestamp.isoformat(),
        "direction": txn.direction.value,
    }
    exception_summary = {
        "txn_id": exception.txn_id,
        "reason_so_far": exception.reason.value,
    }
    return json.dumps({
        "transaction": txn_summary,
        "exception_record": exception_summary,
        "nearby_transactions_sample": pool_context[:5],  # bounded
    }, indent=2)


# --- Provider abstraction -----------------------------------------------------

class LLMProvider:
    """Interface for any LLM backend. Stub and Anthropic implementations below."""

    def complete(self, system: str, user: str, max_tokens: int = 256) -> str:
        raise NotImplementedError


class StubProvider(LLMProvider):
    """Deterministic stub. Used when no API key is configured or as a fallback.

    Returns a thoughtful REFUSED with 0.3 confidence. That's the
    safest possible behavior: it never invents a classification, and
    the system correctly escalates the transaction to human review.
    """

    def complete(self, system: str, user: str, max_tokens: int = 256) -> str:
        return json.dumps({
            "diagnosis_class": "REFUSED",
            "confidence": 0.3,
            "evidence": ["stub_provider: API not configured or unavailable"],
            "reasoning": "LLM unavailable; defaulting to safe refusal.",
        })


class AnthropicProvider(LLMProvider):
    """Real Anthropic provider. Reads ANTHROPIC_API_KEY from env.

    The Anthropic SDK is optional — if not installed, the caller
    should fall back to StubProvider.
    """

    def __init__(self, model: str = "claude-3-5-haiku-20241022"):
        try:
            import anthropic  # type: ignore
        except ImportError as e:
            raise ImportError(
                "anthropic package not installed. `pip install anthropic`"
            ) from e
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable not set")
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def complete(self, system: str, user: str, max_tokens: int = 256) -> str:
        msg = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        # Extract text from the response
        return msg.content[0].text


class OpenAICompatibleProvider(LLMProvider):
    """Provider for any OpenAI-compatible chat-completions endpoint.

    Works against kilo, OpenAI, Groq, OpenRouter, Together, vLLM, etc.
    Configuration via env vars (or explicit constructor args):

      OPENAI_BASE_URL    e.g. https://api.kilo.ai/api/gateway/v1
      OPENAI_API_KEY     bearer token (or pass api_key=...)
      OPENAI_MODEL       e.g. nvidia/nemotron-3-super-120b-a12b

    Network/parse errors are raised — the caller in diagnose_exception()
    treats them as a REFUSED. The provider itself does not silently swallow
    real failures; that would hide actual bugs.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 30.0,
    ):
        try:
            import httpx  # type: ignore
        except ImportError as e:
            raise ImportError(
                "httpx package not installed. `pip install httpx`"
            ) from e

        self._httpx = httpx
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL") or "").rstrip("/")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY") or ""
        self.model = model or os.environ.get("OPENAI_MODEL") or ""
        self.timeout = timeout

        if not self.base_url:
            raise ValueError(
                "OpenAICompatibleProvider: OPENAI_BASE_URL not set "
                "(e.g. https://api.kilo.ai/api/gateway/v1)"
            )
        if not self.api_key:
            raise ValueError(
                "OpenAICompatibleProvider: OPENAI_API_KEY not set"
            )
        if not self.model:
            raise ValueError(
                "OpenAICompatibleProvider: OPENAI_MODEL not set "
                "(e.g. nvidia/nemotron-3-super-120b-a12b)"
            )

    def complete(self, system: str, user: str, max_tokens: int = 256) -> str:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": 0.0,  # deterministic — diagnosis must be reproducible
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        resp = self._httpx.post(
            url, headers=headers, json=body, timeout=self.timeout
        )
        resp.raise_for_status()
        data = resp.json()
        # Standard OpenAI shape: choices[0].message.content
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(
                f"unexpected chat-completions response shape: {data}"
            ) from e


def get_default_provider() -> LLMProvider:
    """Pick provider based on environment.

    Order of preference:
      1. OpenAICompatibleProvider if OPENAI_BASE_URL + OPENAI_API_KEY + OPENAI_MODEL are set
      2. AnthropicProvider if ANTHROPIC_API_KEY is set AND anthropic is installed
      3. StubProvider otherwise (safe refusal — never invents a classification)

    The OpenAI-compatible route is checked first because it is what this
    project's evaluation runs against (kilo gateway → NVIDIA Nemotron).
    """
    if (
        os.environ.get("OPENAI_BASE_URL")
        and os.environ.get("OPENAI_API_KEY")
        and os.environ.get("OPENAI_MODEL")
    ):
        try:
            return OpenAICompatibleProvider()
        except ImportError:
            pass  # httpx missing — fall through
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return AnthropicProvider()
        except ImportError:
            pass
    return StubProvider()


# --- Diagnosis entry point ----------------------------------------------------

CONFIDENCE_FLOOR = 0.5


def _extract_json_block(text: str) -> str:
    """Best-effort JSON extractor for LLM output that includes chain-of-thought.

    Many chat models prepend reasoning prose before emitting the requested JSON.
    We accept the response if any of these succeed:
      1. The whole text is parseable as JSON (strict instruction-following).
      2. A ```json ...``` fenced block is present and parseable.
      3. The first {...} balanced top-level object is parseable.

    Otherwise the caller's json.loads will raise and the diagnosis will be
    escalated as REFUSED — which is the correct, honest behavior.
    """
    candidate = text.strip()
    # 1) already pure JSON
    try:
        json.loads(candidate)
        return candidate
    except (json.JSONDecodeError, ValueError):
        pass
    # 2) ```json ... ``` fenced block
    import re
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, re.DOTALL)
    if fence:
        try:
            json.loads(fence.group(1))
            return fence.group(1)
        except (json.JSONDecodeError, ValueError):
            pass
    # 3) first balanced {...} object
    start = candidate.find("{")
    if start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(candidate)):
            ch = candidate[i]
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    block = candidate[start:i + 1]
                    try:
                        json.loads(block)
                        return block
                    except (json.JSONDecodeError, ValueError):
                        break
    # Fallthrough — let the caller raise json.JSONDecodeError on raw text
    return candidate  # below this, the LLM is treated as having refused


def diagnose_exception(
    txn: NormalizedTxn,
    exception: ExceptionRecord,
    pool_context: list[NormalizedTxn] | None = None,
    provider: LLMProvider | None = None,
) -> Diagnosis:
    """Call the LLM to classify why this exception couldn't be matched.

    Returns a Diagnosis with one of the typed DiagnosisClass values.
    If the LLM fails, refuses, or returns low confidence, the
    Diagnosis reflects that honestly — it is NEVER silently coerced
    to a confident classification.
    """
    provider = provider or get_default_provider()

    pool_dicts = [
        {
            "txn_id": t.txn_id,
            "source": t.source.value,
            "amount_paise": t.amount_paise,
            "reference": t.reference,
            "timestamp": t.timestamp.isoformat(),
        }
        for t in (pool_context or [])
    ]
    user_prompt = _build_user_prompt(txn, exception, pool_dicts)

    try:
        raw = provider.complete(DIAGNOSIS_SYSTEM_PROMPT, user_prompt)
        json_text = _extract_json_block(raw)
        parsed = json.loads(json_text)
        diagnosis_class_str = parsed.get("diagnosis_class", "REFUSED")
        confidence = float(parsed.get("confidence", 0.0))
        evidence = list(parsed.get("evidence", []))
        reasoning = str(parsed.get("reasoning", ""))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        # Malformed output — treat as refusal
        return Diagnosis(
            txn_id=txn.txn_id,
            diagnosis_class=DiagnosisClass.REFUSED,
            confidence=0.0,
            evidence=[f"parse_error: {type(e).__name__}"],
            refused=True,
            reasoning=f"LLM returned malformed output: {e}",
        )
    except Exception as e:
        # API error, network error, anything else — treat as refusal
        return Diagnosis(
            txn_id=txn.txn_id,
            diagnosis_class=DiagnosisClass.REFUSED,
            confidence=0.0,
            evidence=[f"provider_error: {type(e).__name__}"],
            refused=True,
            reasoning=f"LLM provider failed: {e}",
        )

    # Validate the diagnosis class — accept either the enum value
    # (lowercase_with_underscores) or the enum NAME (UPPERCASE_WITH_UNDERSCORES)
    # because LLMs can return either form.
    raw_class = diagnosis_class_str
    diagnosis_class: DiagnosisClass
    matched_by_name = False
    try:
        diagnosis_class = DiagnosisClass(raw_class)
    except ValueError:
        # Try matching by NAME (uppercase) — the LLM may return either form
        matched = None
        for member in DiagnosisClass:
            if member.name == raw_class.upper().replace(" ", "_"):
                matched = member
                matched_by_name = True
                break
        if matched is not None:
            diagnosis_class = matched
        else:
            diagnosis_class = DiagnosisClass.REFUSED
            confidence = 0.0
    refused = (diagnosis_class == DiagnosisClass.REFUSED) or (confidence < CONFIDENCE_FLOOR)

    return Diagnosis(
        txn_id=txn.txn_id,
        diagnosis_class=diagnosis_class,
        confidence=round(confidence, 3),
        evidence=evidence,
        refused=refused,
        reasoning=reasoning,
    )


def diagnose_batch(
    exceptions: list[ExceptionRecord],
    pool: list[NormalizedTxn],
    provider: LLMProvider | None = None,
) -> list[Diagnosis]:
    """Diagnose a batch of exceptions. The LLM is called per-exception."""
    provider = provider or get_default_provider()
    txn_by_id = {t.txn_id: t for t in pool}
    out = []
    for exc in exceptions:
        txn = txn_by_id.get(exc.txn_id)
        if txn is None:
            # Should not happen if invariants hold — but be safe
            out.append(Diagnosis(
                txn_id=exc.txn_id,
                diagnosis_class=DiagnosisClass.REFUSED,
                confidence=0.0,
                evidence=["txn_not_found_in_pool"],
                refused=True,
                reasoning="Transaction record not found in pool",
            ))
            continue
        out.append(diagnose_exception(txn, exc, pool_context=pool, provider=provider))
    return out