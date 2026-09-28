"""Compare on-chain USDC balance with the internal ledger."""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone
from polaris.models import Transaction

from reconciliation.config import match_grace_seconds, max_stale_seconds, usdc_asset
from reconciliation.matching import match_pending, refresh_pending_fiat
from reconciliation.models import Discrepancy, LedgerEntry, ReconciliationRun, SyncState
from reconciliation.rpc_client import RpcClient
from reconciliation.sync import sync_once

logger = logging.getLogger(__name__)


def _internal_balance(state: SyncState, ledger: int) -> Decimal:
    total = Decimal("0")
    for entry in LedgerEntry.objects.filter(ledger__lte=ledger).iterator():
        total += entry.signed_amount
    return state.opening_balance + total


def _ensure_sync_caught_up(rpc: RpcClient, state: SyncState, latest: int, attempts: int = 3) -> bool:
    for _ in range(attempts):
        if state.last_processed_ledger >= latest:
            return True
        sync_once(rpc, state)
        state.refresh_from_db()
    return state.last_processed_ledger >= latest


def reconcile_once(rpc: RpcClient, state: SyncState, *, publish_alerts: bool = True) -> ReconciliationRun:
    from reconciliation.alerts import get_alert_backend

    match_pending()
    refresh_pending_fiat()
    balance, latest = rpc.usdc_balance(state.hot_wallet, usdc_asset())
    ledger_lag = max(0, latest - state.last_processed_ledger)

    if not _ensure_sync_caught_up(rpc, state, latest):
        run = ReconciliationRun.objects.create(
            ledger=latest,
            onchain_balance=balance,
            internal_balance=balance,
            delta=Decimal("0"),
            events_total=LedgerEntry.objects.count(),
            unmatched_count=LedgerEntry.objects.filter(
                match_status=LedgerEntry.MatchStatus.UNMATCHED
            ).count(),
            open_discrepancies=Discrepancy.objects.filter(resolved_at__isnull=True)
            .exclude(kind=Discrepancy.Kind.TEST)
            .count(),
            ledger_lag=ledger_lag,
            status=ReconciliationRun.Status.SKIPPED,
            detail=(
                f"Sync lag: last_processed={state.last_processed_ledger} "
                f"latest={latest}"
            ),
        )
        if publish_alerts:
            get_alert_backend().publish_run(run, state)
        return run

    internal = _internal_balance(state, latest)
    delta = balance - internal
    events_total = LedgerEntry.objects.count()
    unmatched_count = LedgerEntry.objects.filter(
        match_status=LedgerEntry.MatchStatus.UNMATCHED
    ).count()

    if delta != Decimal("0"):
        existing = Discrepancy.objects.filter(
            dedupe_key="balance_mismatch",
            resolved_at__isnull=True,
        ).first()
        if existing:
            existing.expected = balance
            existing.actual = internal
            existing.message = f"On-chain {balance} != internal {internal} (delta {delta})"
            existing.save(update_fields=["expected", "actual", "message"])
        else:
            Discrepancy.objects.create(
                kind=Discrepancy.Kind.BALANCE_MISMATCH,
                severity=Discrepancy.Severity.CRITICAL,
                dedupe_key="balance_mismatch",
                expected=balance,
                actual=internal,
                message=f"On-chain {balance} != internal {internal} (delta {delta})",
            )
    else:
        for open_row in Discrepancy.objects.filter(
            dedupe_key="balance_mismatch",
            resolved_at__isnull=True,
        ):
            open_row.resolved_at = timezone.now()
            open_row.resolution = Discrepancy.Resolution.AUTO_CLEARED
            open_row.save(update_fields=["resolved_at", "resolution"])

    grace = match_grace_seconds()
    cutoff = timezone.now() - timezone.timedelta(seconds=grace)
    missing = (
        Transaction.objects.filter(
            status=Transaction.STATUS.completed,
            completed_at__gte=state.observation_started_at,
        )
        .exclude(Q(stellar_transaction_id__isnull=True) | Q(stellar_transaction_id=""))
    )
    for tx in missing:
        if tx.completed_at and tx.completed_at > cutoff:
            continue
        stellar_hash = (tx.stellar_transaction_id or "").strip()
        if not stellar_hash:
            continue
        if LedgerEntry.objects.filter(stellar_tx_hash=stellar_hash).exists():
            continue
        if Discrepancy.objects.filter(
            dedupe_key=f"missing_onchain:{tx.id}",
            resolved_at__isnull=True,
        ).exists():
            continue
        Discrepancy.objects.create(
            kind=Discrepancy.Kind.MISSING_ONCHAIN,
            severity=Discrepancy.Severity.CRITICAL,
            dedupe_key=f"missing_onchain:{tx.id}",
            polaris_transaction=tx,
            message=(
                f"Completed Polaris tx {tx.id} hash {stellar_hash} not in ledger"
            ),
        )

    stale_seconds = max_stale_seconds()
    if state.last_success_at:
        stale_for = (timezone.now() - state.last_success_at).total_seconds()
        if stale_for > stale_seconds:
            if not Discrepancy.objects.filter(
                dedupe_key="worker_stale",
                resolved_at__isnull=True,
            ).exists():
                Discrepancy.objects.create(
                    kind=Discrepancy.Kind.WORKER_STALE,
                    severity=Discrepancy.Severity.CRITICAL,
                    dedupe_key="worker_stale",
                    message=f"Sync stale for {int(stale_for)}s",
                )
        else:
            for row in Discrepancy.objects.filter(
                dedupe_key="worker_stale",
                resolved_at__isnull=True,
            ):
                row.resolved_at = timezone.now()
                row.resolution = Discrepancy.Resolution.AUTO_CLEARED
                row.save(update_fields=["resolved_at", "resolution"])

    open_count = (
        Discrepancy.objects.filter(resolved_at__isnull=True)
        .exclude(kind=Discrepancy.Kind.TEST)
        .count()
    )
    status = (
        ReconciliationRun.Status.OK if delta == Decimal("0") else ReconciliationRun.Status.MISMATCH
    )
    run = ReconciliationRun.objects.create(
        ledger=latest,
        onchain_balance=balance,
        internal_balance=internal,
        delta=delta,
        events_total=events_total,
        unmatched_count=unmatched_count,
        open_discrepancies=open_count,
        ledger_lag=ledger_lag,
        status=status,
        detail="" if delta == Decimal("0") else f"delta={delta}",
    )
    if publish_alerts:
        backend = get_alert_backend()
        backend.publish_run(run, state)
        for discrepancy in Discrepancy.objects.filter(
            resolved_at__isnull=True,
            alerted_at__isnull=True,
        ).exclude(kind=Discrepancy.Kind.TEST):
            backend.notify_discrepancy(discrepancy)
    return run
