"""
"Cambiar monto": discard a frozen SEP-24 quote and return to the amount step.

Only allowed before the client moves money (deposit: no voucher declared / fiat not
confirmed; withdrawal: payout bank not confirmed yet).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Optional

from django.db import transaction as db_transaction
from polaris.models import Transaction

from verso_integrations.sep24.kyc_gate import session_get, session_set

CHANGE_AMOUNT_FIELD = "change_amount"


def wants_change_amount(post_data) -> bool:
    return bool(post_data is not None and post_data.get(CHANGE_AMOUNT_FIELD))


def get_previous_amount(request, transaction_id) -> Optional[Decimal]:
    """Amount the client typed before going back, to prefill the amount form."""
    if request is None:
        return None
    raw = session_get(request, transaction_id, "previous_amount")
    if not isinstance(raw, str):
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def reset_to_amount_step(request, transaction: Transaction, meta, previous_amount: Decimal) -> None:
    """Delete the step meta + indicative quote and clear the transaction amounts."""
    if request is not None:
        session_set(request, transaction.id, "previous_amount", str(previous_amount))

    with db_transaction.atomic():
        quote = transaction.quote
        # Transaction.quote is on_delete=CASCADE: detach before deleting the quote.
        transaction.quote = None
        transaction.amount_in = None
        transaction.amount_expected = None
        transaction.amount_out = None
        transaction.amount_fee = None
        transaction.fee_asset = None
        transaction.status = Transaction.STATUS.incomplete
        transaction.save()
        meta.delete()
        if quote is not None:
            quote.delete()
