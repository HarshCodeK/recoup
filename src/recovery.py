"""Recovery action proposer.

Given a transaction + classified exception + LLM diagnosis, propose
a concrete recovery action. The action is idempotent and gated by
the policy engine.
"""
from __future__ import annotations
from src.data_model import NormalizedTxn, ExceptionReason, Diagnosis, RecoveryAction
from src.policy_engine import evaluate_recovery, PolicyDecision


# Recovery actions are deterministic mappings from reason → action_type.
# The LLM never chooses the action — it may only provide additional
# context that the deterministic system already had access to.
RECOVERY_PLAYBOOK: dict[ExceptionReason, tuple[str, str, bool]] = {
    # reason → (action_type, description_template, requires_human)
    ExceptionReason.AMOUNT_MISMATCH: (
        "lookup_reference",
        "Look up the canonical amount from the matching invoice/payment reference and propose a payment link for the difference.",
        True,
    ),
    ExceptionReason.MISSING_REFERENCE: (
        "request_reference",
        "Request the merchant to provide the missing external reference. Until then, hold the transaction.",
        True,
    ),
    ExceptionReason.TIMING_WINDOW: (
        "extend_window",
        "Re-evaluate after the timing window closes. If still unmatched, escalate.",
        True,
    ),
    ExceptionReason.DUPLICATE_FIRE: (
        "flag_for_refund",
        "This looks like a duplicate charge. DO NOT auto-retry. Flag for human review and refund workflow.",
        True,
    ),
    ExceptionReason.MISSING_TXN: (
        "replay_gateway",
        "Replay the gateway call to fetch authoritative status. If payment was actually captured, transition to SUCCESS.",
        False,
    ),
    ExceptionReason.AMBIGUOUS: (
        "escalate_to_human",
        "No deterministic recovery possible. Escalate to human ops with full evidence.",
        True,
    ),
    ExceptionReason.STANDALONE_RECURRING: (
        "no_action",
        "This is a subscription record — no recovery needed.",
        False,
    ),
}


def propose_recovery(
    txn: NormalizedTxn,
    reason: ExceptionReason,
    diagnosis: Diagnosis | None = None,
    prior_attempts: int = 0,
) -> tuple[RecoveryAction, PolicyDecision]:
    """Propose a recovery action + apply policy gating.

    Returns (action, policy_decision). The caller is responsible for
    checking `policy.allowed` before executing.
    """
    if reason not in RECOVERY_PLAYBOOK:
        # Shouldn't happen, but defensive
        action_type = "escalate_to_human"
        description = f"unknown reason: {reason.value}"
        requires_human = True
    else:
        action_type, description, requires_human = RECOVERY_PLAYBOOK[reason]

    policy = evaluate_recovery(txn, reason, prior_attempts=prior_attempts)

    action = RecoveryAction(
        txn_id=txn.txn_id,
        action_type=action_type,
        description=description,
        idempotency_key=policy.idempotency_key,
        requires_human_approval=requires_human or policy.requires_human,
        executed=False,
    )
    return action, policy