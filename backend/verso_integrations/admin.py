from django.contrib import admin, messages
from django.utils import timezone

from verso_integrations.deposit import get_cci_deposit_instructions
from verso_integrations.models import FiatDeposit
from verso_integrations.stellar_payout import StellarPayoutError, disburse_usdc as send_usdc_on_chain


@admin.register(FiatDeposit)
class FiatDepositAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "stellar_account",
        "amount_pen",
        "tipo_cambio",
        "amount_usdc",
        "status",
        "stellar_tx_hash",
        "created_at",
    )
    list_filter = ("status",)
    search_fields = ("stellar_account", "stellar_tx_hash")
    readonly_fields = (
        "amount_usdc",
        "status",
        "bank_instructions",
        "stellar_tx_hash",
        "status_message",
        "created_at",
        "updated_at",
        "disbursed_at",
    )
    actions = ("mark_fiat_received", "disburse_usdc")

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "stellar_account",
                    "amount_pen",
                    "tipo_cambio",
                    "amount_usdc",
                    "status",
                    "bank_instructions",
                )
            },
        ),
        (
            "On-chain disbursement",
            {"fields": ("stellar_tx_hash", "status_message", "disbursed_at")},
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not change and not obj.bank_instructions:
            obj.bank_instructions = get_cci_deposit_instructions(
                float(obj.amount_pen),
                str(obj.pk),
                tipo_cambio=float(obj.tipo_cambio),
                amount_usdc=float(obj.amount_usdc),
            )
            obj.save(update_fields=["bank_instructions"])

    @admin.action(description="Mark fiat as received (pending → fiat confirmed)")
    def mark_fiat_received(self, request, queryset):
        updated = 0
        for deposit in queryset:
            if deposit.status != FiatDeposit.Status.PENDING:
                self.message_user(
                    request,
                    f"Deposit #{deposit.pk}: skipped (status is {deposit.status}).",
                    level=messages.WARNING,
                )
                continue
            deposit.status = FiatDeposit.Status.FIAT_CONFIRMED
            deposit.status_message = ""
            deposit.save(update_fields=["status", "status_message", "updated_at"])
            updated += 1
        if updated:
            self.message_user(
                request,
                f"{updated} deposit(s) marked as fiat received.",
                level=messages.SUCCESS,
            )

    @admin.action(description="Disburse USDC on-chain (fiat confirmed → disbursed)")
    def disburse_usdc(self, request, queryset):
        for deposit in queryset:
            if deposit.status != FiatDeposit.Status.FIAT_CONFIRMED:
                self.message_user(
                    request,
                    f"Deposit #{deposit.pk}: skipped (must be fiat confirmed).",
                    level=messages.WARNING,
                )
                continue
            try:
                tx_hash = send_usdc_on_chain(deposit.stellar_account, deposit.amount_usdc)
            except StellarPayoutError as exc:
                deposit.status_message = str(exc)
                deposit.save(update_fields=["status_message", "updated_at"])
                self.message_user(
                    request,
                    f"Deposit #{deposit.pk}: disbursement failed — {exc}",
                    level=messages.ERROR,
                )
                continue

            deposit.status = FiatDeposit.Status.DISBURSED
            deposit.stellar_tx_hash = tx_hash
            deposit.status_message = ""
            deposit.disbursed_at = timezone.now()
            deposit.save(
                update_fields=[
                    "status",
                    "stellar_tx_hash",
                    "status_message",
                    "disbursed_at",
                    "updated_at",
                ]
            )
            self.message_user(
                request,
                f"Deposit #{deposit.pk}: sent {deposit.amount_usdc} USDC — tx {tx_hash}",
                level=messages.SUCCESS,
            )
