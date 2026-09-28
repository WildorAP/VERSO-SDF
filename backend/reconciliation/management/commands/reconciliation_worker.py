"""Main Railway worker: RPC sync + periodic reconcile."""

from __future__ import annotations

import logging
import signal
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from reconciliation.config import (
    hot_wallet,
    network_passphrase,
    poll_seconds,
    reconcile_interval_seconds,
    stellar_rpc_url,
    usdc_contract_id,
)
from reconciliation.models import SyncState
from reconciliation.reconcile import reconcile_once
from reconciliation.rpc_client import RpcClient
from reconciliation.sync import sync_once

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "D3 reconciliation worker (RPC poll + balance reconcile)."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true", help="Run until SIGTERM.")

    def handle(self, *args, **options):
        self._validate_config()
        state = SyncState.get()
        rpc = RpcClient(stellar_rpc_url())
        stop = False

        def _handle_signal(signum, frame):
            nonlocal stop
            stop = True
            logger.info("Received signal %s — stopping after current cycle.", signum)

        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)

        last_reconcile = 0.0
        while not stop:
            close_old_connections()
            try:
                new_events = sync_once(rpc, state)
                state.refresh_from_db()
                now = time.monotonic()
                if now - last_reconcile >= reconcile_interval_seconds():
                    run = reconcile_once(rpc, state)
                    last_reconcile = now
                    logger.info(
                        "cycle events=%s ledger=%s delta=%s status=%s",
                        new_events,
                        state.last_processed_ledger,
                        run.delta,
                        run.status,
                    )
                else:
                    logger.info(
                        "sync events=%s ledger=%s",
                        new_events,
                        state.last_processed_ledger,
                    )
            except Exception:
                logger.exception("Worker cycle failed")
                time.sleep(min(60, poll_seconds() * 4))
                continue

            if not options["loop"]:
                break
            time.sleep(poll_seconds())

    def _validate_config(self) -> None:
        wallet = hot_wallet()
        passphrase = network_passphrase()
        contract = usdc_contract_id()
        try:
            state = SyncState.get()
        except RuntimeError as exc:
            raise CommandError(str(exc)) from exc
        if state.network_passphrase != passphrase:
            raise CommandError("SyncState network_passphrase mismatch.")
        if state.hot_wallet != wallet:
            raise CommandError("SyncState hot_wallet mismatch.")
        if state.asset_contract_id != contract:
            raise CommandError("SyncState asset_contract_id mismatch.")
        stellar_rpc_url()
