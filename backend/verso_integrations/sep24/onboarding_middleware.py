"""Redirect SEP-24 webview to unified onboarding until KYC gate passes."""

from __future__ import annotations

from urllib.parse import urlencode

from django.shortcuts import redirect
from django.urls import reverse
from polaris.models import Transaction

from verso_integrations.models import Sep24DepositMeta, Sep24WithdrawMeta
from verso_integrations.sep24.kyc_gate import onboarding_step, sync_fiat_currency_from_request
from verso_integrations.sep24.onboarding_flow import operate_step_for_transaction
from verso_integrations.sep24.withdraw_wallet import ensure_withdraw_receiving_details
from verso_integrations.sep24.wallet_callbacks import (
    append_wallet_callbacks_to_url,
    persist_sep24_wallet_callbacks,
    sep24_wallet_callback_query,
)

MORE_INFO_PATH_PREFIX = "/sep24/transaction/more_info"

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
        if request.method == "GET" and request.path.startswith(MORE_INFO_PATH_PREFIX):
            redirect_response = self._maybe_redirect_more_info_with_callbacks(request)
            if redirect_response is not None:
                return redirect_response

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

    def _maybe_redirect_more_info_with_callbacks(self, request):
        """
        Re-append wallet callback params on more_info when Polaris dropped them.

        Without ``callback=postMessage`` + ``initialLoad=true``, callback.js never
        postMessages the transaction to Demo Wallet and the user never signs USDC.
        """
        transaction_id = request.GET.get("id")
        if not transaction_id:
            return None

        try:
            transaction = Transaction.objects.get(
                id=transaction_id,
                protocol=Transaction.PROTOCOL.sep24,
            )
        except Transaction.DoesNotExist:
            return None

        persist_sep24_wallet_callbacks(request, transaction_id)

        if transaction.kind == Transaction.KIND.withdrawal:
            ensure_withdraw_receiving_details(transaction)
            transaction.refresh_from_db()

        callback_params = sep24_wallet_callback_query(
            request,
            transaction_id,
            transaction=transaction,
        )
        if not callback_params:
            return None

        current_callback = (request.GET.get("callback") or "").lower()
        if current_callback == "success":
            return None

        params = request.GET.copy()
        changed = False

        session_callback = callback_params.get("callback")
        if session_callback and request.GET.get("callback") != session_callback:
            params["callback"] = session_callback
            changed = True

        session_on_change = callback_params.get("on_change_callback")
        if session_on_change and request.GET.get("on_change_callback") != session_on_change:
            params["on_change_callback"] = session_on_change
            changed = True

        if transaction.kind == Transaction.KIND.withdrawal:
            meta = Sep24WithdrawMeta.objects.filter(transaction=transaction).first()
            awaiting_wallet = (
                meta
                and meta.payout_confirmed_at
                and transaction.status
                not in {
                    Transaction.STATUS.completed,
                    Transaction.STATUS.error,
                }
                and not (transaction.stellar_transaction_id or "").strip()
            )
            if awaiting_wallet and not request.GET.get("initialLoad"):
                params["initialLoad"] = "true"
                changed = True

        if not changed:
            return None

        return redirect(f"{reverse('more_info')}?{params.urlencode()}")
