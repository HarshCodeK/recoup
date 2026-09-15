"""Transaction lifecycle state machine.

A normalized transaction moves through states. Transitions are
deterministic and audited. The LLM never causes a state change.

State diagram:

    INITIATED ──► PENDING ──► SUCCESS
        │            │  ╲
        │            │   ╲──► FAILED
        │            │
        │            ▼
        │         UNKNOWN ──► RECONCILING ──► SUCCESS / FAILED
        │            │                            │
        │            ▼                            ▼
        └────────► HOLD                          HOLD
"""
from __future__ import annotations
from enum import Enum
from typing import Optional

from src.data_model import TransactionState


# Allowed transitions: state → set of legal next states
ALLOWED_TRANSITIONS: dict[TransactionState, set[TransactionState]] = {
    TransactionState.INITIATED: {TransactionState.PENDING, TransactionState.FAILED, TransactionState.HOLD},
    TransactionState.PENDING: {TransactionState.SUCCESS, TransactionState.FAILED, TransactionState.UNKNOWN, TransactionState.HOLD},
    TransactionState.UNKNOWN: {TransactionState.RECONCILING, TransactionState.HOLD},
    TransactionState.RECONCILING: {TransactionState.SUCCESS, TransactionState.FAILED, TransactionState.HOLD},
    TransactionState.SUCCESS: set(),  # terminal
    TransactionState.FAILED: set(),  # terminal
    TransactionState.HOLD: {TransactionState.PENDING, TransactionState.RECONCILING, TransactionState.FAILED},
}


class InvalidTransition(Exception):
    """Raised when a state transition is not allowed."""
    def __init__(self, from_state: TransactionState, to_state: TransactionState):
        super().__init__(f"Invalid transition: {from_state.value} → {to_state.value}")
        self.from_state = from_state
        self.to_state = to_state


def is_allowed(from_state: TransactionState, to_state: TransactionState) -> bool:
    """Check if a transition is allowed. Same-state is a no-op, always allowed."""
    if from_state == to_state:
        return True
    return to_state in ALLOWED_TRANSITIONS.get(from_state, set())


def transition(from_state: TransactionState, to_state: TransactionState) -> TransactionState:
    """Attempt a transition. Raises InvalidTransition if not allowed."""
    if not is_allowed(from_state, to_state):
        raise InvalidTransition(from_state, to_state)
    return to_state


def is_terminal(state: TransactionState) -> bool:
    """Terminal states cannot transition further."""
    return len(ALLOWED_TRANSITIONS.get(state, set())) == 0


def classify_webhook_outcome(webhook_status: str, current_state: TransactionState) -> TransactionState:
    """Map a webhook event to the correct next state.

    Returns the state to transition to, or raises InvalidTransition.
    """
    webhook_status = webhook_status.lower()
    if webhook_status in ("captured", "paid", "succeeded", "success"):
        target = TransactionState.SUCCESS
    elif webhook_status in ("failed", "declined"):
        target = TransactionState.FAILED
    elif webhook_status in ("timeout", "lost", "no_response"):
        target = TransactionState.UNKNOWN
    elif webhook_status in ("refunded",):
        target = TransactionState.SUCCESS  # refund is final
    else:
        target = TransactionState.HOLD  # unknown webhook → manual review

    transition(current_state, target)
    return target