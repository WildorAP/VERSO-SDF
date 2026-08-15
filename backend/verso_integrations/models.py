from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from stellar_sdk import StrKey

from verso_integrations.deposit import compute_amount_usdc


def validate_stellar_public_key(value: str) -> None:
    if not StrKey.is_valid_ed25519_public_key(value):
        raise ValidationError("Invalid Stellar public key (expected G...).")


class FiatDeposit(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending (awaiting bank transfer)"
        FIAT_CONFIRMED = "fiat_confirmed", "Fiat confirmed"
        DISBURSING = "disbursing", "Disbursing (in progress)"
        DISBURSED = "disbursed", "USDC disbursed"

    stellar_account = models.CharField(
        max_length=56,
        validators=[validate_stellar_public_key],
        help_text="Client Stellar account (G...) — same account used for SEP-10.",
    )
    amount_pen = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        help_text="PEN amount the client should transfer via CCI/CCE (simulated).",
    )
    tipo_cambio = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        help_text="Tipo de cambio: PEN por 1 USDC (ej. 3.7500).",
    )
    amount_usdc = models.DecimalField(
        max_digits=18,
        decimal_places=7,
        editable=False,
        help_text="USDC calculado: amount_pen / tipo_cambio (7 decimales Stellar).",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    bank_instructions = models.JSONField(
        default=dict,
        blank=True,
        help_text="Simulated CCI/CCE transfer instructions shown to the client.",
    )
    stellar_tx_hash = models.CharField(max_length=64, blank=True, default="")
    status_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    disbursed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "simulated fiat deposit"
        verbose_name_plural = "simulated fiat deposits"

    def __str__(self) -> str:
        return (
            f"{self.stellar_account[:8]}… {self.amount_pen} PEN @ {self.tipo_cambio} "
            f"→ {self.amount_usdc} USDC ({self.status})"
        )

    def clean(self) -> None:
        super().clean()
        if self.amount_pen <= Decimal("0"):
            raise ValidationError({"amount_pen": "Must be greater than zero."})
        if self.tipo_cambio <= Decimal("0"):
            raise ValidationError({"tipo_cambio": "Must be greater than zero."})

    def save(self, *args, **kwargs):
        self.amount_usdc = compute_amount_usdc(self.amount_pen, self.tipo_cambio)
        super().save(*args, **kwargs)
