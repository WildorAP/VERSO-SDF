"""Redirect SEP-24 webview to unified onboarding until KYC gate passes."""

from __future__ import annotations

from urllib.parse import urlencode

from django.shortcuts import redirect
from django.urls import reverse
from polaris.models import Transaction

from verso_integrations.models import Sep24DepositMeta, Sep24WithdrawMeta
from verso_integrations.sep24.kyc_gate import onboarding_step, sync_fiat_currency_from_request
from verso_integrations.sep24.onboarding_flow import operate_step_for_transaction
from verso_integrations.sep24.wallet_callbacks import (
    append_wallet_callbacks_to_url,
    persist_sep24_wallet_callbacks,
    sep24_wallet_callback_query,
)

WEBAPP_PATHS: dict[str, tuple] = {
    "/sep24/transactions/deposit/webapp": (
        Transaction.KIND.deposit,
        Sep24DepositMeta,
    ),
    "/sep24/transactions/withdraw/webapp": (
        Transaction.KIND.withdrawal,
        Sep24WithdrawMeta,
    ),
}


class Sep24OnboardingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        for path_prefix, (kind, meta_model) in WEBAPP_PATHS.items():
            if not request.path.startswith(path_prefix):
                continue

            transaction_id = request.GET.get("transaction_id")
            if not transaction_id:
                break

            try:
                transaction = Transaction.objects.get(
                    id=transaction_id,
                    kind=kind,
                    protocol=Transaction.PROTOCOL.sep24,
                )
            except Transaction.DoesNotExist:
                return self.get_response(request)

            persist_sep24_wallet_callbacks(request, transaction_id)

            if kind == Transaction.KIND.withdrawal:
                meta = Sep24WithdrawMeta.objects.filter(transaction=transaction).first()
                if (
                    meta
                    and meta.payout_confirmed_at
                    and transaction.status != Transaction.STATUS.completed
                ):
                    params = {
                        "id": str(transaction.id),
                        "initialLoad": "true",
                        **sep24_wallet_callback_query(request, transaction_id),
                    }
                    return redirect(f"{reverse('more_info')}?{urlencode(params)}")

            if meta_model.objects.filter(transaction=transaction).exists():
                return self.get_response(request)

            sync_fiat_currency_from_request(request, transaction_id)

            step = onboarding_step(request, transaction)
            if step != operate_step_for_transaction(transaction):
                query = append_wallet_callbacks_to_url(
                    request,
                    transaction_id,
                    {"transaction_id": transaction_id},
                )
                return redirect(f"{reverse('sep24_onboarding')}?{query}")
            break

        return self.get_response(request)
