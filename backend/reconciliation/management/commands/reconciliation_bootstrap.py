"""Initialize SyncState with opening USDC balance from RPC."""

from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from reconciliation.config import (
    hot_wallet,
    network_passphrase,
    stellar_rpc_url,
    usdc_asset,
    usdc_contract_id,
)
from reconciliation.models import Discrepancy, LedgerEntry, SyncState
from reconciliation.rpc_client import RpcClient
from reconciliation.sync import sync_once


class Command(BaseCommand):
    help = "Initialize reconciliation SyncState (run once before the worker)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Replace existing SyncState.",
        )
        parser.add_argument(
            "--start-ledger",
            type=int,
            default=None,
            help="Optional ledger to backfill events from (within RPC retention).",
        )

    def handle(self, *args, **options):
        if SyncState.objects.filter(pk=1).exists() and not options["force"]:
            raise CommandError("SyncState already exists — use --force to replace.")

        if options["force"]:
            closed = Discrepancy.objects.filter(resolved_at__isnull=True).update(
                resolved_at=timezone.now(),
                resolution=Discrepancy.Resolution.FALSE_POSITIVE,
                resolution_note="Closed by reconciliation_bootstrap --force (ledger reset).",
            )
            if closed:
                self.stdout.write(f"Closed {closed} open discrepancy(ies) before reset.")
            LedgerEntry.objects.all().delete()
            SyncState.objects.filter(pk=1).delete()

        wallet = hot_wallet()
        rpc = RpcClient(stellar_rpc_url())
        balance, latest = rpc.usdc_balance(wallet, usdc_asset())
        now = timezone.now()
        start_ledger = options["start_ledger"] or (latest + 1)

        if options["start_ledger"]:
            opening_balance = Decimal("0")
            last_processed = max(options["start_ledger"] - 1, 0)
        else:
            opening_balance = balance
            last_processed = latest

        state = SyncState.objects.create(
            network_passphrase=network_passphrase(),
            hot_wallet=wallet,
            asset_contract_id=usdc_contract_id(),
            start_ledger=start_ledger,
            cursor="",
            last_processed_ledger=last_processed,
            opening_balance=opening_balance,
            observation_started_at=now,
        )

        if options["start_ledger"]:
            inserted = sync_once(rpc, state)
            balance, latest = rpc.usdc_balance(wallet, usdc_asset())
            movement_total = Decimal("0")
            for entry in LedgerEntry.objects.all():
                movement_total += entry.signed_amount
            state.opening_balance = balance - movement_total
            state.last_processed_ledger = max(state.last_processed_ledger, latest)
            state.save(
                update_fields=[
                    "opening_balance",
                    "last_processed_ledger",
                    "cursor",
                    "updated_at",
                ]
            )
            self.stdout.write(f"Backfilled {inserted} ledger entries from RPC.")
        else:
            self.stdout.write("Observation starts at current ledger (no historical backfill).")

        state.refresh_from_db()
        self.stdout.write(
            self.style.SUCCESS(
                f"Bootstrap OK — hot={wallet} opening_balance={state.opening_balance} "
                f"start_ledger={state.start_ledger} latest={latest}"
            )
        )
