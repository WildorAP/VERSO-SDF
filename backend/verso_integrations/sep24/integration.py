"""
SEP-24 deposit integration — fiat on-ramp (PEN / USD) + onboarding/KYC gate.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from django import forms
from django.conf import settings
from django.http import QueryDict
from django.urls import reverse
from django.utils import timezone
from polaris.integrations import DepositIntegration
from polaris.models import DeliveryMethod, Quote, Transaction
from polaris.templates import Template
from rest_framework.request import Request
from urllib.parse import urlencode

from verso_integrations.deposit import compute_amount_usdc, get_deposit_instructions
from verso_integrations.models import Sep24DepositMeta
from verso_integrations.polaris_setup import usdc_asset_identification
from verso_integrations.rates import RatesError
from verso_integrations.root import stellar_expert_tx_url
from verso_integrations.sep24.fiat import fiat_config
from verso_integrations.sep24.transaction_views import TERMINAL_TRANSACTION_STATUSES
from verso_integrations.sep24.forms import (
    BankTransferReceiptForm,
    FiatDepositForm,
    PenDepositForm,
    TransferAlreadyDeclaredForm,
)
from verso_integrations.sep24.kyc_gate import (
    apply_verso_user_session,
    get_deposit_fiat_currency,
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
    sync_deposit_fiat_currency_from_request,
    sync_kyc_from_core,
)
from verso_integrations.sep24.onboarding_forms import (
    DiditPromptForm,
    KycStatusForm,
    VersoLoginForm,
    VersoProfileForm,
    VersoRegisterForm,
    VersoVerifyEmailForm,
)


def _format_bank_guidance(instructions: dict) -> str:
    currency = instructions.get("fiat_currency", "PEN")
    amount = instructions.get("amount_fiat", instructions.get("amount_pen"))
    rate = instructions.get(
        "tipo_cambio_fiat_per_usdc", instructions.get("tipo_cambio_pen_per_usdc")
    )
    return (
        f"Transfiere {amount} {currency} a {instructions['bank_name']} "
        f"cuenta {instructions['account_number']}. "
        f"Referencia: {instructions['reference']}. "
        f"Recibirás {instructions['amount_usdc']} USDC @ "
        f"{rate} {currency}/USDC."
    )


def _meta_display_context(meta: Sep24DepositMeta) -> dict:
    config = fiat_config(meta.fiat_currency)
    return {
        "fiat_currency": meta.fiat_currency,
        "fiat_symbol": config.symbol,
        "pair_label": config.pair_label,
        "transfer_kind": "cci",
        "amount_fiat": meta.amount_pen,
        "amount_pen": meta.amount_pen,
        "amount_usdc": meta.amount_usdc,
        "tipo_cambio": meta.tipo_cambio,
        "bank_instructions": meta.bank_instructions,
    }


def _deposit_switch_url(request: Request, transaction: Transaction, currency: str) -> str:
    query = urlencode(
        {
            "transaction_id": str(transaction.id),
            "asset_code": transaction.asset.code,
            "source_asset": fiat_config(currency).asset_identification,
        }
    )
    return request.build_absolute_uri(f"{reverse('get_interactive_deposit')}?{query}")


def _onboarding_url(request: Request, transaction: Transaction) -> str:
    query = urlencode({"transaction_id": str(transaction.id)})
    path = reverse("sep24_onboarding")
    return request.build_absolute_uri(f"{path}?{query}")


def _onboarding_switch_url(request: Request, transaction: Transaction, mode: str) -> str:
    query = urlencode({"transaction_id": str(transaction.id), "mode": mode})
    path = reverse("sep24_onboarding_switch")
    return request.build_absolute_uri(f"{path}?{query}")


def _format_local_datetime(value) -> str:
    if not value:
        return ""
    return timezone.localtime(value).strftime("%d/%m/%Y · %H:%M")


def _deposit_wait_status_message(
    transaction: Transaction, meta: Sep24DepositMeta
) -> str:
    status = transaction.status
    if status == Transaction.STATUS.completed:
        return "Operación finalizada"
    if status == Transaction.STATUS.error:
        return (
            transaction.message
            or "Hubo un problema con tu depósito. Escríbenos a soporte@versotek.io."
        )
    if (
        meta.fiat_confirmed_at
        and status == Transaction.STATUS.pending_user_transfer_start
    ):
        return "Depósito confirmado. Enviando USDC a tu wallet…"
    messages = {
        Transaction.STATUS.pending_user_transfer_start: fiat_config(
            meta.fiat_currency
        ).verifying_message,
        Transaction.STATUS.pending_anchor: "Depósito confirmado. Enviando USDC a tu wallet…",
        Transaction.STATUS.pending_stellar: "Enviando USDC on-chain…",
        Transaction.STATUS.pending_external: "Procesando depósito…",
    }
    return messages.get(status, "Procesando depósito…")


def _deposit_waiting_content(request: Request, transaction: Transaction, meta: Sep24DepositMeta, base: dict) -> dict:
    poll_query = urlencode({"id": str(transaction.id)})
    terminal = transaction.status in TERMINAL_TRANSACTION_STATUSES
    stellar_tx_id = transaction.stellar_transaction_id or ""
    base.update(
        {
            "show_rail": True,
            "show_timeline": False,
            "receipt_uploaded": bool(meta.transfer_receipt),
            "transaction_status": str(transaction.status),
            "status_message": _deposit_wait_status_message(transaction, meta),
            "poll_enabled": not terminal,
            "order_at_display": _format_local_datetime(meta.created_at),
            "completed_at_display": _format_local_datetime(transaction.completed_at),
            "stellar_transaction_id": stellar_tx_id,
            "stellar_tx_url": stellar_expert_tx_url(stellar_tx_id),
            "poll_url": request.build_absolute_uri(
                f"{reverse('sep24_transaction_poll')}?{poll_query}"
            ),
            "template_name": "sep24/onboarding/deposit_waiting.html",
            **_meta_display_context(meta),
        }
    )
    return base


def _deposit_transfer_content(meta: Sep24DepositMeta, base: dict) -> dict:
    base.update(
        {
            "template_name": "sep24/onboarding/deposit_transfer.html",
            "show_rail": True,
            "show_timeline": False,
            **_meta_display_context(meta),
        }
    )
    return base


class VersoDepositIntegration(DepositIntegration):
    """Interactive SEP-24 on-ramp: onboarding/KYC → PEN or USD → USDC."""

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
        if step == "profile":
            if post_data is not None:
                return VersoProfileForm(post_data, **form_kwargs)
            return VersoProfileForm(**form_kwargs)
        if step in {"didit", "pending", "rejected"}:
            if post_data is not None:
                return KycStatusForm(post_data)
            return KycStatusForm()
        if step == "deposit":
            currency = get_deposit_fiat_currency(request, transaction.id) if request and transaction else "PEN"
            initial = {}
            if amount is not None:
                initial["amount_fiat"] = amount
            if post_data is not None:
                return FiatDepositForm(post_data, fiat_currency=currency)
            return FiatDepositForm(initial=initial or None, fiat_currency=currency)
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
        if request is not None:
            sync_deposit_fiat_currency_from_request(request, transaction.id)

        try:
            meta = transaction.verso_deposit_meta
        except Sep24DepositMeta.DoesNotExist:
            meta = None

        if meta is not None:
            if meta.fiat_confirmed_at or meta.transfer_declared_at:
                if post_data is not None:
                    return TransferAlreadyDeclaredForm(post_data)
                return None
            if post_data is not None:
                return BankTransferReceiptForm(post_data, request.FILES if request else None)
            return BankTransferReceiptForm()

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
        if isinstance(form, VersoProfileForm):
            if form.profile_result is None:
                return
            mark_profile_completed(request, transaction.id)
            return
        if isinstance(form, (PenDepositForm, FiatDepositForm)):
            self._handle_fiat_deposit(request, form, transaction)
            return
        if isinstance(form, BankTransferReceiptForm):
            self._handle_bank_transfer_confirmation(request, form, transaction)
            return
        if isinstance(form, TransferAlreadyDeclaredForm):
            return

    def _handle_fiat_deposit(
        self,
        request: Request,
        form: PenDepositForm | FiatDepositForm,
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

        currency = get_deposit_fiat_currency(request, transaction.id)
        config = fiat_config(currency)
        try:
            rate = config.rate_fetcher()
        except RatesError as exc:
            form.add_error(None, f"No pudimos obtener el tipo de cambio: {exc}")
            return

        amount_fiat = form.cleaned_data["amount_fiat"].quantize(Decimal("0.01"))
        amount_usdc = compute_amount_usdc(amount_fiat, rate.rate_venta)

        fiat_id = config.asset_identification
        usdc_id = usdc_asset_identification()
        sell_method = DeliveryMethod.objects.get(
            name=config.delivery_method,
            type=DeliveryMethod.TYPE.sell,
        )

        quote = Quote.objects.create(
            type=Quote.TYPE.indicative,
            stellar_account=transaction.stellar_account,
            muxed_account=transaction.muxed_account,
            account_memo=transaction.account_memo,
            sell_asset=fiat_id,
            buy_asset=usdc_id,
            sell_amount=amount_fiat,
            buy_amount=amount_usdc,
            price=rate.rate_venta.quantize(Decimal("0.01")),
            sell_delivery_method=sell_method,
        )

        instructions = get_deposit_instructions(
            currency,
            float(amount_fiat),
            str(transaction.id),
            tipo_cambio=float(rate.rate_venta),
            amount_usdc=float(amount_usdc),
        )

        Sep24DepositMeta.objects.create(
            transaction=transaction,
            fiat_currency=currency,
            amount_pen=amount_fiat,
            tipo_cambio=rate.rate_venta,
            amount_usdc=amount_usdc,
            sell_asset=fiat_id,
            buy_asset=usdc_id,
            bank_instructions=instructions,
        )

        transaction.quote = quote
        transaction.amount_in = amount_fiat
        transaction.amount_expected = amount_fiat
        transaction.amount_out = amount_usdc
        transaction.amount_fee = Decimal("0")
        transaction.fee_asset = fiat_id
        transaction.to_address = transaction.stellar_account
        transaction.status = Transaction.STATUS.pending_user_transfer_start
        transaction.save()

        if getattr(settings, "VERSO_MOCK_AUTO_CONFIRM_FIAT", False):
            Sep24DepositMeta.objects.get(transaction=transaction).mark_fiat_confirmed()

    def _handle_bank_transfer_confirmation(
        self,
        request: Request,
        form: BankTransferReceiptForm,
        transaction: Transaction,
    ) -> None:
        meta = Sep24DepositMeta.objects.get(transaction=transaction)
        receipt = form.cleaned_data["receipt"]
        meta.transfer_declared_at = timezone.now()
        meta.transfer_receipt = receipt
        meta.save(
            update_fields=["transfer_declared_at", "transfer_receipt", "updated_at"]
        )

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
            "title": "Depósito → USDC",
        }

        if transaction is not None:
            meta = Sep24DepositMeta.objects.filter(transaction=transaction).first()
            if meta:
                if meta.transfer_declared_at or meta.fiat_confirmed_at:
                    if template in {Template.MORE_INFO, Template.DEPOSIT}:
                        return _deposit_waiting_content(request, transaction, meta, base)
                elif form is None and template == Template.DEPOSIT:
                    return _deposit_transfer_content(meta, base)

        if template == Template.MORE_INFO and transaction is not None:
            base["poll_url"] = request.build_absolute_uri(
                f"{reverse('sep24_transaction_poll')}?{urlencode({'id': str(transaction.id)})}"
            )
            base["template_name"] = "polaris/more_info_verso.html"
            return base

        if transaction is None or form is None:
            return None

        if isinstance(form, VersoLoginForm):
            register_url = _onboarding_switch_url(request, transaction, "register")
            base["title"] = "Inicia sesión en VERSO"
            base["guidance"] = (
                "Ingresa con tu cuenta VERSO para operar PEN o USD → USDC con la wallet "
                f"{transaction.stellar_account[:8]}… de esta sesión. "
                f'¿Aún no tienes cuenta? <a href="{register_url}">Crear cuenta</a>'
            )
            return base

        if isinstance(form, VersoRegisterForm):
            login_url = _onboarding_switch_url(request, transaction, "login")
            base["title"] = "Crea tu cuenta VERSO"
            base["guidance"] = (
                "Regístrate para operar PEN o USD → USDC. Usaremos la wallet "
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
                start_url = _onboarding_url(request, transaction)
                base["guidance"] = (
                    "Completa tu verificación KYC con VERSO (DIDIT) para continuar. "
                    f'<a class="btn btn--inline" href="{start_url}">Continuar verificación</a>'
                )
            return base

        if isinstance(form, (PenDepositForm, FiatDepositForm)):
            currency = get_deposit_fiat_currency(request, transaction.id)
            config = fiat_config(currency)
            try:
                rate = config.rate_fetcher()
            except RatesError:
                rate = None
            base.update(
                {
                    "template_name": "sep24/onboarding/deposit_amount.html",
                    "show_rail": True,
                    "show_timeline": False,
                    "wallet_short": f"{transaction.stellar_account[:8]}…",
                    "fiat_currency": currency,
                    "fiat_symbol": config.symbol,
                    "pair_label": config.pair_label,
                    "pen_switch_url": _deposit_switch_url(request, transaction, "PEN"),
                    "usd_switch_url": _deposit_switch_url(request, transaction, "USD"),
                    "rate_venta": str(rate.rate_venta) if rate else "",
                    "rate_venta_display": (
                        f"{rate.rate_venta.quantize(Decimal('0.0001'))}" if rate else ""
                    ),
                    "rate_unavailable": rate is None,
                    "min_fiat_amount": config.min_amount,
                }
            )
            return base

        if isinstance(form, BankTransferReceiptForm):
            meta = Sep24DepositMeta.objects.filter(transaction=transaction).first()
            if not meta:
                return None
            return _deposit_transfer_content(meta, base)

        return None
