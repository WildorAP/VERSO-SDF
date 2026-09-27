from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError

from verso_integrations.sep24.fiat import FIAT_PEN, FIAT_USD, fiat_config, normalize_fiat_currency

MAX_RECEIPT_BYTES = 10 * 1024 * 1024


class FiatDepositForm(forms.Form):
    """Collect fiat amount for on-ramp (PEN or USD → USDC)."""

    amount_fiat = forms.DecimalField(
        label="Monto",
        min_value=Decimal("1"),
        max_digits=18,
        decimal_places=2,
        widget=forms.TextInput(
            attrs={
                "id": "id_amount_fiat",
                "inputmode": "decimal",
                "placeholder": "0.00",
                "autocomplete": "off",
            }
        ),
    )

    def __init__(self, *args, fiat_currency: str = FIAT_PEN, **kwargs):
        self.fiat_currency = normalize_fiat_currency(fiat_currency)
        config = fiat_config(self.fiat_currency)
        super().__init__(*args, **kwargs)
        self.fields["amount_fiat"].label = config.amount_label
        self.fields["amount_fiat"].help_text = config.amount_help
        self.fields["amount_fiat"].min_value = Decimal(config.min_amount)


class PenDepositForm(FiatDepositForm):
    """Backward-compatible PEN-only form used in existing tests."""

    amount_pen = forms.DecimalField(
        label="Monto en soles (PEN)",
        min_value=Decimal("1"),
        max_digits=18,
        decimal_places=2,
        help_text="Ingresa el monto que transferirás por CCI/CCE.",
        widget=forms.TextInput(
            attrs={
                "id": "id_amount_pen",
                "inputmode": "decimal",
                "placeholder": "0.00",
                "autocomplete": "off",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        kwargs.pop("fiat_currency", None)
        super().__init__(*args, fiat_currency=FIAT_PEN, **kwargs)
        del self.fields["amount_fiat"]

    def clean(self):
        cleaned = super().clean()
        if "amount_pen" in cleaned:
            cleaned["amount_fiat"] = cleaned["amount_pen"]
        return cleaned


class BankTransferReceiptForm(forms.Form):
    """Confirm fiat bank transfer with mandatory voucher upload."""

    receipt = forms.FileField(
        label="Constancia de transferencia",
        required=True,
        help_text="Sube la foto o PDF de tu comprobante bancario (JPG, PNG o PDF, máx. 10 MB).",
        widget=forms.ClearableFileInput(
            attrs={
                "accept": "image/*,.pdf,application/pdf",
                "class": "upload-input",
            }
        ),
    )

    def clean_receipt(self):
        receipt = self.cleaned_data.get("receipt")
        if not receipt:
            raise ValidationError("Debes adjuntar la constancia de tu transferencia.")
        if receipt.size > MAX_RECEIPT_BYTES:
            raise ValidationError("El archivo no puede superar 10 MB.")
        content_type = getattr(receipt, "content_type", "") or ""
        if not (
            content_type.startswith("image/")
            or content_type == "application/pdf"
            or receipt.name.lower().endswith(".pdf")
        ):
            raise ValidationError("Sube una imagen (JPG, PNG) o un PDF.")
        return receipt


class TransferAlreadyDeclaredForm(forms.Form):
    """No-op form so duplicate POSTs after confirming transfer do not 422."""

    acknowledge = forms.CharField(required=False, widget=forms.HiddenInput)
