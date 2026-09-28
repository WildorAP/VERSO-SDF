"""Run one reconciliation balance check."""

from django.core.management.base import BaseCommand

from reconciliation.config import stellar_rpc_url
from reconciliation.models import SyncState
from reconciliation.reconcile import reconcile_once
from reconciliation.rpc_client import RpcClient


class Command(BaseCommand):
    help = "Run reconcile_once (debug)."

    def handle(self, *args, **options):
        state = SyncState.get()
        rpc = RpcClient(stellar_rpc_url())
        run = reconcile_once(rpc, state)
        self.stdout.write(f"Run {run.id} status={run.status} delta={run.delta}")
