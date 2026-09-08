"""
SEP-24 deposit integration — PEN on-ramp MVP (Etapa 3).

Webview: monto PEN → instrucciones CCI → poll rails → USDC on-chain (Polaris).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from django import forms
from django.conf import settings
from django.http import QueryDict
from polaris.integrations import DepositIntegration
from polaris.models import DeliveryMethod, Quote, Transaction
from polaris.templates import Template
from rest_framework.request import Request

from verso_integrations.deposit import compute_amount_usdc, get_cci_deposit_instructions
from verso_integrations.models import Sep24DepositMeta
from verso_integrations.polaris_setup import (
    DELIVERY_PEN_SELL,
    pen_asset_identification,
    usdc_asset_identification,
)
from verso_integrations.rates import get_pen_usdc_rate
from verso_integrations.sep24.forms import PenDepositForm


def _format_bank_guidance(instructions: dict) -> str:
    return (
        f"Transfiere {instructions['amount_pen']} PEN a {instructions['bank_name']} "
        f"cuenta {instructions['account_number']}. "
        f"Referencia: {instructions['reference']}. "
        f"Recibirás {instructions['amount_usdc']} USDC @ "
        f"{instructions['tipo_cambio_pen_per_usdc']} PEN/USDC."
    )


class VersoDepositIntegration(DepositIntegration):
    """Interactive SEP-24 on-ramp: PEN (CCI/CCE) → USDC."""

    def after_deposit(self, transaction: Transaction, *args, **kwargs):
        """Optional hook after Polaris submits USDC on-chain."""
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

        initial = {}
        if amount is not None:
            initial["amount_pen"] = amount

        if post_data is not None:
            return PenDepositForm(post_data)
        return PenDepositForm(initial=initial or None)

    def after_form_validation(
        self,
        request: Request,
        form: forms.Form,
        transaction: Transaction,
        *args,
        **kwargs,
    ):
        if not isinstance(form, PenDepositForm):
            return

        rate = get_pen_usdc_rate()
        amount_pen = form.cleaned_data["amount_pen"].quantize(Decimal("0.01"))
        amount_usdc = compute_amount_usdc(amount_pen, rate.tipo_cambio)

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
            price=rate.tipo_cambio.quantize(Decimal("0.01")),
            sell_delivery_method=sell_method,
        )

        instructions = get_cci_deposit_instructions(
            float(amount_pen),
            str(transaction.id),
            tipo_cambio=float(rate.tipo_cambio),
            amount_usdc=float(amount_usdc),
        )

        Sep24DepositMeta.objects.create(
            transaction=transaction,
            amount_pen=amount_pen,
            tipo_cambio=rate.tipo_cambio,
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
            "title": "Depósito PEN → USDC",
        }

        if template == Template.MORE_INFO and transaction is not None:
            meta = Sep24DepositMeta.objects.filter(transaction=transaction).first()
            if meta:
                base["guidance"] = _format_bank_guidance(meta.bank_instructions)
                base["bank_instructions"] = meta.bank_instructions
            return base

        if isinstance(form, PenDepositForm):
            base["guidance"] = (
                "Ingresa el monto en soles peruanos. Te mostraremos las "
                "instrucciones de transferencia CCI/CCE y el USDC a recibir."
            )
            return base

        return None
