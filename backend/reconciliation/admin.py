"""Django admin for reconciliation models."""

from __future__ import annotations

from django import forms
from django.contrib import admin, messages
from django.shortcuts import render
from django.utils.html import format_html

import reconciliation.admin_urls  # noqa: F401 — register /admin/reconciliation/report/

from reconciliation.models import Discrepancy, LedgerEntry, ReconciliationRun, SyncState
from verso_integrations.root import stellar_expert_tx_url


class ResolveDiscrepancyForm(forms.Form):
    resolution_note = forms.CharField(
        label="Nota de resolución",
        widget=forms.Textarea(attrs={"rows": 3}),
        required=True,
    )


@admin.register(SyncState)
class SyncStateAdmin(admin.ModelAdmin):
    list_display = (
        "hot_wallet",
        "last_processed_ledger",
        "opening_balance",
        "observation_started_at",
        "last_success_at",
        "lag_minutes",
    )
    readonly_fields = [f.name for f in SyncState._meta.fields]

    @admin.display(description="Lag (min)")
    def lag_minutes(self, obj: SyncState) -> str:
        if not obj.last_success_at:
            return "—"
        from django.utils import timezone

        delta = timezone.now() - obj.last_success_at
        return f"{int(delta.total_seconds() // 60)}"


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = (
        "ledger_closed_at",
        "direction",
        "amount_usdc",
        "event_type",
        "counterparty_short",
        "match_status",
        "match_kind",
        "fiat_status",
        "kyc_status",
        "stellar_tx_link",
    )
    list_filter = ("direction", "match_status", "match_kind", "fiat_status", "event_type")
    search_fields = (
        "stellar_tx_hash",
        "memo_raw",
        "counterparty",
        "anchor_callback_id",
        "event_id",
    )
    readonly_fields = [f.name for f in LedgerEntry._meta.fields]

    @admin.display(description="Counterparty")
    def counterparty_short(self, obj: LedgerEntry) -> str:
        if not obj.counterparty:
            return "—"
        return f"{obj.counterparty[:8]}…"

    @admin.display(description="Stellar tx")
    def stellar_tx_link(self, obj: LedgerEntry) -> str:
        url = stellar_expert_tx_url(obj.stellar_tx_hash)
        if not url:
            return obj.stellar_tx_hash[:16]
        return format_html(
            '<a href="{}" target="_blank" rel="noopener">{}…</a>',
            url,
            obj.stellar_tx_hash[:12],
        )


@admin.register(ReconciliationRun)
class ReconciliationRunAdmin(admin.ModelAdmin):
    list_display = (
        "run_at",
        "status",
        "delta",
        "ledger_lag",
        "onchain_balance",
        "internal_balance",
        "open_discrepancies",
        "unmatched_count",
    )
    list_filter = ("status",)
    readonly_fields = [f.name for f in ReconciliationRun._meta.fields]
    ordering = ("-run_at",)


@admin.register(Discrepancy)
class DiscrepancyAdmin(admin.ModelAdmin):
    list_display = (
        "detected_at",
        "kind",
        "severity",
        "is_open",
        "dedupe_key",
        "resolved_at",
    )
    list_filter = ("kind", "severity", "resolved_at")
    search_fields = ("dedupe_key", "message")
    readonly_fields = (
        "kind",
        "severity",
        "ledger_entry",
        "polaris_transaction",
        "run",
        "dedupe_key",
        "expected",
        "actual",
        "message",
        "detected_at",
        "alerted_at",
        "resolved_at",
        "resolved_by",
        "resolution",
    )
    fields = readonly_fields + ("resolution_note",)
    actions = (
        "resolve_as_funding",
        "resolve_as_manual_transfer",
        "resolve_as_fixed",
        "resolve_as_false_positive",
    )

    @admin.display(boolean=True)
    def is_open(self, obj: Discrepancy) -> bool:
        return obj.is_open

    def _resolve_action(self, request, queryset, resolution: str, action_name: str):
        if "apply" in request.POST:
            form = ResolveDiscrepancyForm(request.POST)
            if form.is_valid():
                note = form.cleaned_data["resolution_note"]
                count = 0
                for row in queryset.filter(resolved_at__isnull=True):
                    row.resolve(request.user, resolution, note)
                    count += 1
                self.message_user(request, f"Resolved {count} discrepancy(ies).")
                return None
        else:
            form = ResolveDiscrepancyForm()

        return render(
            request,
            "admin/reconciliation/resolve_discrepancy.html",
            {
                "title": "Resolver discrepancias",
                "queryset": queryset,
                "form": form,
                "action_name": action_name,
            },
        )

    @admin.action(description="Resolver como fondeo")
    def resolve_as_funding(self, request, queryset):
        return self._resolve_action(
            request, queryset, Discrepancy.Resolution.FUNDING, "resolve_as_funding"
        )

    @admin.action(description="Resolver como transferencia manual")
    def resolve_as_manual_transfer(self, request, queryset):
        return self._resolve_action(
            request,
            queryset,
            Discrepancy.Resolution.MANUAL_TRANSFER,
            "resolve_as_manual_transfer",
        )

    @admin.action(description="Marcar corregida")
    def resolve_as_fixed(self, request, queryset):
        return self._resolve_action(
            request, queryset, Discrepancy.Resolution.FIXED, "resolve_as_fixed"
        )

    @admin.action(description="Falso positivo")
    def resolve_as_false_positive(self, request, queryset):
        return self._resolve_action(
            request,
            queryset,
            Discrepancy.Resolution.FALSE_POSITIVE,
            "resolve_as_false_positive",
        )
