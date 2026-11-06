"""
Operator actions on a SEP-24 operation: what each stage allows, and how each action is applied.

Polaris has no rejected or held statuses: a rejection is ``error`` plus an ``OperationRejection``
row, and a hold leaves the Polaris status untouched and only stops the payout.
"""

from __future__ import annotations

import re

from django.db import transaction as db_transaction
from django.utils import timezone
from polaris.models import Transaction
from polaris.utils import maybe_make_callback

from verso_integrations.models import OperationHold, OperationRejection, Sep24DepositMeta
from verso_integrations.operation_status import (
    Stage,
    active_hold_of,
    deposit_stage,
    rejection_of,
    withdraw_stage,
)
from verso_integrations.withdraw import WithdrawalUsdcNotReceivedError, usdc_payment_received

CONFIRM = "confirm"
PAY = "pay"
REJECT = "reject"
HOLD = "hold"
REFUND = "refund"

ACTION_LABELS = {
    CONFIRM: "Confirmar y enviar",
    PAY: "Marcar pagado",
    REJECT: "Rechazar",
    HOLD: "Retener",
    REFUND: "Registrar devolución",
}

DEPOSIT_ACTIONS = {
    Stage.INITIATED: (CONFIRM, REJECT, HOLD),
    Stage.HELD: (CONFIRM, REJECT),
    Stage.ERROR: (REJECT,),
    Stage.REJECTED_PENDING_REFUND: (REFUND,),
}
WITHDRAW_ACTIONS = {
    Stage.INITIATED: (REJECT, HOLD),
    Stage.RECEIVED_PENDING_PAYOUT: (PAY, REJECT, HOLD),
    Stage.HELD: (PAY, REJECT),
    Stage.ERROR: (REJECT,),
    Stage.REJECTED_PENDING_REFUND: (REFUND,),
}

# The same text for every reason: a client under review must not learn why.
REJECTED_MESSAGE = (
    "Tu operación fue rechazada y no continuará. No envíes fondos. Si ya los enviaste, "
    "te los devolveremos. Escríbenos a soporte@versotek.io."
)
HOLD_MESSAGE = "Tu operación está en revisión."

STELLAR_HASH = re.compile(r"[0-9a-fA-F]{64}")


class OperationError(Exception):
    """The action cannot be applied; the message is shown to the operator."""


def is_deposit(meta) -> bool:
    return isinstance(meta, Sep24DepositMeta)


def stage_of(meta) -> Stage:
    return deposit_stage(meta) if is_deposit(meta) else withdraw_stage(meta)


def allowed_actions(meta) -> tuple[str, ...]:
    actions = DEPOSIT_ACTIONS if is_deposit(meta) else WITHDRAW_ACTIONS
    return actions.get(stage_of(meta), ())


def customer_message(transaction) -> str | None:
    """Text for the client when an operator rejected or is holding the operation."""
    if rejection_of(transaction) is not None:
        return REJECTED_MESSAGE
    if transaction.status != Transaction.STATUS.completed and active_hold_of(transaction):
        return HOLD_MESSAGE
    return None


def _locked(meta, action: str):
    """Re-read under a row lock, so the stage cannot change between the check and the action."""
    Transaction.objects.select_for_update().get(pk=meta.transaction_id)
    meta = type(meta).objects.select_related("transaction").get(pk=meta.pk)
    if action not in allowed_actions(meta):
        raise OperationError(
            f"Operación #{meta.pk}: «{ACTION_LABELS[action]}» no está disponible "
            f"en la etapa {stage_of(meta).label}."
        )
    return meta


def _release_holds(transaction, user) -> None:
    transaction.verso_holds.filter(released_at__isnull=True).update(
        released_at=timezone.now(), released_by=user
    )


def confirm_deposit(meta, user) -> None:
    """Fiat arrived: the deposit worker sends the USDC on its next pass."""
    with db_transaction.atomic():
        meta = _locked(meta, CONFIRM)
        if meta.transfer_declared_at is None:
            raise OperationError(f"Operación #{meta.pk}: el cliente no ha declarado la transferencia.")
        if meta.transaction.status != Transaction.STATUS.pending_user_transfer_start:
            raise OperationError(
                f"Operación #{meta.pk}: no está esperando el pago del cliente "
                f"(estado {meta.transaction.status})."
            )
        _release_holds(meta.transaction, user)
        meta.mark_fiat_confirmed()


def pay_withdrawal(meta, user) -> None:
    """The bank transfer was made: the withdrawal is completed."""
    with db_transaction.atomic():
        meta = _locked(meta, PAY)
        try:
            meta.mark_fiat_sent()
        except WithdrawalUsdcNotReceivedError as exc:
            raise OperationError(f"Operación #{meta.pk}: {exc}") from exc
        _release_holds(meta.transaction, user)


def reject(meta, user, *, reason: str, note: str = "", funds_received: bool = False) -> OperationRejection:
    """
    Close the operation. ``funds_received`` is only read for deposits not yet confirmed, because
    the anchor cannot see the bank; withdrawals know from the on-chain payment.
    """
    with db_transaction.atomic():
        meta = _locked(meta, REJECT)
        tx = meta.transaction
        if is_deposit(meta):
            if (tx.stellar_transaction_id or "").strip():
                raise OperationError(f"Operación #{meta.pk}: el USDC ya salió en la red.")
            funds = bool(meta.fiat_confirmed_at) or funds_received
        else:
            if meta.fiat_sent_at:
                raise OperationError(f"Operación #{meta.pk}: ya figura pagada por el banco.")
            funds = usdc_payment_received(tx)
        rejection = OperationRejection.objects.create(
            transaction=tx, reason=reason, note=note, funds_received=funds, rejected_by=user
        )
        _release_holds(tx, user)
        tx.status = Transaction.STATUS.error
        tx.status_message = f"Rejected by operator: {reason}"
        tx.save(update_fields=["status", "status_message"])
    maybe_make_callback(tx)
    return rejection


def hold(meta, user, *, note: str) -> OperationHold:
    """Stop the payout while the operation is reviewed; the Polaris status does not change."""
    if not note.strip():
        raise OperationError("Indica el motivo de la retención.")
    with db_transaction.atomic():
        meta = _locked(meta, HOLD)
        return OperationHold.objects.create(transaction=meta.transaction, note=note, placed_by=user)


def register_refund(meta, user, *, amount, reference: str) -> None:
    """Record a refund made outside the anchor; this does not move money."""
    reference = reference.strip()
    if amount is None or amount <= 0:
        raise OperationError("El monto devuelto debe ser mayor que cero.")
    if not reference:
        raise OperationError("Indica la referencia de la devolución.")
    if not is_deposit(meta) and not STELLAR_HASH.fullmatch(reference):
        raise OperationError("La referencia debe ser el hash de la transacción en la red (64 caracteres).")
    with db_transaction.atomic():
        meta = _locked(meta, REFUND)
        tx = meta.transaction
        rejection = tx.verso_rejection
        rejection.refund_amount = amount
        rejection.refund_reference = reference
        rejection.refunded_at = timezone.now()
        rejection.refunded_by = user
        rejection.save(
            update_fields=["refund_amount", "refund_reference", "refunded_at", "refunded_by"]
        )
        tx.refunded = True
        tx.save(update_fields=["refunded"])
