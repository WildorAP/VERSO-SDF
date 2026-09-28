"""Run one RPC sync cycle."""

from django.core.management.base import BaseCommand

from reconciliation.models import SyncState
from reconciliation.rpc_client import RpcClient
from reconciliation.config import stellar_rpc_url
from reconciliation.sync import sync_once


class Command(BaseCommand):
    help = "Fetch RPC events once (debug)."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        state = SyncState.get()
        rpc = RpcClient(stellar_rpc_url())
        count = sync_once(rpc, state, dry_run=options["dry_run"])
        self.stdout.write(f"New events: {count}")
