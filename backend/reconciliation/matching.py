"""Link LedgerEntry rows to Polaris / FiatDeposit and enrich fiat/KYC fields."""

from __future__ import annotations

import logging
from decimal import Decimal

from django.core.cache import cache
from django.utils import timezone
from polaris.models import Transaction

from reconciliation.config import match_grace_seconds
from reconciliation.models import Discrepancy, LedgerEntry
from verso_integrations.kyc_bridge import fetch_client_by_stellar_key
from verso_integrations.models import FiatDeposit, Sep24DepositMeta, Sep24WithdrawMeta

logger = logging.getLogger(__name__)

KYC_CACHE_SECONDS = 600


def _open_discrepancy(
    *,
    kind: str,
    severity: str,
    dedupe_key: str,
    message: str,
    ledger_entry: LedgerEntry | None = None,
    polaris_transaction: Transaction | None = None,
    expected: Decimal | None = None,
    actual: Decimal | None = None,
) -> Discrepancy:
    existing = Discrepancy.objects.filter(dedupe_key=dedupe_key, resolved_at__isnull=True).first()
    if existing:
        changed = False
        if expected is not None and existing.expected != expected:
            existing.expected = expected
            changed = True
        if actual is not None and existing.actual != actual:
            existing.actual = actual
            changed = True
        if message and existing.message != message:
            existing.message = message
            changed = True
        if changed:
            existing.save(update_fields=["expected", "actual", "message"])
        return existing
    return Discrepancy.objects.create(
        kind=kind,
        severity=severity,
        dedupe_key=dedupe_key,
        message=message,
        ledger_entry=ledger_entry,
        polaris_transaction=polaris_transaction,
        expected=expected,
        actual=actual,
    )


def _resolve_open(dedupe_key: str, resolution: str = Discrepancy.Resolution.AUTO_CLEARED) -> None:
    for discrepancy in Discrepancy.objects.filter(dedupe_key=dedupe_key, resolved_at__isnull=True):
        discrepancy.resolved_at = timezone.now()
        discrepancy.resolution = resolution
        discrepancy.save(update_fields=["resolved_at", "resolution"])


def _lookup_kyc_status(counterparty: str) -> str:
    if not counterparty or not counterparty.startswith("G"):
        return "unknown"
    cache_key = f"recon:kyc:{counterparty}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        payload = fetch_client_by_stellar_key(counterparty)
    except Exception as exc:
        logger.warning("KYC lookup failed for %s: %s", counterparty, exc)
        status = "unknown"
        cache.set(cache_key, status, KYC_CACHE_SECONDS)
        return status
    if payload is None:
        status = "not_found"
    else:
        status = str(payload.get("kyc_status") or payload.get("status") or "unknown")
    cache.set(cache_key, status, KYC_CACHE_SECONDS)
    return status


def enrich(entry: LedgerEntry) -> None:
    update_fields: list[str] = []

    if entry.counterparty:
        kyc = _lookup_kyc_status(entry.counterparty)
        if entry.kyc_status != kyc:
            entry.kyc_status = kyc
            update_fields.append("kyc_status")

    if entry.polaris_transaction_id:
        tx = entry.polaris_transaction
        try:
            if tx.kind == Transaction.KIND.withdrawal:
                meta = tx.verso_withdraw_meta
                entry.fiat_rail = "cci_cce"
                entry.fiat_status = (
                    LedgerEntry.FiatStatus.SENT
                    if meta.fiat_sent_at
                    else LedgerEntry.FiatStatus.PENDING
                )
                if meta.fiat_currency == Sep24WithdrawMeta.FiatCurrency.USD:
                    entry.amount_usd = meta.amount_pen
                    entry.amount_pen = None
                else:
                    entry.amount_pen = meta.amount_pen
                    entry.amount_usd = None
            elif tx.kind == Transaction.KIND.deposit:
                meta = tx.verso_deposit_meta
                entry.fiat_rail = "cci_cce"
                entry.fiat_status = (
                    LedgerEntry.FiatStatus.CONFIRMED
                    if meta.fiat_confirmed_at
                    else LedgerEntry.FiatStatus.PENDING
                )
                if meta.fiat_currency == Sep24DepositMeta.FiatCurrency.USD:
                    entry.amount_usd = meta.amount_pen
                    entry.amount_pen = None
                else:
                    entry.amount_pen = meta.amount_pen
                    entry.amount_usd = None
        except (Sep24WithdrawMeta.DoesNotExist, Sep24DepositMeta.DoesNotExist):
            pass
        update_fields.extend(
            ["fiat_rail", "fiat_status", "amount_pen", "amount_usd", "updated_at"]
        )
    elif entry.fiat_deposit_id:
        deposit = entry.fiat_deposit
        entry.fiat_rail = "cci_cce"
        entry.amount_pen = deposit.amount_pen
        entry.amount_usd = None
        entry.fiat_status = (
            LedgerEntry.FiatStatus.CONFIRMED
            if deposit.status != FiatDeposit.Status.PENDING
            else LedgerEntry.FiatStatus.PENDING
        )
        update_fields.extend(
            ["fiat_rail", "fiat_status", "amount_pen", "amount_usd", "updated_at"]
        )

    if update_fields:
        entry.save(update_fields=list(dict.fromkeys(update_fields)))


def _apply_match(
    entry: LedgerEntry,
    *,
    tx: Transaction | None = None,
    fiat_deposit: FiatDeposit | None = None,
    match_kind: str,
) -> None:
    entry.match_status = LedgerEntry.MatchStatus.MATCHED
    entry.match_kind = match_kind
    if tx:
        entry.polaris_transaction = tx
        entry.anchor_callback_id = str(tx.id)
        if tx.amount_in and entry.direction == LedgerEntry.Direction.INBOUND:
            expected = tx.amount_in
        elif tx.amount_out and entry.direction == LedgerEntry.Direction.OUTBOUND:
            expected = tx.amount_out
        else:
            expected = None
        if expected is not None and expected != entry.amount_usdc:
            _open_discrepancy(
                kind=Discrepancy.Kind.AMOUNT_MISMATCH,
                severity=Discrepancy.Severity.WARNING,
                dedupe_key=f"amount_mismatch:{entry.event_id}",
                message=(
                    f"Ledger entry {entry.amount_usdc} USDC != Polaris {expected} USDC "
                    f"for tx {tx.id}"
                ),
                ledger_entry=entry,
                polaris_transaction=tx,
                expected=expected,
                actual=entry.amount_usdc,
            )
    if fiat_deposit:
        entry.fiat_deposit = fiat_deposit
        entry.anchor_callback_id = f"fiatdeposit:{fiat_deposit.pk}"
        if fiat_deposit.amount_usdc != entry.amount_usdc:
            _open_discrepancy(
                kind=Discrepancy.Kind.AMOUNT_MISMATCH,
                severity=Discrepancy.Severity.WARNING,
                dedupe_key=f"amount_mismatch:{entry.event_id}",
                message=(
                    f"Ledger entry {entry.amount_usdc} USDC != FiatDeposit "
                    f"{fiat_deposit.amount_usdc} USDC"
                ),
                ledger_entry=entry,
                expected=fiat_deposit.amount_usdc,
                actual=entry.amount_usdc,
            )
    entry.save()
    enrich(entry)
    if entry.direction == LedgerEntry.Direction.INBOUND:
        _resolve_open(f"{Discrepancy.Kind.UNMATCHED_INBOUND}:{entry.event_id}")
    else:
        _resolve_open(f"{Discrepancy.Kind.UNMATCHED_OUTBOUND}:{entry.event_id}")


def _try_match_entry(entry: LedgerEntry) -> bool:
    if entry.match_status == LedgerEntry.MatchStatus.CLASSIFIED:
        return True

    if entry.direction == LedgerEntry.Direction.INBOUND:
        tx = None
        if entry.memo_type == "hash" and entry.memo_raw:
            tx = Transaction.objects.filter(
                kind=Transaction.KIND.withdrawal,
                memo=entry.memo_raw,
                memo_type=Transaction.MEMO_TYPES.hash,
            ).first()
        if tx is None:
            tx = Transaction.objects.filter(
                kind=Transaction.KIND.withdrawal,
                stellar_transaction_id=entry.stellar_tx_hash,
            ).first()
        if tx:
            _apply_match(entry, tx=tx, match_kind="sep24_withdrawal")
            return True
        return False

    tx = Transaction.objects.filter(
        kind=Transaction.KIND.deposit,
        stellar_transaction_id=entry.stellar_tx_hash,
    ).first()
    if tx:
        _apply_match(entry, tx=tx, match_kind="sep24_deposit")
        return True

    fiat = FiatDeposit.objects.filter(stellar_tx_hash=entry.stellar_tx_hash).first()
    if fiat:
        _apply_match(entry, fiat_deposit=fiat, match_kind="fiat_deposit")
        return True

    if entry.event_type in ("mint", "burn", "clawback"):
        entry.match_kind = "other"
        entry.save(update_fields=["match_kind", "updated_at"])
    return False


def refresh_pending_fiat() -> int:
    """Re-enrich matched entries whose fiat leg is not final yet; return count checked."""
    qs = LedgerEntry.objects.filter(
        match_status=LedgerEntry.MatchStatus.MATCHED,
        fiat_status=LedgerEntry.FiatStatus.PENDING,
    ).select_related("polaris_transaction", "fiat_deposit")
    count = 0
    for entry in qs:
        enrich(entry)
        count += 1
    return count


def match_pending() -> int:
    """Match pending/unmatched entries; return count newly matched."""
    grace = match_grace_seconds()
    now = timezone.now()
    matched = 0
    qs = LedgerEntry.objects.filter(
        match_status__in=[
            LedgerEntry.MatchStatus.PENDING,
            LedgerEntry.MatchStatus.UNMATCHED,
        ]
    ).order_by("ledger", "event_id")

    for entry in qs:
        if _try_match_entry(entry):
            matched += 1
            continue

        age = (now - entry.ledger_closed_at).total_seconds()
        if age < grace:
            continue

        if entry.match_status != LedgerEntry.MatchStatus.UNMATCHED:
            entry.match_status = LedgerEntry.MatchStatus.UNMATCHED
            entry.save(update_fields=["match_status", "updated_at"])
            enrich(entry)

        kind = (
            Discrepancy.Kind.UNMATCHED_INBOUND
            if entry.direction == LedgerEntry.Direction.INBOUND
            else Discrepancy.Kind.UNMATCHED_OUTBOUND
        )
        _open_discrepancy(
            kind=kind,
            severity=Discrepancy.Severity.WARNING,
            dedupe_key=f"{kind}:{entry.event_id}",
            message=f"No internal match for {entry.direction} {entry.amount_usdc} USDC",
            ledger_entry=entry,
        )
    return matched
