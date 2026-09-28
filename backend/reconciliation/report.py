"""14-day reconciliation report export (CSV + JSON)."""

from __future__ import annotations

import csv
import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.db.models import Avg, Count, Max, Min, Q
from django.utils import timezone

from reconciliation.config import max_stale_seconds, reconcile_interval_seconds
from reconciliation.models import Discrepancy, LedgerEntry, ReconciliationRun, SyncState


def _decimal_default(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError


def generate_report(*, days: int, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    end = timezone.now()
    start = end - timedelta(days=days)
    state = SyncState.get()

    runs = ReconciliationRun.objects.filter(run_at__gte=start, run_at__lte=end)
    entries = LedgerEntry.objects.filter(ledger_closed_at__gte=start, ledger_closed_at__lte=end)
    discrepancies = Discrepancy.objects.filter(detected_at__gte=start, detected_at__lte=end).exclude(
        kind=Discrepancy.Kind.TEST
    )

    open_now = Discrepancy.objects.filter(resolved_at__isnull=True).exclude(
        kind=Discrepancy.Kind.TEST
    ).count()

    run_stats = runs.aggregate(
        total=Count("id"),
        ok=Count("id", filter=Q(status=ReconciliationRun.Status.OK)),
        mismatch=Count("id", filter=Q(status=ReconciliationRun.Status.MISMATCH)),
        skipped=Count("id", filter=Q(status=ReconciliationRun.Status.SKIPPED)),
        max_delta=Max("delta"),
        min_delta=Min("delta"),
    )
    total_runs = run_stats["total"] or 0
    ok_runs = run_stats["ok"] or 0

    by_direction = {
        row["direction"]: row["c"]
        for row in entries.values("direction").annotate(c=Count("id"))
    }
    by_match_kind = {
        row["match_kind"] or "unknown": row["c"]
        for row in entries.values("match_kind").annotate(c=Count("id"))
    }

    resolved = discrepancies.filter(resolved_at__isnull=False)
    resolution_seconds = []
    for row in resolved.iterator():
        if row.resolved_at:
            resolution_seconds.append((row.resolved_at - row.detected_at).total_seconds())
    avg_resolution = (
        sum(resolution_seconds) / len(resolution_seconds) if resolution_seconds else None
    )

    latest_run = runs.order_by("-run_at").first()
    max_stale = max_stale_seconds()
    run_times = list(runs.order_by("run_at").values_list("run_at", flat=True))
    heartbeat_gaps = []
    for previous, current in zip(run_times, run_times[1:]):
        gap_seconds = (current - previous).total_seconds()
        if gap_seconds > max_stale:
            heartbeat_gaps.append(
                {
                    "from": previous.isoformat(),
                    "to": current.isoformat(),
                    "seconds": int(gap_seconds),
                }
            )

    summary = {
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "network_passphrase": state.network_passphrase,
        "hot_wallet": state.hot_wallet,
        "asset_contract_id": state.asset_contract_id,
        "start_ledger": state.start_ledger,
        "opening_balance": str(state.opening_balance),
        "onchain_balance_final": str(latest_run.onchain_balance) if latest_run else None,
        "internal_balance_final": str(latest_run.internal_balance) if latest_run else None,
        "runs_total": total_runs,
        "runs_ok": ok_runs,
        "runs_mismatch": run_stats["mismatch"] or 0,
        "runs_skipped": run_stats["skipped"] or 0,
        "runs_ok_percent": (ok_runs / total_runs * 100) if total_runs else None,
        "events_total": entries.count(),
        "events_by_direction": by_direction,
        "events_by_match_kind": by_match_kind,
        "discrepancies_detected": discrepancies.count(),
        "discrepancies_resolved": resolved.count(),
        "discrepancies_open_now": open_now,
        "discrepancies_by_kind": {
            row["kind"]: row["c"]
            for row in discrepancies.values("kind").annotate(c=Count("id"))
        },
        "avg_resolution_seconds": avg_resolution,
        "max_ledger_lag": runs.aggregate(v=Max("ledger_lag"))["v"],
        "heartbeat_gaps_over_stale_threshold": heartbeat_gaps,
        "heartbeat_stale_threshold_seconds": max_stale,
        "reconcile_interval_seconds": reconcile_interval_seconds(),
        "no_unresolved_discrepancies": open_now == 0,
        "method": "Stellar RPC getEvents + getLedgerEntries trustline balance",
    }

    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=_decimal_default), encoding="utf-8")

    proposal_columns = [
        "stellar_tx_hash",
        "amount_usdc",
        "amount_pen",
        "amount_usd",
        "direction",
        "kyc_status",
        "fiat_rail",
        "fiat_status",
        "anchor_callback_id",
        "created_at",
        "updated_at",
    ]
    extra_columns = [
        "event_id",
        "ledger",
        "ledger_closed_at",
        "match_kind",
        "counterparty",
    ]
    ledger_path = output_dir / "ledger_entries.csv"
    with ledger_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(proposal_columns + extra_columns)
        for entry in entries.order_by("ledger", "event_id"):
            writer.writerow(
                [
                    entry.stellar_tx_hash,
                    entry.amount_usdc,
                    entry.amount_pen,
                    entry.amount_usd,
                    entry.direction,
                    entry.kyc_status,
                    entry.fiat_rail,
                    entry.fiat_status,
                    entry.anchor_callback_id,
                    entry.created_at.isoformat(),
                    entry.updated_at.isoformat(),
                    entry.event_id,
                    entry.ledger,
                    entry.ledger_closed_at.isoformat(),
                    entry.match_kind,
                    entry.counterparty,
                ]
            )

    disc_path = output_dir / "discrepancies.csv"
    with disc_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "kind",
                "severity",
                "dedupe_key",
                "message",
                "detected_at",
                "resolved_at",
                "resolution",
                "resolution_note",
            ]
        )
        for row in discrepancies.order_by("detected_at"):
            writer.writerow(
                [
                    row.kind,
                    row.severity,
                    row.dedupe_key,
                    row.message,
                    row.detected_at.isoformat(),
                    row.resolved_at.isoformat() if row.resolved_at else "",
                    row.resolution,
                    row.resolution_note,
                ]
            )

    runs_path = output_dir / "runs.csv"
    hourly_buckets: dict[str, dict] = {}
    for run in runs.order_by("run_at"):
        hour_key = run.run_at.replace(minute=0, second=0, microsecond=0).isoformat()
        bucket = hourly_buckets.setdefault(
            hour_key,
            {
                "run_count": 0,
                "min_delta": run.delta,
                "max_delta": run.delta,
                "mismatch_count": 0,
            },
        )
        bucket["run_count"] += 1
        bucket["min_delta"] = min(bucket["min_delta"], run.delta)
        bucket["max_delta"] = max(bucket["max_delta"], run.delta)
        if run.status == ReconciliationRun.Status.MISMATCH:
            bucket["mismatch_count"] += 1
    with runs_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["hour", "run_count", "min_delta", "max_delta", "mismatch_count"]
        )
        for hour_key in sorted(hourly_buckets.keys()):
            bucket = hourly_buckets[hour_key]
            writer.writerow(
                [
                    hour_key,
                    bucket["run_count"],
                    bucket["min_delta"],
                    bucket["max_delta"],
                    bucket["mismatch_count"],
                ]
            )

    readme = output_dir / "README.txt"
    readme.write_text(
        "VERSO D3 reconciliation report.\n"
        "Columns match the SCF proposal schema plus technical fields.\n"
        "On-chain ingestion: Stellar RPC getEvents (CAP-67 USDC SAC).\n"
        "Balance check: RPC getLedgerEntries (USDC trustline).\n",
        encoding="utf-8",
    )

    return summary
