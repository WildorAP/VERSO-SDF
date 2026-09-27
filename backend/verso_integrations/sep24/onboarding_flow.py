"""Shared SEP-24 onboarding steps for deposit and withdrawal interactive flows."""

from __future__ import annotations

from decimal import Decimal
from typing import Optional
from urllib.parse import urlencode

from django import forms
from django.conf import settings
from django.http import QueryDict
from django.urls import reverse
from polaris.models import Transaction
from rest_framework.request import Request

from verso_integrations.sep24.kyc_gate import (
    apply_verso_user_session,
    get_pending_verso_user,
    is_email_verified,
    lookup_client_kyc,
    mark_email_verified,
    mark_onboarding_login_flow,
    mark_onboarding_register_flow,
    mark_profile_completed,
    mark_session_kyc_approved,
    onboarding_step,
    set_pending_verso_user,
    sync_kyc_from_core,
)
from verso_integrations.sep24.onboarding_forms import (
    KycStatusForm,
    VersoLoginForm,
    VersoProfileForm,
    VersoRegisterForm,
    VersoVerifyEmailForm,
)


def operate_step_for_transaction(transaction: Transaction) -> str:
    if transaction.kind == Transaction.KIND.withdrawal:
        return "withdraw"
    return "deposit"


def onboarding_url(request: Request, transaction: Transaction) -> str:
    query = urlencode({"transaction_id": str(transaction.id)})
    return request.build_absolute_uri(f"{reverse('sep24_onboarding')}?{query}")


def onboarding_switch_url(request: Request, transaction: Transaction, mode: str) -> str:
    query = urlencode({"transaction_id": str(transaction.id), "mode": mode})
    return request.build_absolute_uri(f"{reverse('sep24_onboarding_switch')}?{query}")


def form_for_onboarding_step(
    step: str,
    post_data: Optional[QueryDict],
    *,
    request: Optional[Request] = None,
    transaction: Optional[Transaction] = None,
    amount: Optional[Decimal] = None,
) -> Optional[forms.Form]:
    form_kwargs = {"request": request, "transaction": transaction}
    if step == "login":
        if post_data is not None:
            return VersoLoginForm(post_data, **form_kwargs)
        return VersoLoginForm(**form_kwargs)
    if step == "register":
        if post_data is not None:
            return VersoRegisterForm(post_data, **form_kwargs)
        return VersoRegisterForm(**form_kwargs)
    if step == "verify_email":
        if post_data is not None:
            return VersoVerifyEmailForm(post_data, **form_kwargs)
        return VersoVerifyEmailForm(**form_kwargs)
    if step == "profile":
        if post_data is not None:
            return VersoProfileForm(post_data, **form_kwargs)
        return VersoProfileForm(**form_kwargs)
    if step in {"didit", "pending", "rejected"}:
        if post_data is not None:
            return KycStatusForm(post_data)
        return KycStatusForm()
    return None


def after_onboarding_form_validation(
    request: Request,
    form: forms.Form,
    transaction: Transaction,
) -> bool:
    """Handle onboarding form POST. Returns True when handled."""
    if isinstance(form, VersoLoginForm):
        if form.login_result is None:
            return True
        mark_onboarding_login_flow(request, transaction.id)
        apply_verso_user_session(request, transaction.id, form.login_result)
        return True
    if isinstance(form, VersoRegisterForm):
        if form.register_result is None:
            return True
        mark_onboarding_register_flow(request, transaction.id)
        set_pending_verso_user(
            request,
            transaction.id,
            user_id=form.register_result.user_id,
            email=form.register_result.email,
        )
        return True
    if isinstance(form, VersoVerifyEmailForm):
        mark_email_verified(request, transaction.id)
        if getattr(settings, "VERSO_MOCK_KYC_AUTO_APPROVE_AFTER_VERIFY", False):
            mark_session_kyc_approved(request, transaction.id)
            return True
        synced = sync_kyc_from_core(request, transaction.id)
        if synced and synced.status == "approved":
            mark_session_kyc_approved(request, transaction.id)
        return True
    if isinstance(form, VersoProfileForm):
        if form.profile_result is None:
            return True
        mark_profile_completed(request, transaction.id)
        return True
    return False


def content_for_onboarding_form(
    request: Request,
    form: forms.Form,
    transaction: Transaction,
    base: dict,
) -> Optional[dict]:
    if isinstance(form, VersoLoginForm):
        register_url = onboarding_switch_url(request, transaction, "register")
        base["title"] = "Inicia sesión en VERSO"
        base["guidance"] = (
            "Ingresa con tu cuenta VERSO para operar con la wallet "
            f"{transaction.stellar_account[:8]}… de esta sesión. "
            f'¿Aún no tienes cuenta? <a href="{register_url}">Crear cuenta</a>'
        )
        return base

    if isinstance(form, VersoRegisterForm):
        login_url = onboarding_switch_url(request, transaction, "login")
        base["title"] = "Crea tu cuenta VERSO"
        base["guidance"] = (
            "Regístrate para operar con VERSO. Usaremos la wallet "
            f"{transaction.stellar_account[:8]}… con la que abriste esta sesión. "
            f'¿Ya tienes cuenta? <a href="{login_url}">Iniciar sesión</a>'
        )
        return base

    if isinstance(form, VersoVerifyEmailForm):
        _user_id, email = get_pending_verso_user(request, transaction.id)
        base["title"] = "Verifica tu correo"
        base["guidance"] = f"Ingresa el código enviado a {email or 'tu correo'}."
        return base

    if isinstance(form, VersoProfileForm):
        base["title"] = "Completa tu perfil"
        base["guidance"] = (
            "Necesitamos algunos datos adicionales para cumplir con la normativa "
            "antes de continuar con la verificación de identidad."
        )
        return base

    if isinstance(form, KycStatusForm):
        step = onboarding_step(request, transaction)
        if step == "pending":
            base["title"] = "Verificación en revisión"
            base["guidance"] = (
                "Aún no tienes acceso para operar. Tu KYC está pendiente de aprobación."
            )
        elif step == "rejected":
            base["title"] = "Verificación no aprobada"
            base["guidance"] = (
                "No pudimos validar tu identidad. Escríbenos a soporte@versotek.io."
            )
        else:
            base["title"] = "Verificación de identidad"
            start_url = onboarding_url(request, transaction)
            base["guidance"] = (
                "Completa tu verificación KYC con VERSO (DIDIT) para continuar. "
                f'<a class="btn btn--inline" href="{start_url}">Continuar verificación</a>'
            )
        return base

    return None
