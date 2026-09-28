"""Poll Stellar RPC getEvents and persist LedgerEntry rows."""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from reconciliation.events import UsdcMovement, build_event_filters, decode_event
from reconciliation.matching import match_pending
from reconciliation.models import Discrepancy, LedgerEntry, SyncState
from reconciliation.rpc_client import RpcClient, RpcClientError

logger = logging.getLogger(__name__)

EVENT_PAGE_LIMIT = 200


def movement_to_defaults(movement: UsdcMovement, hot_wallet: str) -> dict:
    direction = (
        LedgerEntry.Direction.INBOUND
        if movement.to_address == hot_wallet
        else LedgerEntry.Direction.OUTBOUND
    )
    counterparty = (
        movement.from_address
        if direction == LedgerEntry.Direction.INBOUND
        else movement.to_address
    )
    valid_event_types = {choice.value for choice in LedgerEntry.EventType}
    event_type = movement.event_type
    if event_type not in valid_event_types:
        event_type = LedgerEntry.EventType.TRANSFER

    return {
        "stellar_tx_hash": movement.tx_hash,
        "amount_usdc": movement.amount,
        "direction": direction,
        "event_type": event_type,
        "ledger": movement.ledger,
        "ledger_closed_at": movement.ledger_closed_at,
        "from_address": movement.from_address,
        "to_address": movement.to_address,
        "counterparty": counterparty,
        "memo_raw": movement.memo_raw,
        "memo_type": movement.memo_type,
        "raw": movement.raw,
        "match_status": LedgerEntry.MatchStatus.PENDING,
    }


def _record_gap_error(state: SyncState, message: str) -> None:
    state.last_error = message
    state.last_error_at = timezone.now()
    state.save(update_fields=["last_error", "last_error_at", "updated_at"])
    if not Discrepancy.objects.filter(
        dedupe_key="worker_stale:gap",
        resolved_at__isnull=True,
    ).exists():
        Discrepancy.objects.create(
            kind=Discrepancy.Kind.WORKER_STALE,
            severity=Discrepancy.Severity.CRITICAL,
            dedupe_key="worker_stale:gap",
            message=message,
        )


def _fetch_events_page(
    rpc: RpcClient,
    *,
    hot_wallet: str,
    asset_contract_id: str,
    cursor: str | None,
    start_ledger: int | None,
) -> tuple:
    filters = build_event_filters(asset_contract_id, hot_wallet)
    return rpc.get_events(
        start_ledger=start_ledger,
        cursor=cursor,
        filters=filters,
        limit=EVENT_PAGE_LIMIT,
    )


def sync_once(rpc: RpcClient, state: SyncState, *, dry_run: bool = False) -> int:
    """Fetch new RPC events; return count of newly inserted (or dry-run seen) rows."""
    hot_wallet = state.hot_wallet

    if dry_run:
        cursor = state.cursor or None
        start_ledger = None if cursor else state.start_ledger
        seen = 0
        response = _fetch_events_page(
            rpc,
            hot_wallet=hot_wallet,
            asset_contract_id=state.asset_contract_id,
            cursor=cursor,
            start_ledger=start_ledger,
        )
        for event in response.events:
            movement = decode_event(event, hot_wallet)
            if movement is None:
                continue
            seen += 1
            logger.info(
                "dry-run event %s %s %s USDC tx=%s (cursor NOT advanced)",
                movement.event_type,
                movement.from_address[:8],
                movement.amount,
                movement.tx_hash[:16],
            )
        return seen

    new_count = 0
    try:
        with transaction.atomic():
            locked = SyncState.objects.select_for_update().get(pk=state.pk)
            cursor = locked.cursor or None
            start_ledger = None if cursor else locked.start_ledger

            while True:
                response = _fetch_events_page(
                    rpc,
                    hot_wallet=hot_wallet,
                    asset_contract_id=locked.asset_contract_id,
                    cursor=cursor,
                    start_ledger=start_ledger,
                )

                page_new = 0
                max_ledger = locked.last_processed_ledger
                for event in response.events:
                    movement = decode_event(event, hot_wallet)
                    if movement is None:
                        continue
                    _, created = LedgerEntry.objects.get_or_create(
                        event_id=movement.event_id,
                        defaults=movement_to_defaults(movement, hot_wallet),
                    )
                    if created:
                        page_new += 1
                    max_ledger = max(max_ledger, movement.ledger)

                new_count += page_new
                cursor = response.cursor
                locked.cursor = cursor or ""
                locked.last_processed_ledger = max(
                    locked.last_processed_ledger,
                    max_ledger,
                    response.latest_ledger,
                )
                locked.last_success_at = timezone.now()
                locked.last_error = ""
                locked.last_error_at = None
                locked.save()

                if len(response.events) < EVENT_PAGE_LIMIT:
                    break
                start_ledger = None
    except RpcClientError as exc:
        msg = str(exc).lower()
        if "retention" in msg or "before" in msg:
            _record_gap_error(state, f"RPC cursor/start ledger out of retention: {exc}")
        SyncState.objects.filter(pk=state.pk).update(
            last_error=str(exc),
            last_error_at=timezone.now(),
        )
        state.refresh_from_db()
        raise

    state.refresh_from_db()
    if new_count:
        match_pending()
    return new_count
