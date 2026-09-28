"""
SEP-24 withdraw wallet handoff — receiving account + callback URL helpers.

Demo Wallet / Lobstr sign and send USDC only after ``more_info`` loads with
``initialLoad=true`` and ``callback=postMessage``. Polaris' final redirect after
the bank form often omits ``callback`` even when it was persisted in session
during onboarding.
"""

from __future__ import annotations

import logging

from django.http import HttpRequest
from django.urls import reverse
from polaris.integrations import registered_custody_integration as rci
from polaris.models import Transaction
from polaris.utils import validate_account_and_memo

from verso_integrations.models import Sep24WithdrawMeta
from verso_integrations.sep24.wallet_callbacks import append_wallet_callbacks_to_url

logger = logging.getLogger(__name__)


def build_withdraw_post_url(request: HttpRequest, transaction: Transaction) -> str:
    """POST target for withdraw webapp forms, always including wallet callbacks."""
    query = append_wallet_callbacks_to_url(
        request,
        str(transaction.id),
        {
            "transaction_id": str(transaction.id),
            "asset_code": transaction.asset.code,
        },
    )
    return request.build_absolute_uri(f"{reverse('post_interactive_withdraw')}?{query}")


def ensure_withdraw_receiving_details(transaction: Transaction) -> bool:
    """
    Assign anchor receiving account + memo when the interactive flow finished
    but Polaris did not persist them (partial state after bank confirm).
    """
    if transaction.kind != Transaction.KIND.withdrawal:
        return False
    if (transaction.receiving_anchor_account or "").strip():
        return False

    meta = Sep24WithdrawMeta.objects.filter(transaction=transaction).first()
    if meta is None or meta.payout_confirmed_at is None:
        return False

    if transaction.status == Transaction.STATUS.completed:
        return False

    try:
        receiving_account, memo, memo_type = validate_account_and_memo(
            *rci.get_receiving_account_and_memo(request=None, transaction=transaction)
        )
    except ValueError:
        logger.exception(
            "Could not assign receiving account for withdraw transaction %s",
            transaction.id,
        )
        return False

    transaction.status = Transaction.STATUS.pending_user_transfer_start
    transaction.receiving_anchor_account = receiving_account
    transaction.memo = memo
    transaction.memo_type = memo_type
    transaction.save(
        update_fields=[
            "status",
            "receiving_anchor_account",
            "memo",
            "memo_type",
        ]
    )
    return True
