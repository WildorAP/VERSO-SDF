"""
SEP-24 transaction status polling for the more_info receipt page.
"""

from __future__ import annotations

from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from polaris.models import Transaction
from polaris.shared.serializers import TransactionSerializer
from rest_framework.request import Request

from verso_integrations.root import stellar_expert_tx_url

TERMINAL_TRANSACTION_STATUSES = frozenset(
    {
        Transaction.STATUS.completed,
        Transaction.STATUS.error,
        Transaction.STATUS.no_market,
        Transaction.STATUS.too_small,
        Transaction.STATUS.too_large,
    }
)


def transaction_poll(request: HttpRequest) -> JsonResponse:
    """Return live SEP-24 transaction fields for the more_info auto-refresh UI."""
    transaction_id = request.GET.get("id")
    if not transaction_id:
        return JsonResponse({"error": "id is required."}, status=400)

    transaction = get_object_or_404(
        Transaction,
        id=transaction_id,
        protocol=Transaction.PROTOCOL.sep24,
    )
    drf_request = Request(request)
    serializer = TransactionSerializer(
        transaction,
        context={"request": drf_request, "sep6": False},
    )

    stellar_tx_id = transaction.stellar_transaction_id or ""

    return JsonResponse(
        {
            "status": str(transaction.status),
            "message": transaction.message or "",
            "completed_at": (
                transaction.completed_at.isoformat() if transaction.completed_at else None
            ),
            "completed_at_display": (
                timezone.localtime(transaction.completed_at).strftime("%d/%m/%Y · %H:%M")
                if transaction.completed_at
                else ""
            ),
            "stellar_transaction_id": stellar_tx_id,
            "stellar_tx_url": stellar_expert_tx_url(stellar_tx_id),
            "terminal": transaction.status in TERMINAL_TRANSACTION_STATUSES,
            "transaction": serializer.data,
        }
    )
