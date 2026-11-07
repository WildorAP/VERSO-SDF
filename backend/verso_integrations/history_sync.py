"""
Historial unificado: envío de las operaciones SEP-24 del Anchor al historial de VERSO.

El comando ``sync_history_to_verso`` (servicio SYNC) llama a ``sync_once`` en un bucle.
Cada barrido:

1. Toma las operaciones SEP-24 que ya tienen monto (Sep24DepositMeta / Sep24WithdrawMeta)
   y no están en ``incomplete``.
2. Arma su estado actual y calcula una huella. Si coincide con la del último envío
   aceptado, no hace nada.
3. Si cambió, la envía a ``POST /internal/anchor/transactions/`` de VERSO (idempotente).

Se usa un barrido y no señales porque Polaris a veces cambia el estado con
``QuerySet.update()``, que no dispara señales. Este módulo solo lee las tablas de
Polaris y de metas; únicamente escribe en ``VersoHistorySync``.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Callable

from django.db.models import Q
from django.utils import timezone
from polaris.models import Transaction

from verso_integrations.core_client import CoreClientError, SyncResponse, sync_anchor_transaction
from verso_integrations.models import Sep24DepositMeta, Sep24WithdrawMeta, VersoHistorySync

logger = logging.getLogger(__name__)

KIND_BY_POLARIS = {
    Transaction.KIND.deposit: "deposit",
    Transaction.KIND.withdrawal: "withdrawal",
}

# Espera entre reintentos por errores temporales (VERSO caído, timeout, 5xx, 401...).
TRANSIENT_BACKOFF_SECONDS = (30, 60, 120, 300, 600, 900)
# Espera para errores que requieren intervención (wallet no vinculada, datos rechazados).
NEEDS_ATTENTION_RETRY_SECONDS = 3600

Sender = Callable[[dict], SyncResponse]


@dataclass
class SyncStats:
    checked: int = 0
    up_to_date: int = 0
    waiting: int = 0
    sent: int = 0
    not_linked: int = 0
    rejected: int = 0
    retrying: int = 0
    dry_run: list[dict] = field(default_factory=list)

    @property
    def had_activity(self) -> bool:
        return bool(self.sent or self.not_linked or self.rejected or self.retrying or self.dry_run)

    def summary(self) -> str:
        return (
            f"revisadas={self.checked} al_dia={self.up_to_date} en_espera={self.waiting} "
            f"enviadas={self.sent} no_vinculadas={self.not_linked} rechazadas={self.rejected} "
            f"reintentando={self.retrying}"
        )


def _meta_for(transaction: Transaction):
    for related in ("verso_deposit_meta", "verso_withdraw_meta"):
        try:
            return getattr(transaction, related)
        except (Sep24DepositMeta.DoesNotExist, Sep24WithdrawMeta.DoesNotExist):
            continue
    return None


def build_payload(transaction: Transaction) -> dict | None:
    """Estado actual de la operación en el formato del endpoint de VERSO, o None si no aplica."""
    kind = KIND_BY_POLARIS.get(transaction.kind)
    if kind is None or transaction.status == Transaction.STATUS.incomplete:
        return None
    meta = _meta_for(transaction)
    if meta is None:
        return None

    payload = {
        "anchor_transaction_id": str(transaction.id),
        "kind": kind,
        "status": str(transaction.status),
        "stellar_account": transaction.stellar_account,
        "fiat_currency": meta.fiat_currency,
        "amount_fiat": str(meta.amount_pen),
        "amount_usdc": str(meta.amount_usdc),
        "rate": str(meta.tipo_cambio),
        "started_at": transaction.started_at.isoformat(),
        "completed_at": transaction.completed_at.isoformat() if transaction.completed_at else None,
        "stellar_transaction_id": (transaction.stellar_transaction_id or "").strip(),
    }
    if kind == "withdrawal":
        details = meta.payout_bank_details or {}
        origen = (details.get("origen_fondos") or "").strip()
        if origen:
            payload["origen_fondos"] = origen
            if origen == "OTRO":
                payload["origen_fondos_otro"] = (details.get("origen_fondos_otro") or "").strip()
    return payload


def payload_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def candidate_transactions():
    return (
        Transaction.objects.filter(protocol=Transaction.PROTOCOL.sep24)
        .exclude(status=Transaction.STATUS.incomplete)
        .filter(Q(verso_deposit_meta__isnull=False) | Q(verso_withdraw_meta__isnull=False))
        .select_related("verso_deposit_meta", "verso_withdraw_meta", "verso_history_sync")
        .order_by("started_at")
    )


def _state_for(transaction: Transaction) -> VersoHistorySync | None:
    try:
        return transaction.verso_history_sync
    except VersoHistorySync.DoesNotExist:
        return None


def _is_due(state: VersoHistorySync | None, current_hash: str, now) -> bool:
    if state is None:
        return True
    if state.payload_hash == current_hash:
        return False
    if state.next_attempt_at is None or state.next_attempt_at <= now:
        return True
    # La operación cambió desde el último intento fallido: vale la pena intentar de inmediato.
    return state.attempted_hash != current_hash


def _transient_delay(attempts: int) -> int:
    index = min(max(attempts, 1), len(TRANSIENT_BACKOFF_SECONDS)) - 1
    return TRANSIENT_BACKOFF_SECONDS[index]


def _classify(response: SyncResponse) -> tuple[str, str]:
    """Devuelve (resultado, mensaje) para una respuesta de VERSO."""
    code = response.payload.get("code", "")
    error = response.payload.get("error", "")
    if response.status_code in (200, 201):
        return VersoHistorySync.Result.SYNCED, ""
    if response.status_code == 422 and code == "stellar_account_not_linked":
        return VersoHistorySync.Result.NOT_LINKED, error or "La wallet no está vinculada a un usuario de VERSO."
    if response.status_code in (400, 409):
        return VersoHistorySync.Result.REJECTED, f"HTTP {response.status_code} {code}: {error}".strip()
    if 300 <= response.status_code < 400:
        return (
            VersoHistorySync.Result.RETRYING,
            f"HTTP {response.status_code}: VERSO respondió una redirección; revisa VERSO_CORE_API_URL.",
        )
    if response.status_code == 404:
        return (
            VersoHistorySync.Result.RETRYING,
            "HTTP 404: el endpoint de historial no existe en VERSO (¿falta desplegar VERSO?).",
        )
    return VersoHistorySync.Result.RETRYING, f"HTTP {response.status_code} {code}: {error}".strip()


def _record(transaction, state, *, result, current_hash, now, payload, message="", response=None):
    state = state or VersoHistorySync(transaction=transaction)
    state.result = result
    state.attempted_hash = current_hash
    state.last_attempt_at = now
    if result == VersoHistorySync.Result.SYNCED:
        state.payload_hash = current_hash
        state.sent_status = payload["status"]
        state.attempts = 0
        state.last_error = ""
        state.synced_at = now
        state.next_attempt_at = None
        verso_id = (response.payload if response else {}).get("id")
        if isinstance(verso_id, int):
            state.verso_transaccion_id = verso_id
    else:
        state.attempts += 1
        state.last_error = message[:2000]
        if result == VersoHistorySync.Result.RETRYING:
            delay = _transient_delay(state.attempts)
        else:
            delay = NEEDS_ATTENTION_RETRY_SECONDS
        state.next_attempt_at = now + timedelta(seconds=delay)
    state.save()
    return state


def sync_once(*, sender: Sender | None = None, dry_run: bool = False, now=None) -> SyncStats:
    """Un barrido completo. Nunca lanza por errores de VERSO: los registra y sigue con la siguiente."""
    sender = sender or sync_anchor_transaction
    now = now or timezone.now()
    stats = SyncStats()

    for transaction in candidate_transactions():
        payload = build_payload(transaction)
        if payload is None:
            continue
        stats.checked += 1
        current_hash = payload_hash(payload)
        state = _state_for(transaction)

        if state is not None and state.payload_hash == current_hash:
            stats.up_to_date += 1
            continue
        if not _is_due(state, current_hash, now):
            stats.waiting += 1
            continue
        if dry_run:
            stats.dry_run.append(payload)
            continue

        try:
            response = sender(payload)
        except CoreClientError as exc:
            result, message, response = VersoHistorySync.Result.RETRYING, str(exc), None
        else:
            result, message = _classify(response)

        _record(
            transaction,
            state,
            result=result,
            current_hash=current_hash,
            now=now,
            payload=payload,
            message=message,
            response=response,
        )
        if result == VersoHistorySync.Result.SYNCED:
            stats.sent += 1
        elif result == VersoHistorySync.Result.NOT_LINKED:
            stats.not_linked += 1
            logger.warning("Historial VERSO: %s sin usuario vinculado: %s", transaction.id, message)
        elif result == VersoHistorySync.Result.REJECTED:
            stats.rejected += 1
            logger.error("Historial VERSO: %s rechazada: %s", transaction.id, message)
        else:
            stats.retrying += 1
            logger.warning("Historial VERSO: %s se reintentará: %s", transaction.id, message)

    return stats
