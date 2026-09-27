from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError


MAX_RECEIPT_BYTES = 10 * 1024 * 1024


class PenDepositForm(forms.Form):
    """Collect PEN amount for on-ramp (off-chain → USDC)."""

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


class BankTransferReceiptForm(forms.Form):
    """Confirm PEN bank transfer with mandatory voucher upload."""

    receipt = forms.FileField(
        label="Constancia de transferencia",
        required=True,
        help_text="Sube la foto o PDF de tu voucher bancario (JPG, PNG o PDF, máx. 10 MB).",
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
