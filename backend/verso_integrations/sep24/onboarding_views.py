"""
Unified SEP-24 onboarding UI — login/register, email verify, DIDIT prompt.

All steps share templates/sep24/onboarding/_base.html with a single progress rail.
Deposit (PEN amount) stays in the Polaris webview after onboarding completes.
"""

from __future__ import annotations

from urllib.parse import urlencode

from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from polaris.models import Transaction
from rest_framework.request import Request

from verso_integrations.models import Sep24DepositMeta, Sep24WithdrawMeta
from verso_integrations.sep24.kyc_gate import (
    get_pending_verso_user,
    is_register_onboarding_flow,
    mark_onboarding_register_flow,
    onboarding_step,
)
from verso_integrations.sep24.kyc_views import (
    _webapp_url,
    didit_embed_origins_json,
    prepare_kyc_step,
)
from verso_integrations.sep24.onboarding_flow import (
    after_onboarding_form_validation,
    operate_step_for_transaction,
)

from verso_integrations.sep24.onboarding_forms import (
    VersoLoginForm,
    VersoProfileForm,
    VersoRegisterForm,
    VersoVerifyEmailForm,
)

STEP_NUMBERS = {
    "login": 1,
    "register": 1,
    "verify_email": 2,
    "profile": 2,
    "didit": 3,
    "pending": 3,
    "rejected": 3,
}

STEP_TITLES = {
    1: "Formulario",
    2: "Validación de correo",
    3: "Verificación DIDIT",
}


def onboarding_url(request: HttpRequest, transaction: Transaction) -> str:
    query = urlencode({"transaction_id": str(transaction.id)})
    return request.build_absolute_uri(f"{reverse('sep24_onboarding')}?{query}")


def _as_drf_request(request: HttpRequest) -> Request:
    return Request(request)


def _load_transaction(request: HttpRequest) -> Transaction:
    transaction_id = request.GET.get("transaction_id")
    if not transaction_id:
        raise ValueError("transaction_id is required.")
    return get_object_or_404(
        Transaction,
        id=transaction_id,
        protocol=Transaction.PROTOCOL.sep24,
    )


def _has_operate_meta(transaction: Transaction) -> bool:
    if transaction.kind == Transaction.KIND.deposit:
        return Sep24DepositMeta.objects.filter(transaction=transaction).exists()
    if transaction.kind == Transaction.KIND.withdrawal:
        return Sep24WithdrawMeta.objects.filter(transaction=transaction).exists()
    return False


def _switch_url(request: HttpRequest, transaction: Transaction, mode: str) -> str:
    query = urlencode({"transaction_id": str(transaction.id), "mode": mode})
    return request.build_absolute_uri(f"{reverse('sep24_onboarding_switch')}?{query}")


def sep24_onboarding(request: HttpRequest) -> HttpResponse:
    try:
        transaction = _load_transaction(request)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    if _has_operate_meta(transaction):
        return redirect(_webapp_url(request, transaction))

    step = onboarding_step(request, transaction)
    if step == operate_step_for_transaction(transaction):
        return redirect(_webapp_url(request, transaction))

    drf_request = _as_drf_request(request)
    error = None
    profile_form = None

    if request.method == "POST":
        if step == "login":
            form = VersoLoginForm(request.POST, request=drf_request, transaction=transaction)
            if form.is_valid():
                after_onboarding_form_validation(drf_request, form, transaction)
                return redirect(onboarding_url(request, transaction))
            error = _first_form_error(form)
        elif step == "register":
            form = VersoRegisterForm(request.POST, request=drf_request, transaction=transaction)
            if form.is_valid():
                after_onboarding_form_validation(drf_request, form, transaction)
                return redirect(onboarding_url(request, transaction))
            error = _first_form_error(form)
        elif step == "verify_email":
            form = VersoVerifyEmailForm(request.POST, request=drf_request, transaction=transaction)
            if form.is_valid():
                after_onboarding_form_validation(drf_request, form, transaction)
                return redirect(onboarding_url(request, transaction))
            error = _first_form_error(form)
        elif step == "profile":
            profile_form = VersoProfileForm(request.POST, request=drf_request, transaction=transaction)
            if profile_form.is_valid():
                after_onboarding_form_validation(drf_request, profile_form, transaction)
                return redirect(onboarding_url(request, transaction))
            error = _first_form_error(profile_form)

    return _render_step(request, transaction, step, error=error, profile_form=profile_form)


def _first_form_error(form) -> str:
    if form.non_field_errors():
        return str(form.non_field_errors()[0])
    for field in form:
        if field.errors:
            return str(field.errors[0])
    return "Revisa los datos e intenta de nuevo."


def _render_step(
    request: HttpRequest,
    transaction: Transaction,
    step: str,
    *,
    error: str | None = None,
    profile_form: VersoProfileForm | None = None,
) -> HttpResponse:
    step_number = STEP_NUMBERS.get(step, 1)
    wallet_short = f"{transaction.stellar_account[:8]}…"
    if step == "register":
        mark_onboarding_register_flow(request, transaction.id)
    show_timeline = step != "login" and is_register_onboarding_flow(request, transaction.id)
    context = {
        "step": step_number,
        "step_title": STEP_TITLES.get(step_number, ""),
        "show_rail": True,
        "show_timeline": show_timeline,
        "error": error,
        "transaction": transaction,
        "wallet_short": wallet_short,
        "webapp_url": _webapp_url(request, transaction),
        "register_url": _switch_url(request, transaction, "register"),
        "login_url": _switch_url(request, transaction, "login"),
    }

    if step == "login":
        return render(request, "sep24/onboarding/login.html", context)
    if step == "register":
        return render(request, "sep24/onboarding/register.html", context)
    if step == "verify_email":
        _user_id, email = get_pending_verso_user(request, transaction.id)
        context["email"] = email or ""
        return render(request, "sep24/onboarding/verify_email.html", context)
    if step == "profile":
        context["show_timeline"] = False
        if profile_form is None:
            profile_form = VersoProfileForm(
                request=_as_drf_request(request),
                transaction=transaction,
            )
        context["form"] = profile_form
        return render(request, "sep24/onboarding/profile_wizard.html", context)
    if step == "didit":
        _user_id, email = get_pending_verso_user(request, transaction.id)
        context["email"] = email or ""
        context["content_wide"] = True
        kyc = prepare_kyc_step(request, transaction)
        if kyc.redirect_to:
            return redirect(kyc.redirect_to)
        if kyc.error:
            context["kyc_error"] = kyc.error
        else:
            context["kyc_embed_url"] = kyc.embed_url
            context["kyc_callback_url"] = kyc.callback_url
            context["kyc_fallback_url"] = kyc.fallback_url
            poll_query = urlencode({"transaction_id": str(transaction.id)})
            context["kyc_poll_url"] = request.build_absolute_uri(
                f"{reverse('sep24_kyc_poll')}?{poll_query}"
            )
            context["didit_origins_json"] = didit_embed_origins_json()
        return render(request, "sep24/onboarding/didit.html", context)
    if step == "pending":
        context["status_message"] = (
            "Tu verificación KYC está en revisión. Te avisaremos cuando puedas operar."
        )
        return render(request, "sep24/onboarding/status.html", context)
    if step == "rejected":
        context["status_message"] = (
            "No pudimos validar tu identidad. Escríbenos a soporte@versotek.io."
        )
        return render(request, "sep24/onboarding/status.html", context)

    return redirect(onboarding_url(request, transaction))
