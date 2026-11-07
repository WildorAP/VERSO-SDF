from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join

from verso_integrations import operations
from verso_integrations.models import Sep24DepositMeta, Sep24WithdrawMeta, VersoHistorySync
from verso_integrations.operation_forms import EmptyActionForm, HoldForm, RefundForm, RejectForm
from verso_integrations.operation_status import (
    DEPOSIT_STAGES,
    STAGE_COLORS,
    WITHDRAW_STAGES,
    deposit_stage,
    deposit_stage_q,
    withdraw_stage,
    withdraw_stage_q,
    active_hold_of,
    rejection_of,
)


def _stage_badge(stage):
    return format_html(
        '<span style="color:#fff;background:{};padding:2px 8px;border-radius:10px;'
        'white-space:nowrap;">{}</span>',
        STAGE_COLORS[stage],
        stage.label,
    )


class DepositStageFilter(admin.SimpleListFilter):
    title = "estado"
    parameter_name = "estado"

    def lookups(self, request, model_admin):
        return [(stage.value, stage.label) for stage in DEPOSIT_STAGES]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(deposit_stage_q(self.value()))
        return queryset


class WithdrawStageFilter(admin.SimpleListFilter):
    title = "estado"
    parameter_name = "estado"

    def lookups(self, request, model_admin):
        return [(stage.value, stage.label) for stage in WITHDRAW_STAGES]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(withdraw_stage_q(self.value()))
        return queryset


class OperationAdminMixin:
    """One button per allowed action on each operation; records are never deleted from here."""

    actions = None
    change_form_template = "admin/verso_integrations/operation_change_form.html"

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("transaction__verso_rejection")
            .prefetch_related("transaction__verso_holds")
        )

    def get_urls(self):
        urls = [
            path(
                "<path:object_id>/operacion/<str:action>/",
                self.admin_site.admin_view(self.operation_view),
                name=self._url_name("operation"),
            )
        ]
        return urls + super().get_urls()

    def _url_name(self, suffix):
        return f"{self.opts.app_label}_{self.opts.model_name}_{suffix}"

    def _action_links(self, meta):
        return [
            (
                operations.ACTION_LABELS[action],
                reverse(f"admin:{self._url_name('operation')}", args=[meta.pk, action]),
            )
            for action in operations.allowed_actions(meta)
        ]

    @admin.display(description="Acciones")
    def acciones(self, meta):
        links = self._action_links(meta)
        if not links:
            return "—"
        options = format_html_join(
            "", '<option value="{}">{}</option>', ((url, label) for label, url in links)
        )
        # Choosing an option only opens its confirmation page: nothing is applied from the list.
        return format_html(
            '<select autocomplete="off" aria-label="Acciones de la operación {}" '
            'onchange="if (this.value) window.location.href = this.value">'
            '<option value="">Elegir acción…</option>{}</select>',
            meta.pk,
            options,
        )

    @admin.display(description="Rechazo y retenciones")
    def historial(self, meta):
        lines = []
        for hold in meta.transaction.verso_holds.all():
            lines.append(
                f"Retenida por {hold.placed_by} el {hold.placed_at:%d/%m/%Y %H:%M}: {hold.note}"
            )
            if hold.released_at:
                lines.append(f"Liberada por {hold.released_by} el {hold.released_at:%d/%m/%Y %H:%M}")
        rejection = rejection_of(meta.transaction)
        if rejection:
            lines.append(
                f"Rechazada por {rejection.rejected_by} el {rejection.created_at:%d/%m/%Y %H:%M}: "
                f"{rejection.get_reason_display()}. {rejection.note}"
            )
            if rejection.refunded_at:
                lines.append(
                    f"Devuelta por {rejection.refunded_by} el {rejection.refunded_at:%d/%m/%Y %H:%M}: "
                    f"{rejection.refund_amount} · {rejection.refund_reference}"
                )
        if not lines:
            return "—"
        return format_html_join("", "<div>{}</div>", ((line,) for line in lines))

    def change_view(self, request, object_id, form_url="", extra_context=None):
        meta = self.get_object(request, object_id)
        context = dict(extra_context or {})
        context["operation_actions"] = self._action_links(meta) if meta else []
        return super().change_view(request, object_id, form_url, extra_context=context)

    def operation_view(self, request, object_id, action):
        meta = get_object_or_404(self.get_queryset(request), pk=object_id)
        if not self.has_change_permission(request, meta):
            raise PermissionDenied
        change_url = reverse(f"admin:{self._url_name('change')}", args=[meta.pk])
        if action not in operations.allowed_actions(meta):
            self.message_user(
                request, "Esa acción no está disponible en la etapa actual.", level=messages.ERROR
            )
            return redirect(change_url)

        data = request.POST if request.method == "POST" else None
        form = self._action_form(meta, action, data)
        if data is not None and form.is_valid():
            try:
                success = self._apply_action(meta, action, request.user, form.cleaned_data)
            except operations.OperationError as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
            else:
                self.message_user(request, success, level=messages.SUCCESS)
            return redirect(change_url)

        label = operations.ACTION_LABELS[action]
        return render(
            request,
            "admin/verso_integrations/operation_action.html",
            {
                **self.admin_site.each_context(request),
                "title": f"{label} · operación #{meta.pk}",
                "opts": self.opts,
                "meta": meta,
                "stage": operations.stage_of(meta),
                "active_hold": active_hold_of(meta.transaction),
                "releases_hold": action in (operations.CONFIRM, operations.PAY, operations.REJECT),
                "warning": self._action_warning(meta, action),
                "label": label,
                "form": form,
                "change_url": change_url,
            },
        )

    def _action_form(self, meta, action, data):
        if action == operations.REJECT:
            is_unconfirmed_deposit = operations.is_deposit(meta) and not meta.fiat_confirmed_at
            return RejectForm(data, ask_funds=is_unconfirmed_deposit)
        if action == operations.HOLD:
            return HoldForm(data)
        if action == operations.REFUND:
            reference_help = (
                "Número de la transferencia bancaria."
                if operations.is_deposit(meta)
                else "Hash de la transacción en la red."
            )
            return RefundForm(data, reference_help=reference_help)
        return EmptyActionForm(data)

    def _action_warning(self, meta, action):
        if action == operations.CONFIRM:
            return f"Al confirmar, saldrán automáticamente {meta.amount_usdc} USDC a la wallet del cliente."
        if action == operations.PAY:
            return f"Confirma solo si ya pagaste {meta.amount_fiat} {meta.fiat_currency} por el banco."
        if action == operations.REFUND:
            return "Esto solo deja constancia: la devolución se hace aparte."
        return None

    def _apply_action(self, meta, action, user, data):
        if action == operations.CONFIRM:
            operations.confirm_deposit(meta, user)
            return "Pago confirmado: el USDC saldrá en unos segundos."
        if action == operations.PAY:
            operations.pay_withdrawal(meta, user)
            return "Retiro marcado como pagado y completado."
        if action == operations.REJECT:
            rejection = operations.reject(
                meta,
                user,
                reason=data["reason"],
                note=data["note"],
                funds_received=data.get("funds_received", False),
            )
            if rejection.funds_received:
                return "Operación rechazada. Queda pendiente registrar la devolución."
            return "Operación rechazada."
        if action == operations.HOLD:
            operations.hold(meta, user, note=data["note"])
            return "Operación retenida."
        operations.register_refund(meta, user, amount=data["amount"], reference=data["reference"])
        return "Devolución registrada."


@admin.register(Sep24DepositMeta)
class Sep24DepositMetaAdmin(OperationAdminMixin, admin.ModelAdmin):
    list_display = (
        "id",
        "estado",
        "acciones",
        "transaction",
        "fiat_currency",
        "amount_pen",
        "tipo_cambio",
        "amount_usdc",
        "transfer_declared_at",
        "fiat_confirmed_at",
        "created_at",
    )
    list_filter = (DepositStageFilter, "fiat_confirmed_at", "transfer_declared_at")
    list_select_related = ("transaction",)
    search_fields = ("transaction__id", "transaction__stellar_account")
    readonly_fields = (
        "historial",
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

    @admin.display(description="Estado")
    def estado(self, meta):
        return _stage_badge(deposit_stage(meta))


@admin.register(Sep24WithdrawMeta)
class Sep24WithdrawMetaAdmin(OperationAdminMixin, admin.ModelAdmin):
    list_display = (
        "id",
        "estado",
        "acciones",
        "transaction",
        "fiat_currency",
        "amount_usdc",
        "amount_pen",
        "tipo_cambio",
        "payout_confirmed_at",
        "fiat_sent_at",
        "created_at",
    )
    list_filter = (WithdrawStageFilter, "fiat_currency", "fiat_sent_at", "payout_confirmed_at")
    list_select_related = ("transaction",)
    search_fields = (
        "transaction__id",
        "transaction__stellar_account",
        "payout_bank_details",
    )
    readonly_fields = (
        "historial",
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

    @admin.display(description="Estado")
    def estado(self, meta):
        return _stage_badge(withdraw_stage(meta))


@admin.register(VersoHistorySync)
class VersoHistorySyncAdmin(admin.ModelAdmin):
    """Estado de la sincronización del historial con VERSO (solo lectura)."""

    list_display = (
        "transaction",
        "result",
        "sent_status",
        "attempts",
        "last_attempt_at",
        "synced_at",
        "next_attempt_at",
    )
    list_filter = ("result",)
    search_fields = ("transaction__id", "transaction__stellar_account", "last_error")
    readonly_fields = [field.name for field in VersoHistorySync._meta.fields]
    actions = ("retry_now",)

    def has_add_permission(self, request):
        return False

    @admin.action(description="Reintentar ahora (reenviar en el próximo barrido)")
    def retry_now(self, request, queryset):
        updated = queryset.update(payload_hash="", attempted_hash="", next_attempt_at=None)
        self.message_user(
            request,
            f"{updated} operación(es) se reenviarán a VERSO en el próximo barrido.",
            level=messages.SUCCESS,
        )
