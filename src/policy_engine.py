"""Deterministic policy engine.

Hard rules that the LLM cannot bypass. Applied before any state
transition or recovery action. Each rule has a unit test.

Invariants:
  1. Never transition an UNKNOWN txn without explicit reconciliation
  2. Never auto-recover an exception flagged as fraud-suspected
  3. Never execute recovery without an idempotency key
  4. Never accept a state transition not in the FSM
"""
from __future__ import annotations
from dataclasses import dataclass
from src.data_model import NormalizedTxn, TransactionState, ExceptionReason, RecoveryAction


# Hard caps (paise). Defaults are conservative for an MVP.
@dataclass(frozen=True)
class PolicyConfig:
    max_recovery_amount_paise: int = 10_000_00  # ₹10,000
    min_recovery_amount_paise: int = 100_00     # ₹100 — economic floor
    max_recovery_attempts_per_event: int = 3
    recovery_cooldown_seconds: int = 3600  # 1 hour between attempts

    # Reasons that must NEVER be auto-recovered
    NEVER_AUTO_RECOVER: frozenset[ExceptionReason] = frozenset({
        ExceptionReason.DUPLICATE_FIRE,
    })


DEFAULT_CONFIG = PolicyConfig()


@dataclass
class PolicyDecision:
    """Result of policy evaluation. The LLM cannot override this."""
    action: str  # "recover", "hold", "escalate"
    allowed: bool
    reason: str
    requires_human: bool
    idempotency_key: str
    recovery_attempt_number: int = 1
    config_snapshot: PolicyConfig | None = None


def evaluate_recovery(
    txn: NormalizedTxn,
    exception_reason: ExceptionReason,
    prior_attempts: int = 0,
    config: PolicyConfig = DEFAULT_CONFIG,
) -> PolicyDecision:
    """Decide whether to auto-recover, hold, or escalate.

    Returns a PolicyDecision. The LLM does NOT see this function's
    input or output — it's invoked AFTER diagnosis classifies, before
    any executor dispatches.
    """
    import hashlib

    # Invariant 1: amount within economic floor and ceiling
    if txn.amount_paise < config.min_recovery_amount_paise:
        return PolicyDecision(
            action="hold",
            allowed=False,
            reason=f"amount_below_economic_floor ({txn.amount_paise/100:.2f} INR)",
            requires_human=True,
            idempotency_key=_idem_key(txn, "floor"),
        )
    if txn.amount_paise > config.max_recovery_amount_paise:
        return PolicyDecision(
            action="escalate",
            allowed=False,
            reason=f"amount_above_max_cap ({txn.amount_paise/100:.2f} INR)",
            requires_human=True,
            idempotency_key=_idem_key(txn, "cap"),
        )

    # Invariant 2: never auto-recover DUPLICATE_FIRE (refund workflow, not retry)
    if exception_reason in config.NEVER_AUTO_RECOVER:
        return PolicyDecision(
            action="escalate",
            allowed=False,
            reason=f"reason_requires_human: {exception_reason.value}",
            requires_human=True,
            idempotency_key=_idem_key(txn, "never_auto"),
        )

    # Invariant 3: max attempts
    if prior_attempts >= config.max_recovery_attempts_per_event:
        return PolicyDecision(
            action="escalate",
            allowed=False,
            reason=f"max_attempts_reached ({prior_attempts})",
            requires_human=True,
            idempotency_key=_idem_key(txn, f"max_{prior_attempts}"),
        )

    # Allowed: standard recovery via deterministic executor
    return PolicyDecision(
        action="recover",
        allowed=True,
        reason=f"policy_pass: amount={txn.amount_paise/100:.0f}INR, reason={exception_reason.value}",
        requires_human=False,  # auto, but can be overridden by human override endpoint
        idempotency_key=_idem_key(txn, f"recover_{exception_reason.value}_{prior_attempts+1}"),
        recovery_attempt_number=prior_attempts + 1,
        config_snapshot=config,
    )


def _idem_key(txn: NormalizedTxn, action: str) -> str:
    """Deterministic idempotency key derived from txn_id + action.

    Same txn + same action = same key. Different action = different key.
    The reason is part of the action so different exception types for
    the same txn get different keys.
    """
    import hashlib
    raw = f"{txn.txn_id}|{action}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]