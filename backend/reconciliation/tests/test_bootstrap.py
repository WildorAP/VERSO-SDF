from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from reconciliation.models import Discrepancy, LedgerEntry, SyncState

HOT = "GBTV5QYBPGHGT2SVUHCFRRKFFWUWHOEPKH7QAXGTJHFGFYZRIE24UOPB"
COMMAND = "reconciliation.management.commands.reconciliation_bootstrap"


@patch(f"{COMMAND}.stellar_rpc_url", return_value="https://rpc.test")
@patch(f"{COMMAND}.hot_wallet", return_value=HOT)
@patch(f"{COMMAND}.RpcClient")
class BootstrapForceTests(TestCase):
    def test_force_closes_open_discrepancies_before_reset(self, mock_rpc_cls, _wallet, _url):
        mock_rpc_cls.return_value.usdc_balance.return_value = (Decimal("50.0000000"), 500)
        call_command("reconciliation_bootstrap", stdout=StringIO())

        entry = LedgerEntry.objects.create(
            stellar_tx_hash="f" * 64,
            amount_usdc=Decimal("1.0000000"),
            direction=LedgerEntry.Direction.INBOUND,
            event_id="evt-force",
            event_type=LedgerEntry.EventType.TRANSFER,
            ledger=501,
            ledger_closed_at=timezone.now(),
            to_address=HOT,
        )
        Discrepancy.objects.create(
            kind=Discrepancy.Kind.UNMATCHED_INBOUND,
            severity=Discrepancy.Severity.WARNING,
            dedupe_key="unmatched_inbound:evt-force",
            message="open",
            ledger_entry=entry,
        )

        call_command("reconciliation_bootstrap", "--force", stdout=StringIO())

        self.assertFalse(LedgerEntry.objects.exists())
        self.assertFalse(Discrepancy.objects.filter(resolved_at__isnull=True).exists())
        closed = Discrepancy.objects.get(dedupe_key="unmatched_inbound:evt-force")
        self.assertEqual(closed.resolution, Discrepancy.Resolution.FALSE_POSITIVE)
        self.assertEqual(SyncState.get().opening_balance, Decimal("50.0000000"))
