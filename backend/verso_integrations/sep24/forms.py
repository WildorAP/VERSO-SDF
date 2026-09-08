from decimal import Decimal

from django import forms


class PenDepositForm(forms.Form):
    """Collect PEN amount for on-ramp (off-chain → USDC)."""

    amount_pen = forms.DecimalField(
        label="Monto en soles (PEN)",
        min_value=Decimal("1"),
        max_digits=18,
        decimal_places=2,
        help_text="Ingresa el monto que transferirás por CCI/CCE.",
    )
