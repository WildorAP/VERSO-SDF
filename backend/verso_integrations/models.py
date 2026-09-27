from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from stellar_sdk import StrKey

from verso_integrations.deposit import compute_amount_usdc


def validate_stellar_public_key(value: str) -> None:
    if not StrKey.is_valid_ed25519_public_key(value):
        raise ValidationError("Invalid Stellar public key (expected G...).")


class Sep24DepositMeta(models.Model):
    """Fiat on-ramp metadata linked to a Polaris SEP-24 Transaction."""

    class FiatCurrency(models.TextChoices):
        PEN = "PEN", "PEN"
        USD = "USD", "USD"

    transaction = models.OneToOneField(
        "polaris.Transaction",
        on_delete=models.CASCADE,
        related_name="verso_deposit_meta",
    )
    fiat_currency = models.CharField(
        max_length=3,
        choices=FiatCurrency.choices,
        default=FiatCurrency.PEN,
        db_index=True,
        help_text="Fiat currency sold by the client in this SEP-24 on-ramp.",
    )
    amount_pen = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        help_text="Fiat amount the client should transfer (PEN or USD).",
    )
    tipo_cambio = models.DecimalField(max_digits=12, decimal_places=4)
    amount_usdc = models.DecimalField(max_digits=18, decimal_places=7)
    sell_asset = models.TextField(
        help_text="SEP-38 asset id for fiat sold by user (iso4217:PEN or iso4217:USD).",
    )
    buy_asset = models.TextField(
        help_text="SEP-38 asset id received on-chain (stellar:USDC:...).",
    )
    bank_instructions = models.JSONField(default=dict, blank=True)
    transfer_receipt = models.FileField(
        upload_to="sep24/receipts/%Y/%m/",
        blank=True,
        null=True,
        help_text="Bank transfer voucher uploaded by the client.",
    )
    transfer_declared_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="When the client confirmed they sent the fiat transfer.",
    )
    fiat_confirmed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "SEP-24 deposit (fiat on-ramp)"
        verbose_name_plural = "SEP-24 deposits (fiat on-ramp)"

    @property
    def amount_fiat(self) -> Decimal:
        return self.amount_pen

    def __str__(self) -> str:
        return (
            f"SEP-24 {self.transaction_id} — {self.amount_pen} {self.fiat_currency} "
            f"→ {self.amount_usdc} USDC"
        )

    def mark_fiat_confirmed(self) -> None:
        from polaris.models import Transaction

        self.fiat_confirmed_at = timezone.now()
        self.save(update_fields=["fiat_confirmed_at", "updated_at"])
        tx = self.transaction
        if tx.status == Transaction.STATUS.incomplete:
            tx.status = Transaction.STATUS.pending_user_transfer_start
            tx.save(update_fields=["status"])


class Sep24WithdrawMeta(models.Model):
    """Fiat off-ramp metadata linked to a Polaris SEP-24 withdrawal Transaction."""

    class FiatCurrency(models.TextChoices):
        PEN = "PEN", "PEN"
        USD = "USD", "USD"

    transaction = models.OneToOneField(
        "polaris.Transaction",
        on_delete=models.CASCADE,
        related_name="verso_withdraw_meta",
    )
    fiat_currency = models.CharField(
        max_length=3,
        choices=FiatCurrency.choices,
        default=FiatCurrency.PEN,
        db_index=True,
        help_text="Fiat currency paid to the client in this SEP-24 off-ramp.",
    )
    amount_usdc = models.DecimalField(max_digits=18, decimal_places=7)
    amount_pen = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        help_text="Fiat amount the client receives (PEN or USD).",
    )
    tipo_cambio = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        help_text="rate_compra (fiat per 1 USDC) at quote time.",
    )
    sell_asset = models.TextField(
        help_text="SEP-38 asset id sold by user on-chain (stellar:USDC:...).",
    )
    buy_asset = models.TextField(
        help_text="SEP-38 asset id paid off-chain (iso4217:PEN or iso4217:USD).",
    )
    payout_bank_details = models.JSONField(default=dict, blank=True)
    payout_confirmed_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="When the client submitted their payout CCI details.",
    )
    fiat_sent_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "SEP-24 withdrawal (fiat off-ramp)"
        verbose_name_plural = "SEP-24 withdrawals (fiat off-ramp)"

    @property
    def amount_fiat(self) -> Decimal:
        return self.amount_pen

    def __str__(self) -> str:
        return (
            f"SEP-24 {self.transaction_id} — {self.amount_usdc} USDC "
            f"→ {self.amount_pen} {self.fiat_currency}"
        )

    def mark_fiat_sent(self) -> None:
        self.fiat_sent_at = timezone.now()
        self.save(update_fields=["fiat_sent_at", "updated_at"])


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
        editable=False,
        default=Decimal("0"),
        help_text="Tipo de cambio: PEN por 1 USDC — obtenido automáticamente de VERSO (rate_venta) al crear el depósito.",
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
        if self.amount_pen is not None and self.amount_pen <= Decimal("0"):
            raise ValidationError({"amount_pen": "Must be greater than zero."})

        if self._state.adding:
            from verso_integrations.rates import RatesError, get_pen_usdc_rate

            try:
                rate = get_pen_usdc_rate()
            except RatesError as exc:
                raise ValidationError(
                    f"No se pudo obtener el tipo de cambio en vivo de VERSO: {exc}"
                ) from exc
            self.tipo_cambio = rate.rate_venta

    def save(self, *args, **kwargs):
        self.amount_usdc = compute_amount_usdc(self.amount_pen, self.tipo_cambio)
        super().save(*args, **kwargs)
