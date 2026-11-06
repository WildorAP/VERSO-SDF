"""
Operator-facing stage of a SEP-24 operation, derived from the Polaris status and VERSO metadata.

Read-only: nothing here changes a transaction. The same rules are expressed twice — as a
function (admin column) and as a Q filter (admin list filter) — and tests keep them in sync.
"""

from __future__ import annotations

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Exists, OuterRef, Q, TextChoices
from polaris.models import Transaction

from verso_integrations.models import OperationHold

S = Transaction.STATUS

FAILED_STATUSES = (S.error, S.no_market, S.too_small, S.too_large)
DEPOSIT_SENDING_STATUSES = (S.pending_stellar, S.pending_trust)
WITHDRAW_RECEIVED_STATUSES = (S.pending_anchor, S.pending_external)


class Stage(TextChoices):
    INITIATED = "initiated", "Iniciada"
    RECEIVED = "received", "Pago recibido"
    RECEIVED_PENDING_PAYOUT = "received_pending_payout", "Pago recibido – falta transferir"
    SENDING = "sending", "Enviando"
    COMPLETED = "completed", "Completada"
    ERROR = "error", "Error"
    HELD = "held", "Retenida"
    REJECTED = "rejected", "Rechazada"
    REJECTED_PENDING_REFUND = "rejected_pending_refund", "Rechazada – por devolver"
    REFUNDED = "refunded", "Devuelta"


STAGE_COLORS = {
    Stage.INITIATED: "#b7791f",
    Stage.RECEIVED: "#2b6cb0",
    Stage.RECEIVED_PENDING_PAYOUT: "#2b6cb0",
    Stage.SENDING: "#6b46c1",
    Stage.COMPLETED: "#2f855a",
    Stage.ERROR: "#c53030",
    Stage.HELD: "#c05621",
    Stage.REJECTED: "#718096",
    Stage.REJECTED_PENDING_REFUND: "#c53030",
    Stage.REFUNDED: "#4a5568",
}

REJECTION_STAGES = (Stage.REJECTED, Stage.REJECTED_PENDING_REFUND, Stage.REFUNDED)
DEPOSIT_STAGES = (
    Stage.INITIATED,
    Stage.HELD,
    Stage.RECEIVED,
    Stage.SENDING,
    Stage.COMPLETED,
    Stage.ERROR,
    *REJECTION_STAGES,
)
WITHDRAW_STAGES = (
    Stage.INITIATED,
    Stage.HELD,
    Stage.RECEIVED_PENDING_PAYOUT,
    Stage.COMPLETED,
    Stage.ERROR,
    *REJECTION_STAGES,
)


def rejection_of(transaction):
    try:
        return transaction.verso_rejection
    except ObjectDoesNotExist:
        return None


def active_hold_of(transaction):
    return next((h for h in transaction.verso_holds.all() if h.released_at is None), None)


def _rejection_stage(rejection) -> Stage:
    if rejection.refunded_at is not None:
        return Stage.REFUNDED
    return Stage.REJECTED_PENDING_REFUND if rejection.funds_received else Stage.REJECTED


def _rejection_stage_q(stage: str) -> Q:
    rejected = Q(transaction__verso_rejection__isnull=False)
    refunded = Q(transaction__verso_rejection__refunded_at__isnull=False)
    funds = Q(transaction__verso_rejection__funds_received=True)
    if stage == Stage.REFUNDED:
        return rejected & refunded
    if stage == Stage.REJECTED_PENDING_REFUND:
        return rejected & funds & ~refunded
    return rejected & ~funds & ~refunded


def _active_hold_q() -> Q:
    return Q(Exists(OperationHold.objects.filter(transaction=OuterRef("transaction"), released_at__isnull=True)))


def deposit_stage(meta) -> Stage:
    rejection = rejection_of(meta.transaction)
    if rejection is not None:
        return _rejection_stage(rejection)
    status = meta.transaction.status
    if status in FAILED_STATUSES:
        return Stage.ERROR
    if status == S.completed:
        return Stage.COMPLETED
    if active_hold_of(meta.transaction):
        return Stage.HELD
    if status in DEPOSIT_SENDING_STATUSES:
        return Stage.SENDING
    if meta.fiat_confirmed_at is not None:
        return Stage.RECEIVED
    return Stage.INITIATED


def withdraw_stage(meta) -> Stage:
    rejection = rejection_of(meta.transaction)
    if rejection is not None:
        return _rejection_stage(rejection)
    status = meta.transaction.status
    if status in FAILED_STATUSES:
        return Stage.ERROR
    if status == S.completed:
        return Stage.COMPLETED
    if active_hold_of(meta.transaction):
        return Stage.HELD
    if status in WITHDRAW_RECEIVED_STATUSES:
        return Stage.RECEIVED_PENDING_PAYOUT
    return Stage.INITIATED


def deposit_stage_q(stage: str) -> Q:
    """Q on Sep24DepositMeta matching ``deposit_stage(meta) == stage``."""
    if stage in REJECTION_STAGES:
        return _rejection_stage_q(stage)
    not_rejected = Q(transaction__verso_rejection__isnull=True)
    failed = Q(transaction__status__in=FAILED_STATUSES)
    completed = Q(transaction__status=S.completed)
    held = _active_hold_q()
    sending = Q(transaction__status__in=DEPOSIT_SENDING_STATUSES)
    confirmed = Q(fiat_confirmed_at__isnull=False)
    if stage == Stage.ERROR:
        return not_rejected & failed
    if stage == Stage.COMPLETED:
        return not_rejected & completed
    if stage == Stage.HELD:
        return not_rejected & held & ~failed & ~completed
    if stage == Stage.SENDING:
        return not_rejected & sending & ~held
    if stage == Stage.RECEIVED:
        return not_rejected & confirmed & ~failed & ~completed & ~sending & ~held
    if stage == Stage.INITIATED:
        return not_rejected & ~confirmed & ~failed & ~completed & ~sending & ~held
    raise ValueError(f"Unknown deposit stage: {stage}")


def withdraw_stage_q(stage: str) -> Q:
    """Q on Sep24WithdrawMeta matching ``withdraw_stage(meta) == stage``."""
    if stage in REJECTION_STAGES:
        return _rejection_stage_q(stage)
    not_rejected = Q(transaction__verso_rejection__isnull=True)
    failed = Q(transaction__status__in=FAILED_STATUSES)
    completed = Q(transaction__status=S.completed)
    held = _active_hold_q()
    received = Q(transaction__status__in=WITHDRAW_RECEIVED_STATUSES)
    if stage == Stage.ERROR:
        return not_rejected & failed
    if stage == Stage.COMPLETED:
        return not_rejected & completed
    if stage == Stage.HELD:
        return not_rejected & held & ~failed & ~completed
    if stage == Stage.RECEIVED_PENDING_PAYOUT:
        return not_rejected & received & ~held
    if stage == Stage.INITIATED:
        return not_rejected & ~failed & ~completed & ~received & ~held
    raise ValueError(f"Unknown withdrawal stage: {stage}")
