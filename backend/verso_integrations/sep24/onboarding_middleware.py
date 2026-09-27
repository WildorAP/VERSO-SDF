"""Redirect SEP-24 webview to unified onboarding until KYC gate passes."""

from __future__ import annotations

from django.shortcuts import redirect
from django.urls import reverse
from polaris.models import Transaction

from verso_integrations.models import Sep24DepositMeta
from verso_integrations.sep24.kyc_gate import onboarding_step

WEBAPP_PATH = "/sep24/transactions/deposit/webapp"


class Sep24OnboardingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith(WEBAPP_PATH):
            transaction_id = request.GET.get("transaction_id")
            if transaction_id:
                try:
                    transaction = Transaction.objects.get(
                        id=transaction_id,
                        kind=Transaction.KIND.deposit,
                        protocol=Transaction.PROTOCOL.sep24,
                    )
                except Transaction.DoesNotExist:
                    return self.get_response(request)

                if Sep24DepositMeta.objects.filter(transaction=transaction).exists():
                    return self.get_response(request)

                step = onboarding_step(request, transaction)
                if step != "deposit":
                    query = f"transaction_id={transaction_id}"
                    return redirect(f"{reverse('sep24_onboarding')}?{query}")

        return self.get_response(request)
