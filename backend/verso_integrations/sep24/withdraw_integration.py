"""
SEP-24 withdrawal integration — USDC off-ramp (PEN / USD) + onboarding/KYC gate.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional
from urllib.parse import urlencode

from django import forms
from django.conf import settings
from django.http import QueryDict
from django.urls import reverse
from django.utils import timezone
from polaris.integrations import WithdrawalIntegration
from polaris.models import DeliveryMethod, Quote, Transaction
from polaris.templates import Template
from rest_framework.request import Request

from verso_integrations.models import Sep24WithdrawMeta
from verso_integrations.polaris_setup import usdc_asset_identification
from verso_integrations.rates import RatesError
from verso_integrations.root import stellar_expert_tx_url
from verso_integrations.sep24.fiat import fiat_config
from verso_integrations.sep24.forms import PayoutBankForm, UsdcWithdrawForm
from verso_integrations.sep24.kyc_gate import (
    lookup_client_kyc,
    onboarding_step,
    sync_fiat_currency_from_request,
    get_withdraw_fiat_currency,
)
from verso_integrations.sep24.onboarding_flow import (
    after_onboarding_form_validation,
    content_for_onboarding_form,
    form_for_onboarding_step,
    operate_step_for_transaction,
)
from verso_integrations.sep24.transaction_views import TERMINAL_TRANSACTION_STATUSES
from verso_integrations.sep38 import price_for_pair
from verso_integrations.withdraw import build_payout_bank_details, compute_amount_fiat, usdc_payment_received


def _awaiting_usdc_payment(transaction: Transaction) -> bool:
    if transaction.status == Transaction.STATUS.completed:
        return False
    return not usdc_payment_received(transaction)


def _meta_display_context(meta: Sep24WithdrawMeta) -> dict:
    config = fiat_config(meta.fiat_currency)
    return {
        "fiat_currency": meta.fiat_currency,
        "fiat_symbol": config.symbol,
        "pair_label": config.off_ramp_pair_label,
        "transfer_kind": "cci",
        "amount_fiat": meta.amount_pen,
        "amount_pen": meta.amount_pen,
        "amount_usdc": meta.amount_usdc,
        "tipo_cambio": meta.tipo_cambio,
        "payout_bank_details": meta.payout_bank_details,
    }


def _withdraw_switch_url(request: Request, transaction: Transaction, currency: str) -> str:
    query = urlencode(
        {
            "transaction_id": str(transaction.id),
            "asset_code": transaction.asset.code,
            "destination_asset": fiat_config(currency).asset_identification,
        }
    )
    return request.build_absolute_uri(f"{reverse('get_interactive_withdraw')}?{query}")


def _format_local_datetime(value) -> str:
    if not value:
        return ""
    return timezone.localtime(value).strftime("%d/%m/%Y · %H:%M")


def _withdraw_wait_status_message(
    transaction: Transaction, meta: Sep24WithdrawMeta
) -> str:
    status = transaction.status
    if status == Transaction.STATUS.completed:
        return "Operación finalizada"
    if status == Transaction.STATUS.error:
        return (
            transaction.message
            or "Hubo un problema con tu retiro. Escríbenos a soporte@versotek.io."
        )
    if status == Transaction.STATUS.pending_user_transfer_start:
        return "Envía USDC a la cuenta indicada con el memo exacto."
    if not usdc_payment_received(transaction):
        return "Esperando tu envío de USDC on-chain…"
    if meta.fiat_sent_at:
        return "Transferencia fiat completada."
    messages = {
        Transaction.STATUS.pending_anchor: fiat_config(
            meta.fiat_currency
        ).withdraw_verifying_message,
        Transaction.STATUS.pending_external: "Enviando fondos a tu cuenta bancaria…",
        Transaction.STATUS.pending_stellar: "Procesando retiro…",
    }
    return messages.get(status, "Procesando retiro…")


def _more_info_withdraw_content(
    request: Request, transaction: Transaction, meta: Sep24WithdrawMeta, base: dict
) -> dict:
    """Keep Polaris more_info (callback.js) so Demo Wallet can submit USDC."""
    poll_query = urlencode({"id": str(transaction.id)})
    base.update(
        {
            "poll_url": request.build_absolute_uri(
                f"{reverse('sep24_transaction_poll')}?{poll_query}"
            ),
            "template_name": "polaris/more_info_withdraw_verso.html",
            "awaiting_usdc": _awaiting_usdc_payment(transaction),
            "receiving_account": transaction.receiving_anchor_account or "",
            "memo": transaction.memo or "",
            "memo_type": transaction.memo_type or "",
            **_meta_display_context(meta),
        }
    )
    return base


def _withdraw_waiting_content(
    request: Request, transaction: Transaction, meta: Sep24WithdrawMeta, base: dict
) -> dict:
    poll_query = urlencode({"id": str(transaction.id)})
    terminal = transaction.status in TERMINAL_TRANSACTION_STATUSES
    stellar_tx_id = transaction.stellar_transaction_id or ""
    base.update(
        {
            "show_rail": True,
            "show_timeline": False,
            "transaction_status": str(transaction.status),
            "status_message": _withdraw_wait_status_message(transaction, meta),
            "poll_enabled": not terminal,
            "order_at_display": _format_local_datetime(meta.created_at),
            "completed_at_display": _format_local_datetime(transaction.completed_at),
            "stellar_transaction_id": stellar_tx_id,
            "stellar_tx_url": stellar_expert_tx_url(stellar_tx_id),
            "poll_url": request.build_absolute_uri(
                f"{reverse('sep24_transaction_poll')}?{poll_query}"
            ),
            "template_name": "sep24/onboarding/withdraw_waiting.html",
            **_meta_display_context(meta),
        }
    )
    return base


def _withdraw_send_content(
    request: Request, transaction: Transaction, meta: Sep24WithdrawMeta, base: dict
) -> dict:
    base.update(
        {
            "template_name": "sep24/onboarding/withdraw_send.html",
            "show_rail": True,
            "show_timeline": False,
            "receiving_account": transaction.receiving_anchor_account or "",
            "memo": transaction.memo or "",
            "memo_type": transaction.memo_type or "",
            **_meta_display_context(meta),
        }
    )
    return base


class VersoWithdrawIntegration(WithdrawalIntegration):
    """Interactive SEP-24 off-ramp: onboarding/KYC → USDC → PEN or USD."""

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
            sync_fiat_currency_from_request(request, transaction.id)

        meta = Sep24WithdrawMeta.objects.filter(transaction=transaction).first()

        if meta is not None:
            if meta.payout_confirmed_at:
                return None
            if post_data is not None:
                return PayoutBankForm(post_data)
            return PayoutBankForm()

        step = onboarding_step(request, transaction)
        if step != operate_step_for_transaction(transaction):
            return form_for_onboarding_step(
                step,
                post_data,
                request=request,
                transaction=transaction,
                amount=amount,
            )

        currency = get_withdraw_fiat_currency(request, transaction.id)
        initial = {}
        if amount is not None:
            initial["amount_usdc"] = amount
        if post_data is not None:
            return UsdcWithdrawForm(post_data, fiat_currency=currency)
        return UsdcWithdrawForm(initial=initial or None, fiat_currency=currency)

    def after_form_validation(
        self,
        request: Request,
        form: forms.Form,
        transaction: Transaction,
        *args,
        **kwargs,
    ):
        if after_onboarding_form_validation(request, form, transaction):
            return
        if isinstance(form, UsdcWithdrawForm):
            self._handle_usdc_withdraw(request, form, transaction)
            return
        if isinstance(form, PayoutBankForm):
            self._handle_payout_bank(request, form, transaction)

    def _handle_usdc_withdraw(
        self,
        request: Request,
        form: UsdcWithdrawForm,
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

        currency = get_withdraw_fiat_currency(request, transaction.id)
        config = fiat_config(currency)
        try:
            rate = config.rate_fetcher()
        except RatesError as exc:
            form.add_error(None, f"No pudimos obtener el tipo de cambio: {exc}")
            return

        amount_usdc = form.cleaned_data["amount_usdc"].quantize(Decimal("0.0000001"))
        amount_fiat = compute_amount_fiat(amount_usdc, rate.rate_compra)

        usdc_id = usdc_asset_identification()
        fiat_id = config.asset_identification
        buy_method = DeliveryMethod.objects.get(
            name=config.buy_delivery_method,
            type=DeliveryMethod.TYPE.buy,
        )
        from polaris.models import OffChainAsset

        scheme, identifier = fiat_id.split(":", 1)
        fiat_asset = OffChainAsset.objects.get(scheme=scheme, identifier=identifier)

        quote = Quote.objects.create(
            type=Quote.TYPE.indicative,
            stellar_account=transaction.stellar_account,
            muxed_account=transaction.muxed_account,
            account_memo=transaction.account_memo,
            sell_asset=usdc_id,
            buy_asset=fiat_id,
            sell_amount=amount_usdc,
            buy_amount=amount_fiat,
            price=price_for_pair(transaction.asset, fiat_asset, rate),
            buy_delivery_method=buy_method,
        )

        Sep24WithdrawMeta.objects.create(
            transaction=transaction,
            fiat_currency=currency,
            amount_usdc=amount_usdc,
            amount_pen=amount_fiat,
            tipo_cambio=rate.rate_compra,
            sell_asset=usdc_id,
            buy_asset=fiat_id,
        )

        transaction.quote = quote
        transaction.amount_in = amount_usdc
        transaction.amount_expected = amount_usdc
        transaction.amount_out = amount_fiat
        transaction.amount_fee = Decimal("0")
        transaction.fee_asset = usdc_id
        transaction.save()

    def _handle_payout_bank(
        self,
        request: Request,
        form: PayoutBankForm,
        transaction: Transaction,
    ) -> None:
        meta = Sep24WithdrawMeta.objects.get(transaction=transaction)
        meta.payout_bank_details = build_payout_bank_details(
            bank_name=form.cleaned_data["bank_name"],
            account_number=form.cleaned_data["account_number"],
            account_holder=form.cleaned_data["account_holder"],
            fiat_currency=meta.fiat_currency,
            origen_fondos=form.cleaned_data["origen_fondos"],
            origen_fondos_otro=form.cleaned_data.get("origen_fondos_otro", ""),
        )
        meta.payout_confirmed_at = timezone.now()
        meta.save(
            update_fields=["payout_bank_details", "payout_confirmed_at", "updated_at"]
        )

        if getattr(settings, "VERSO_MOCK_AUTO_SEND_FIAT", False):
            meta.mark_fiat_sent()

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
            "title": "Retiro USDC",
        }

        if transaction is not None:
            meta = Sep24WithdrawMeta.objects.filter(transaction=transaction).first()
            if meta and meta.payout_confirmed_at:
                if template == Template.MORE_INFO:
                    return _more_info_withdraw_content(request, transaction, meta, base)
                # Polaris POST checks content_for_template(WITHDRAW, form=None) after the
                # last form; returning content here would keep the user on webapp instead of
                # redirecting to more_info (callback.js / Demo Wallet payment).
                if template == Template.WITHDRAW and form is not None:
                    if _awaiting_usdc_payment(transaction):
                        return _withdraw_send_content(request, transaction, meta, base)
                    return _withdraw_waiting_content(request, transaction, meta, base)

        if template == Template.MORE_INFO and transaction is not None:
            base["poll_url"] = request.build_absolute_uri(
                f"{reverse('sep24_transaction_poll')}?{urlencode({'id': str(transaction.id)})}"
            )
            base["template_name"] = "polaris/more_info_verso.html"
            return base

        if transaction is None or form is None:
            return None

        onboarding_content = content_for_onboarding_form(request, form, transaction, base)
        if onboarding_content is not None:
            return onboarding_content

        if isinstance(form, UsdcWithdrawForm):
            currency = get_withdraw_fiat_currency(request, transaction.id)
            config = fiat_config(currency)
            try:
                rate = config.rate_fetcher()
            except RatesError:
                rate = None
            base.update(
                {
                    "template_name": "sep24/onboarding/withdraw_amount.html",
                    "show_rail": True,
                    "show_timeline": False,
                    "wallet_short": f"{transaction.stellar_account[:8]}…",
                    "fiat_currency": currency,
                    "fiat_symbol": config.symbol,
                    "pair_label": config.off_ramp_pair_label,
                    "pen_switch_url": _withdraw_switch_url(request, transaction, "PEN"),
                    "usd_switch_url": _withdraw_switch_url(request, transaction, "USD"),
                    "rate_compra": str(rate.rate_compra) if rate else "",
                    "rate_compra_display": (
                        f"{rate.rate_compra.quantize(Decimal('0.0001'))}" if rate else ""
                    ),
                    "rate_unavailable": rate is None,
                    "min_usdc_amount": config.min_usdc_amount,
                }
            )
            return base

        if isinstance(form, PayoutBankForm):
            meta = Sep24WithdrawMeta.objects.filter(transaction=transaction).first()
            if not meta:
                return None
            base.update(
                {
                    "template_name": "sep24/onboarding/withdraw_bank.html",
                    "show_rail": True,
                    "show_timeline": False,
                    **_meta_display_context(meta),
                }
            )
            return base

        return None
