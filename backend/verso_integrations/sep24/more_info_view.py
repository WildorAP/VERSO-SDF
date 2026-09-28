"""
SEP-24 more_info wrapper — ensure wallet callback params reach Polaris' template.

Polaris only passes ``callback`` to ``callback.js`` when it appears in the query
string. Wallets use ``postMessage``, but our onboarding flow often lands on
more_info with only ``initialLoad=true``. Inject defaults before rendering.
"""

from __future__ import annotations

from django.views.decorators.clickjacking import xframe_options_exempt
from polaris.models import Transaction
from polaris.shared import endpoints
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import TemplateHTMLRenderer
from rest_framework.request import Request
from rest_framework.response import Response

from verso_integrations.models import Sep24WithdrawMeta
from verso_integrations.sep24.withdraw_wallet import ensure_withdraw_receiving_details
from verso_integrations.sep24.wallet_callbacks import (
    inject_wallet_callbacks_into_query,
    persist_sep24_wallet_callbacks,
    withdraw_awaiting_wallet_payment,
)


@xframe_options_exempt
@api_view(["GET"])
@renderer_classes([TemplateHTMLRenderer])
def verso_more_info(request: Request) -> Response:
    transaction_id = request.query_params.get("id")
    if transaction_id:
        persist_sep24_wallet_callbacks(request, transaction_id)
        try:
            transaction = Transaction.objects.get(
                id=transaction_id,
                protocol=Transaction.PROTOCOL.sep24,
            )
        except Transaction.DoesNotExist:
            transaction = None
        if transaction is not None and transaction.kind == Transaction.KIND.withdrawal:
            meta = Sep24WithdrawMeta.objects.filter(transaction=transaction).first()
            ensure_withdraw_receiving_details(transaction)
            transaction.refresh_from_db()
            if withdraw_awaiting_wallet_payment(transaction, meta):
                inject_wallet_callbacks_into_query(request, transaction)

    return endpoints.more_info(request)
