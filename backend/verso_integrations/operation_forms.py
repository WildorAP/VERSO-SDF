"""Forms of the operator actions in the admin (see ``operations.py``)."""

from __future__ import annotations

from decimal import Decimal

from django import forms

from verso_integrations.models import OperationRejection


class EmptyActionForm(forms.Form):
    """Actions that only need the confirmation page."""


class RejectForm(forms.Form):
    reason = forms.ChoiceField(label="Motivo", choices=OperationRejection.Reason.choices)
    note = forms.CharField(
        label="Nota interna (el cliente no la ve)",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    funds_received = forms.BooleanField(label="Ya recibimos el dinero del cliente", required=False)

    def __init__(self, *args, ask_funds: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        if not ask_funds:
            del self.fields["funds_received"]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("reason") == OperationRejection.Reason.OTHER and not cleaned.get("note", "").strip():
            self.add_error("note", "Describe el motivo.")
        return cleaned


class HoldForm(forms.Form):
    note = forms.CharField(
        label="Motivo (el cliente no lo ve)",
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class RefundForm(forms.Form):
    amount = forms.DecimalField(label="Monto devuelto", min_value=Decimal("0.0000001"), decimal_places=7)
    reference = forms.CharField(label="Referencia", max_length=128)

    def __init__(self, *args, reference_help: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reference"].help_text = reference_help
