"""Normalized transaction schema.

All sources (UPI, Card, Bank Transfer, Subscription, Invoice) converge on
this schema before any matching or rule logic runs. Amounts are stored
in integer paise to avoid float drift. Timestamps are UTC ISO 8601.
"""
from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class Source(str, Enum):
    UPI = "upi"
    CARD = "card"
    BANK_TRANSFER = "bank_transfer"
    SUBSCRIPTION = "subscription"
    INVOICE = "invoice"


class TxnDirection(str, Enum):
    CREDIT = "credit"   # money in
    DEBIT = "debit"     # money out (refund, fee)


class TransactionState(str, Enum):
    INITIATED = "initiated"
    PENDING = "pending"
    UNKNOWN = "unknown"
    RECONCILING = "reconciling"
    SUCCESS = "success"
    FAILED = "failed"
    HOLD = "hold"


class MatchSource(str, Enum):
    RULE = "rule"
    PROBABILISTIC = "probabilistic"


class NormalizedTxn(BaseModel):
    """One financial record from one source, normalized."""
    txn_id: str = Field(..., description="Unique within source + global")
    source: Source
    direction: TxnDirection
    amount_paise: int = Field(..., ge=0, description="Amount in integer paise")
    currency: str = Field(default="INR", min_length=3, max_length=3)
    timestamp: datetime = Field(..., description="UTC ISO 8601")
    reference: Optional[str] = Field(
        None, description="External reference (invoice #, mandate id, UPI txn ref)"
    )
    counterparty: Optional[str] = Field(
        None, description="Customer/payee identifier (masked)"
    )
    state: TransactionState = TransactionState.PENDING
    raw_payload: dict = Field(
        default_factory=dict, description="Original source payload, never used for decisions"
    )

    @field_validator("amount_paise")
    @classmethod
    def _amount_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("amount_paise must be non-negative")
        return v


class MatchDecision(BaseModel):
    """Output of rule/match engine for one pairing."""
    txn_id_a: str
    txn_id_b: str
    matched: bool
    source: MatchSource
    reasons: list[str] = Field(default_factory=list)
    score: float = 0.0
    score_breakdown: dict = Field(default_factory=dict)


class ExceptionReason(str, Enum):
    AMOUNT_MISMATCH = "amount_mismatch"
    MISSING_REFERENCE = "missing_reference"
    TIMING_WINDOW = "timing_window"
    DUPLICATE_FIRE = "duplicate_fire"
    MISSING_TXN = "missing_txn"
    AMBIGUOUS = "ambiguous"
    STANDALONE_RECURRING = "standalone_recurring"  # subscription records, no counterpart needed


class ExceptionRecord(BaseModel):
    """One exception the engine could not auto-match."""
    txn_id: str
    source: Source
    amount_paise: int
    reason: ExceptionReason
    reference: Optional[str] = None
    timestamp: datetime
    auto_recoverable: bool = False
    needs_human: bool = True
    detail: str = ""


class DiagnosisClass(str, Enum):
    """LLM-returned classification for ambiguous exceptions."""
    AMBIGUOUS_AMOUNT = "ambiguous_amount"
    AMBIGUOUS_REFERENCE = "ambiguous_reference"
    TIMING_OUT_OF_BOUNDS = "timing_out_of_bounds"
    CURRENCY_DRIFT = "currency_drift"
    DATA_INCOMPLETE = "data_incomplete"
    DUPLICATE_SUSPECTED = "duplicate_suspected"
    RECOVERY_POSSIBLE = "recovery_possible"
    RECOVERY_NOT_POSSIBLE = "recovery_not_possible"
    REFUSED = "refused"


class Diagnosis(BaseModel):
    """LLM output for one ambiguous exception."""
    txn_id: str
    diagnosis_class: DiagnosisClass
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)
    refused: bool = False
    reasoning: str = ""

    @field_validator("refused")
    @classmethod
    def _refused_consistency(cls, v: bool, info) -> bool:
        """Enforce: REFUSED class ↔ refused=True.
        Other classes ↔ refused=False. Don't override caller intent for
        non-REFUSED classes."""
        # If caller didn't set refused (default False), keep False
        cls_value = info.data.get("diagnosis_class")
        if cls_value == DiagnosisClass.REFUSED:
            return True  # REFUSED class always refused
        # For any other class, the validator that follows will check confidence
        return v


class RecoveryAction(BaseModel):
    """A proposed recovery action for a recoverable exception."""
    txn_id: str
    action_type: str  # "lookup_subscription", "propose_payment_link", "mark_fee_drift"
    description: str
    idempotency_key: str
    requires_human_approval: bool = True
    executed: bool = False