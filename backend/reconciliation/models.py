"""PostgreSQL models for D3 USDC reconciliation."""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class SyncState(models.Model):
    """Singleton cursor and opening balance for RPC event sync."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    network_passphrase = models.CharField(max_length=128)
    hot_wallet = models.CharField(max_length=56)
    asset_contract_id = models.CharField(max_length=56)
    start_ledger = models.PositiveIntegerField()
    cursor = models.CharField(max_length=64, blank=True, default="")
    last_processed_ledger = models.PositiveIntegerField()
    opening_balance = models.DecimalField(max_digits=20, decimal_places=7)
    observation_started_at = models.DateTimeField(
        help_text="Only Polaris txs completed after this time are checked for missing_onchain.",
    )
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    last_error_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Reconciliation sync state"
        verbose_name_plural = "Reconciliation sync state"

    @classmethod
    def get(cls) -> SyncState:
        try:
            return cls.objects.get(pk=1)
        except cls.DoesNotExist as exc:
            raise RuntimeError(
                "Reconciliation SyncState missing — run reconciliation_bootstrap first."
            ) from exc

    def __str__(self) -> str:
        return f"SyncState ledger={self.last_processed_ledger} cursor={self.cursor[:8]!r}…"


class LedgerEntry(models.Model):
    """One on-chain USDC movement on the anchor hot wallet (proposal schema + technical fields)."""

    class Direction(models.TextChoices):
        INBOUND = "inbound", "Inbound"
        OUTBOUND = "outbound", "Outbound"

    class EventType(models.TextChoices):
        TRANSFER = "transfer", "Transfer"
        MINT = "mint", "Mint"
        BURN = "burn", "Burn"
        CLAWBACK = "clawback", "Clawback"

    class FiatStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        CONFIRMED = "confirmed", "Confirmed"
        SENT = "sent", "Sent"
        NOT_APPLICABLE = "not_applicable", "Not applicable"

    class MatchStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        MATCHED = "matched", "Matched"
        UNMATCHED = "unmatched", "Unmatched"
        CLASSIFIED = "classified", "Classified"

    stellar_tx_hash = models.CharField(max_length=64, db_index=True)
    amount_usdc = models.DecimalField(max_digits=20, decimal_places=7)
    amount_pen = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    amount_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    direction = models.CharField(max_length=8, choices=Direction.choices, db_index=True)
    kyc_status = models.CharField(max_length=32, default="unknown")
    fiat_rail = models.CharField(max_length=32, blank=True, default="")
    fiat_status = models.CharField(
        max_length=20,
        choices=FiatStatus.choices,
        default=FiatStatus.NOT_APPLICABLE,
    )
    anchor_callback_id = models.CharField(max_length=64, blank=True, default="", db_index=True)

    event_id = models.CharField(max_length=64, unique=True)
    event_type = models.CharField(max_length=16, choices=EventType.choices)
    ledger = models.PositiveIntegerField(db_index=True)
    ledger_closed_at = models.DateTimeField()
    from_address = models.CharField(max_length=69, blank=True, default="")
    to_address = models.CharField(max_length=69, blank=True, default="")
    counterparty = models.CharField(max_length=69, blank=True, default="")
    memo_raw = models.CharField(max_length=128, blank=True, default="")
    memo_type = models.CharField(max_length=8, blank=True, default="")
    polaris_transaction = models.ForeignKey(
        "polaris.Transaction",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    fiat_deposit = models.ForeignKey(
        "verso_integrations.FiatDeposit",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    match_status = models.CharField(
        max_length=16,
        choices=MatchStatus.choices,
        default=MatchStatus.PENDING,
        db_index=True,
    )
    match_kind = models.CharField(max_length=32, blank=True, default="")
    raw = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["ledger", "event_id"]
        verbose_name = "Stellar ledger entry"
        verbose_name_plural = "Stellar ledger entries"

    @property
    def signed_amount(self) -> Decimal:
        if self.direction == self.Direction.INBOUND:
            return self.amount_usdc
        return -self.amount_usdc

    def __str__(self) -> str:
        return f"{self.direction} {self.amount_usdc} USDC @ {self.stellar_tx_hash[:12]}…"


class ReconciliationRun(models.Model):
    class Status(models.TextChoices):
        OK = "ok", "OK"
        MISMATCH = "mismatch", "Mismatch"
        SKIPPED = "skipped", "Skipped"

    run_at = models.DateTimeField(auto_now_add=True, db_index=True)
    ledger = models.PositiveIntegerField()
    onchain_balance = models.DecimalField(max_digits=20, decimal_places=7)
    internal_balance = models.DecimalField(max_digits=20, decimal_places=7)
    delta = models.DecimalField(max_digits=20, decimal_places=7)
    events_total = models.PositiveIntegerField(default=0)
    unmatched_count = models.PositiveIntegerField(default=0)
    open_discrepancies = models.PositiveIntegerField(default=0)
    ledger_lag = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=Status.choices)
    detail = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-run_at"]

    def __str__(self) -> str:
        return f"Run {self.run_at:%Y-%m-%d %H:%M} {self.status} delta={self.delta}"


class Discrepancy(models.Model):
    class Kind(models.TextChoices):
        BALANCE_MISMATCH = "balance_mismatch", "Balance mismatch"
        UNMATCHED_INBOUND = "unmatched_inbound", "Unmatched inbound"
        UNMATCHED_OUTBOUND = "unmatched_outbound", "Unmatched outbound"
        AMOUNT_MISMATCH = "amount_mismatch", "Amount mismatch"
        MISSING_ONCHAIN = "missing_onchain", "Missing on-chain"
        WORKER_STALE = "worker_stale", "Worker stale"
        TEST = "test", "Test"

    class Severity(models.TextChoices):
        CRITICAL = "critical", "Critical"
        WARNING = "warning", "Warning"

    class Resolution(models.TextChoices):
        FUNDING = "funding", "Funding"
        MANUAL_TRANSFER = "manual_transfer", "Manual transfer"
        FIXED = "fixed", "Fixed"
        FALSE_POSITIVE = "false_positive", "False positive"
        AUTO_CLEARED = "auto_cleared", "Auto cleared"

    kind = models.CharField(max_length=32, choices=Kind.choices, db_index=True)
    severity = models.CharField(max_length=16, choices=Severity.choices)
    ledger_entry = models.ForeignKey(
        LedgerEntry,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="discrepancies",
    )
    polaris_transaction = models.ForeignKey(
        "polaris.Transaction",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    run = models.ForeignKey(
        ReconciliationRun,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="discrepancies",
    )
    dedupe_key = models.CharField(max_length=128, db_index=True)
    expected = models.DecimalField(max_digits=20, decimal_places=7, null=True, blank=True)
    actual = models.DecimalField(max_digits=20, decimal_places=7, null=True, blank=True)
    message = models.TextField()
    detected_at = models.DateTimeField(auto_now_add=True)
    alerted_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    resolution = models.CharField(
        max_length=32,
        choices=Resolution.choices,
        blank=True,
        default="",
    )
    resolution_note = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["dedupe_key"],
                condition=Q(resolved_at__isnull=True),
                name="recon_unique_open_dedupe_key",
            )
        ]
        ordering = ["-detected_at"]
        verbose_name_plural = "Discrepancies"

    @property
    def is_open(self) -> bool:
        return self.resolved_at is None

    def resolve(
        self,
        user,
        resolution: str,
        note: str = "",
    ) -> None:
        if resolution != self.Resolution.AUTO_CLEARED and not (note or "").strip():
            raise ValueError("resolution_note is required unless resolution is auto_cleared.")
        self.resolved_at = timezone.now()
        self.resolved_by = user
        self.resolution = resolution
        self.resolution_note = note or ""
        self.save(
            update_fields=[
                "resolved_at",
                "resolved_by",
                "resolution",
                "resolution_note",
            ]
        )
        if self.ledger_entry and resolution in (
            self.Resolution.FUNDING,
            self.Resolution.MANUAL_TRANSFER,
        ):
            entry = self.ledger_entry
            entry.match_status = LedgerEntry.MatchStatus.CLASSIFIED
            entry.match_kind = resolution
            entry.save(update_fields=["match_status", "match_kind", "updated_at"])

    def __str__(self) -> str:
        state = "open" if self.is_open else "resolved"
        return f"{self.kind} ({self.severity}, {state})"
