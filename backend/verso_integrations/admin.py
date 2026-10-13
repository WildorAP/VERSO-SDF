from django.contrib import admin, messages
from polaris.models import Transaction

from verso_integrations.models import Sep24DepositMeta, Sep24WithdrawMeta


@admin.register(Sep24DepositMeta)
class Sep24DepositMetaAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "transaction",
        "fiat_currency",
        "amount_pen",
        "tipo_cambio",
        "amount_usdc",
        "transfer_declared_at",
        "fiat_confirmed_at",
        "created_at",
    )
    list_filter = ("fiat_confirmed_at", "transfer_declared_at")
    search_fields = ("transaction__id", "transaction__stellar_account")
    readonly_fields = (
        "transaction",
        "fiat_currency",
        "amount_pen",
        "tipo_cambio",
        "amount_usdc",
        "sell_asset",
        "buy_asset",
        "bank_instructions",
        "transfer_receipt",
        "transfer_declared_at",
        "fiat_confirmed_at",
        "created_at",
        "updated_at",
    )
    actions = ("mark_fiat_received",)

    @admin.action(description="Mark fiat received (enables Polaris rails poll)")
    def mark_fiat_received(self, request, queryset):
        updated = 0
        for meta in queryset:
            if meta.fiat_confirmed_at is not None:
                self.message_user(
                    request,
                    f"SEP-24 meta #{meta.pk}: skipped (already confirmed).",
                    level=messages.WARNING,
                )
                continue
            meta.mark_fiat_confirmed()
            updated += 1
        if updated:
            self.message_user(
                request,
                f"{updated} SEP-24 deposit(s) marked as fiat received.",
                level=messages.SUCCESS,
            )


@admin.register(Sep24WithdrawMeta)
class Sep24WithdrawMetaAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "transaction",
        "fiat_currency",
        "amount_usdc",
        "amount_pen",
        "tipo_cambio",
        "payout_confirmed_at",
        "fiat_sent_at",
        "created_at",
    )
    list_filter = ("fiat_currency", "fiat_sent_at", "payout_confirmed_at")
    search_fields = (
        "transaction__id",
        "transaction__stellar_account",
        "payout_bank_details",
    )
    readonly_fields = (
        "transaction",
        "fiat_currency",
        "amount_usdc",
        "amount_pen",
        "tipo_cambio",
        "sell_asset",
        "buy_asset",
        "payout_bank_details",
        "payout_confirmed_at",
        "fiat_sent_at",
        "created_at",
        "updated_at",
    )
    actions = ("mark_fiat_sent",)

    @admin.action(description="Mark fiat sent (completes SEP-24 withdrawal)")
    def mark_fiat_sent(self, request, queryset):
        updated = 0
        repaired = 0
        for meta in queryset:
            tx = meta.transaction
            if tx.status == Transaction.STATUS.completed:
                self.message_user(
                    request,
                    f"SEP-24 meta #{meta.pk}: skipped (transaction already completed).",
                    level=messages.WARNING,
                )
                continue
            already_sent = meta.fiat_sent_at is not None
            try:
                meta.mark_fiat_sent()
            except Exception as exc:
                from verso_integrations.withdraw import WithdrawalUsdcNotReceivedError

                if isinstance(exc, WithdrawalUsdcNotReceivedError):
                    self.message_user(
                        request,
                        f"SEP-24 meta #{meta.pk}: {exc}",
                        level=messages.ERROR,
                    )
                    continue
                raise
            if already_sent:
                repaired += 1
            else:
                updated += 1
        if updated:
            self.message_user(
                request,
                f"{updated} SEP-24 withdrawal(s) marked as fiat sent and completed.",
                level=messages.SUCCESS,
            )
        if repaired:
            self.message_user(
                request,
                f"{repaired} withdrawal(s) re-synced to completed (fiat was already marked sent).",
                level=messages.SUCCESS,
            )