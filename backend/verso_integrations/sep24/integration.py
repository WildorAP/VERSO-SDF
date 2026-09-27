"""
SEP-24 deposit integration — PEN on-ramp MVP (Etapa 3) + onboarding/KYC gate (Etapa 4).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from django import forms
from django.conf import settings
from django.http import QueryDict
from django.urls import reverse
from polaris.integrations import DepositIntegration
from polaris.models import DeliveryMethod, Quote, Transaction
from polaris.templates import Template
from rest_framework.request import Request
from urllib.parse import urlencode

from verso_integrations.deposit import compute_amount_usdc, get_cci_deposit_instructions
from verso_integrations.models import Sep24DepositMeta
from verso_integrations.polaris_setup import (
    DELIVERY_PEN_SELL,
    pen_asset_identification,
    usdc_asset_identification,
)
from verso_integrations.rates import get_pen_usdc_rate
from verso_integrations.sep24.forms import PenDepositForm
from verso_integrations.sep24.kyc_gate import (
    apply_verso_user_session,
    get_pending_verso_user,
    is_email_verified,
    lookup_client_kyc,
    mark_email_verified,
    mark_onboarding_login_flow,
    mark_onboarding_register_flow,
    mark_session_kyc_approved,
    onboarding_step,
    set_pending_verso_user,
    sync_kyc_from_core,
)
from verso_integrations.sep24.onboarding_forms import (
    DiditPromptForm,
    KycStatusForm,
    VersoLoginForm,
    VersoRegisterForm,
    VersoVerifyEmailForm,
)


def _format_bank_guidance(instructions: dict) -> str:
    return (
        f"Transfiere {instructions['amount_pen']} PEN a {instructions['bank_name']} "
        f"cuenta {instructions['account_number']}. "
        f"Referencia: {instructions['reference']}. "
        f"Recibirás {instructions['amount_usdc']} USDC @ "
        f"{instructions['tipo_cambio_pen_per_usdc']} PEN/USDC."
    )


def _onboarding_url(request: Request, transaction: Transaction) -> str:
    query = urlencode({"transaction_id": str(transaction.id)})
    path = reverse("sep24_onboarding")
    return request.build_absolute_uri(f"{path}?{query}")


def _onboarding_switch_url(request: Request, transaction: Transaction, mode: str) -> str:
    query = urlencode({"transaction_id": str(transaction.id), "mode": mode})
    path = reverse("sep24_onboarding_switch")
    return request.build_absolute_uri(f"{path}?{query}")


class VersoDepositIntegration(DepositIntegration):
    """Interactive SEP-24 on-ramp: onboarding/KYC → PEN (CCI/CCE) → USDC."""

    def after_deposit(self, transaction: Transaction, *args, **kwargs):
        return None

    def _form_for_step(
        self,
        step: str,
        post_data: Optional[QueryDict],
        amount: Optional[Decimal],
        request: Optional[Request] = None,
        transaction: Optional[Transaction] = None,
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
        if step in {"didit", "pending", "rejected"}:
            if post_data is not None:
                return KycStatusForm(post_data)
            return KycStatusForm()
        if step == "deposit":
            initial = {}
            if amount is not None:
                initial["amount_pen"] = amount
            if post_data is not None:
                return PenDepositForm(post_data)
            return PenDepositForm(initial=initial or None)
        return None

    def form_for_transaction(
        self,
        request: Request,
        transaction: Transaction,
        post_data: Optional[QueryDict] = None,
        amount: Optional[Decimal] = None,
        *args,
        **kwargs,
    ) -> Optional[forms.Form]:
        if Sep24DepositMeta.objects.filter(transaction=transaction).exists():
            return None

        step = onboarding_step(request, transaction)
        return self._form_for_step(step, post_data, amount, request, transaction)

    def after_form_validation(
        self,
        request: Request,
        form: forms.Form,
        transaction: Transaction,
        *args,
        **kwargs,
    ):
        if isinstance(form, VersoLoginForm):
            if form.login_result is None:
                return
            mark_onboarding_login_flow(request, transaction.id)
            apply_verso_user_session(request, transaction.id, form.login_result)
            return
        if isinstance(form, VersoRegisterForm):
            if form.register_result is None:
                return
            mark_onboarding_register_flow(request, transaction.id)
            set_pending_verso_user(
                request,
                transaction.id,
                user_id=form.register_result.user_id,
                email=form.register_result.email,
            )
            return
        if isinstance(form, VersoVerifyEmailForm):
            mark_email_verified(request, transaction.id)

            if getattr(settings, "VERSO_MOCK_KYC_AUTO_APPROVE_AFTER_VERIFY", False):
                mark_session_kyc_approved(request, transaction.id)
                return

            synced = sync_kyc_from_core(request, transaction.id)
            if synced and synced.status == "approved":
                mark_session_kyc_approved(request, transaction.id)
            return
        if isinstance(form, PenDepositForm):
            self._handle_pen_deposit(request, form, transaction)

    def _handle_pen_deposit(
        self,
        request: Request,
        form: PenDepositForm,
        transaction: Transaction,
    ) -> None:
        kyc = lookup_client_kyc(
            transaction.stellar_account,
            request=request,
            transaction_id=transaction.id,
        )
        if kyc.status != "approved":
            form.add_error(
                None,
                "Debes completar la verificación VERSO antes de continuar.",
            )
            return

        rate = get_pen_usdc_rate()
        amount_pen = form.cleaned_data["amount_pen"].quantize(Decimal("0.01"))
        amount_usdc = compute_amount_usdc(amount_pen, rate.rate_venta)

        pen_id = pen_asset_identification()
        usdc_id = usdc_asset_identification()
        sell_method = DeliveryMethod.objects.get(
            name=DELIVERY_PEN_SELL,
            type=DeliveryMethod.TYPE.sell,
        )

        quote = Quote.objects.create(
            type=Quote.TYPE.indicative,
            stellar_account=transaction.stellar_account,
            muxed_account=transaction.muxed_account,
            account_memo=transaction.account_memo,
            sell_asset=pen_id,
            buy_asset=usdc_id,
            sell_amount=amount_pen,
            buy_amount=amount_usdc,
            price=rate.rate_venta.quantize(Decimal("0.01")),
            sell_delivery_method=sell_method,
        )

        instructions = get_cci_deposit_instructions(
            float(amount_pen),
            str(transaction.id),
            tipo_cambio=float(rate.rate_venta),
            amount_usdc=float(amount_usdc),
        )

        Sep24DepositMeta.objects.create(
            transaction=transaction,
            amount_pen=amount_pen,
            tipo_cambio=rate.rate_venta,
            amount_usdc=amount_usdc,
            sell_asset=pen_id,
            buy_asset=usdc_id,
            bank_instructions=instructions,
        )

        transaction.quote = quote
        transaction.amount_in = amount_pen
        transaction.amount_expected = amount_pen
        transaction.amount_out = amount_usdc
        transaction.amount_fee = Decimal("0")
        transaction.fee_asset = pen_id
        transaction.to_address = transaction.stellar_account
        transaction.status = Transaction.STATUS.pending_user_transfer_start
        transaction.save()

        if getattr(settings, "VERSO_MOCK_AUTO_CONFIRM_FIAT", False):
            Sep24DepositMeta.objects.get(transaction=transaction).mark_fiat_confirmed()

    def content_for_template(
        self,
        request: Request,
        template: Template,
        form: Optional[forms.Form] = None,
        transaction: Optional[Transaction] = None,
        *args,
        **kwargs,
    ) -> Optional[dict]:
        base = {
            "icon_label": "VERSO",
            "icon_path": "sep24/img/verso-logo.png",
            "title": "Depósito PEN → USDC",
        }

        if template == Template.MORE_INFO and transaction is not None:
            meta = Sep24DepositMeta.objects.filter(transaction=transaction).first()
            if meta:
                base["guidance"] = _format_bank_guidance(meta.bank_instructions)
                base["bank_instructions"] = meta.bank_instructions
            return base

        if transaction is None or form is None:
            return None

        if isinstance(form, VersoLoginForm):
            register_url = _onboarding_switch_url(request, transaction, "register")
            base["title"] = "Inicia sesión en VERSO"
            base["guidance"] = (
                "Ingresa con tu cuenta VERSO para operar PEN → USDC con la wallet "
                f"{transaction.stellar_account[:8]}… de esta sesión. "
                f'¿Aún no tienes cuenta? <a href="{register_url}">Crear cuenta</a>'
            )
            return base

        if isinstance(form, VersoRegisterForm):
            login_url = _onboarding_switch_url(request, transaction, "login")
            base["title"] = "Crea tu cuenta VERSO"
            base["guidance"] = (
                "Regístrate para operar PEN → USDC. Usaremos la wallet "
                f"{transaction.stellar_account[:8]}… con la que abriste esta sesión. "
                f'¿Ya tienes cuenta? <a href="{login_url}">Iniciar sesión</a>'
            )
            return base

        if isinstance(form, VersoVerifyEmailForm):
            _user_id, email = get_pending_verso_user(request, transaction.id)
            base["title"] = "Verifica tu correo"
            base["guidance"] = (
                f"Ingresa el código enviado a {email or 'tu correo'}."
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
                start_url = _onboarding_url(request, transaction)
                base["guidance"] = (
                    "Completa tu verificación KYC con VERSO (DIDIT) para continuar. "
                    f'<a class="btn btn--inline" href="{start_url}">Continuar verificación</a>'
                )
            return base

        if isinstance(form, PenDepositForm):
            base["guidance"] = (
                "Ingresa el monto en soles peruanos. Te mostraremos las "
                "instrucciones de transferencia CCI/CCE y el USDC a recibir."
            )
            return base

        return None
