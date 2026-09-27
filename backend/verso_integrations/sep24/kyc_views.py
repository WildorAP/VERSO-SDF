"""
SEP-24 DIDIT redirect helpers (Etapa 4).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlencode

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from polaris.models import Transaction
from polaris.sep24.utils import interactive_url
from rest_framework.request import Request

from verso_integrations.core_client import CoreClientError, kyc_handoff, user_status
from verso_integrations.sep24.kyc_gate import (
    clear_wants_register,
    get_pending_verso_user,
    is_email_verified,
    is_session_kyc_approved,
    mark_session_kyc_approved,
    mark_wants_register,
    sync_kyc_from_core,
)


def _as_drf_request(request: HttpRequest) -> Request:
    return Request(request)


def _build_return_url(request: HttpRequest, transaction: Transaction) -> str:
    callback = reverse("sep24_kyc_callback")
    query = urlencode({"transaction_id": str(transaction.id)})
    return request.build_absolute_uri(f"{callback}?{query}")


def _webapp_url(request: HttpRequest, transaction: Transaction) -> str:
    drf_request = _as_drf_request(request)
    amount = transaction.amount_in or transaction.amount_out
    if amount is not None and amount <= Decimal("0"):
        amount = None
    url = interactive_url(
        request=drf_request,
        transaction_id=str(transaction.id),
        account=transaction.stellar_account,
        memo=transaction.account_memo,
        asset_code=transaction.asset.code,
        op_type="deposit",
        amount=amount,
        lang=None,
    )
    if not url:
        host = os.environ.get("HOST_URL", "http://localhost:8000").rstrip("/")
        return f"{host}/sep24/transactions/deposit/webapp?transaction_id={transaction.id}"
    return url


def _load_deposit_transaction(request: HttpRequest, transaction_id: str | None) -> Transaction:
    if not transaction_id:
        raise ValueError("transaction_id is required.")
    return get_object_or_404(
        Transaction,
        id=transaction_id,
        kind=Transaction.KIND.deposit,
        protocol=Transaction.PROTOCOL.sep24,
    )


def _onboarding_url(request: HttpRequest, transaction: Transaction) -> str:
    query = urlencode({"transaction_id": str(transaction.id)})
    return request.build_absolute_uri(f"{reverse('sep24_onboarding')}?{query}")


@dataclass(frozen=True)
class KycStepResult:
    redirect_to: str | None = None
    embed_url: str | None = None
    callback_url: str | None = None
    fallback_url: str | None = None
    error: str | None = None


def didit_embed_origins() -> list[str]:
    return list(
        getattr(
            settings,
            "DIDIT_EMBED_ORIGINS",
            [
                "https://verify.didit.me",
                "https://verification.didit.me",
            ],
        )
    )


def didit_embed_origins_json() -> str:
    return json.dumps(didit_embed_origins())


def prepare_kyc_step(request: HttpRequest, transaction: Transaction) -> KycStepResult:
    """Resolve DIDIT handoff for embed or redirect fallback."""
    user_id, _email = get_pending_verso_user(request, transaction.id)
    if not user_id:
        return KycStepResult(
            error="Completa registro y verificación de correo antes de iniciar KYC."
        )
    if not is_email_verified(request, transaction.id):
        return KycStepResult(error="Verifica tu correo antes de iniciar KYC.")

    return_url = _build_return_url(request, transaction)
    start_query = urlencode({"transaction_id": str(transaction.id)})
    fallback_url = request.build_absolute_uri(f"{reverse('sep24_kyc_start')}?{start_query}")

    try:
        handoff = kyc_handoff(user_id=user_id, return_url=return_url)
    except CoreClientError as exc:
        return KycStepResult(error=str(exc))

    if handoff.kyc_completed:
        mark_session_kyc_approved(request, transaction.id)
        return KycStepResult(redirect_to=_webapp_url(request, transaction))

    if not handoff.kyc_url:
        return KycStepResult(error="VERSO Core did not return a KYC URL.")

    if handoff.kyc_url == return_url and settings.LOCAL_MODE:
        mark_session_kyc_approved(request, transaction.id)
        return KycStepResult(redirect_to=_webapp_url(request, transaction))

    return KycStepResult(
        embed_url=handoff.kyc_url,
        callback_url=return_url,
        fallback_url=fallback_url,
    )


def kyc_start(request: HttpRequest) -> HttpResponse:
    try:
        transaction = _load_deposit_transaction(request, request.GET.get("transaction_id"))
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    result = prepare_kyc_step(request, transaction)
    if result.error:
        return HttpResponseBadRequest(result.error)
    if result.redirect_to:
        return redirect(result.redirect_to)
    if result.embed_url:
        return redirect(result.embed_url)
    return HttpResponseBadRequest("No se pudo iniciar la verificación DIDIT.")


def kyc_poll(request: HttpRequest) -> JsonResponse:
    """Poll VERSO Core for DIDIT completion (iframe postMessage is unreliable on mobile QR)."""
    try:
        transaction = _load_deposit_transaction(request, request.GET.get("transaction_id"))
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    if is_session_kyc_approved(request, transaction.id):
        return JsonResponse(
            {
                "status": "approved",
                "redirect": _webapp_url(request, transaction),
            }
        )

    _user_id, email = get_pending_verso_user(request, transaction.id)
    if not email:
        return JsonResponse(
            {"error": "Sesión de onboarding incompleta."},
            status=400,
        )

    try:
        status = user_status(email=email)
    except CoreClientError as exc:
        return JsonResponse({"error": str(exc)}, status=502)

    if status.kyc_completed:
        mark_session_kyc_approved(request, transaction.id)
        return JsonResponse(
            {
                "status": "approved",
                "redirect": _webapp_url(request, transaction),
            }
        )

    return JsonResponse({"status": "pending"})


def kyc_callback(request: HttpRequest) -> HttpResponse:
    try:
        transaction = _load_deposit_transaction(request, request.GET.get("transaction_id"))
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    _user_id, email = get_pending_verso_user(request, transaction.id)
    if not email:
        return HttpResponseBadRequest(
            "Sesión de onboarding incompleta. Vuelve a iniciar sesión en VERSO."
        )

    try:
        status = user_status(email=email)
    except CoreClientError as exc:
        return HttpResponseBadRequest(str(exc))

    if status.kyc_completed:
        mark_session_kyc_approved(request, transaction.id)
        return redirect(_webapp_url(request, transaction))

    sync_kyc_from_core(request, transaction.id)
    return redirect(_onboarding_url(request, transaction))


def onboarding_switch(request: HttpRequest) -> HttpResponse:
    """Toggle between login and register steps inside the SEP-24 webview."""
    transaction_id = request.GET.get("transaction_id")
    mode = (request.GET.get("mode") or "login").strip().lower()
    if not transaction_id:
        return HttpResponseBadRequest("transaction_id is required.")

    transaction = get_object_or_404(
        Transaction,
        id=transaction_id,
        kind=Transaction.KIND.deposit,
        protocol=Transaction.PROTOCOL.sep24,
    )
    if mode == "register":
        mark_wants_register(request, transaction.id)
    else:
        clear_wants_register(request, transaction.id)
    return redirect(_onboarding_url(request, transaction))
