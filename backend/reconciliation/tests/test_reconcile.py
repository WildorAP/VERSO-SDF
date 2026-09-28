from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone
from polaris.models import Asset, Transaction

from reconciliation.models import Discrepancy, LedgerEntry, ReconciliationRun, SyncState
from reconciliation.reconcile import reconcile_once
from reconciliation.tests.factories import create_sync_state
from verso_integrations.polaris_setup import seed_polaris_t2


class ReconcileTests(TestCase):
    HOT = "GBTV5QYBPGHGT2SVUHCFRRKFFWUWHOEPKH7QAXGTJHFGFYZRIE24UOPB"

    @classmethod
    def setUpTestData(cls):
        seed_polaris_t2()
        cls.asset = Asset.objects.get(code="USDC")

    def setUp(self):
        self.observation_start = timezone.now() - timezone.timedelta(hours=2)
        self.state = create_sync_state(
            hot_wallet=self.HOT,
            last_processed_ledger=100,
            observation_started_at=self.observation_start,
        )
        self.rpc = MagicMock()
        self.rpc.usdc_balance.return_value = (Decimal("110.0000000"), 100)

    @patch("reconciliation.reconcile.sync_once")
    @patch("reconciliation.alerts.get_alert_backend")
    def test_balance_ok(self, mock_backend, mock_sync):
        LedgerEntry.objects.create(
            stellar_tx_hash="h" * 64,
            amount_usdc=Decimal("10.0000000"),
            direction=LedgerEntry.Direction.INBOUND,
            event_id="evt-1",
            event_type=LedgerEntry.EventType.TRANSFER,
            ledger=100,
            ledger_closed_at=timezone.now(),
            from_address="GCLIENT",
            to_address=self.HOT,
            counterparty="GCLIENT",
        )
        run = reconcile_once(self.rpc, self.state, publish_alerts=False)
        self.assertEqual(run.status, ReconciliationRun.Status.OK)
        self.assertEqual(run.delta, Decimal("0"))
        self.assertEqual(run.ledger_lag, 0)

    @patch("reconciliation.reconcile.sync_once")
    def test_balance_mismatch_creates_discrepancy(self, mock_sync):
        run = reconcile_once(self.rpc, self.state, publish_alerts=False)
        self.assertEqual(run.status, ReconciliationRun.Status.MISMATCH)
        self.assertTrue(
            Discrepancy.objects.filter(
                kind=Discrepancy.Kind.BALANCE_MISMATCH,
                resolved_at__isnull=True,
            ).exists()
        )

    @patch("reconciliation.reconcile.sync_once")
    @patch("reconciliation.reconcile.match_grace_seconds", return_value=0)
    def test_missing_onchain_respects_observation_window(self, _grace, _sync):
        old_tx = Transaction.objects.create(
            asset=self.asset,
            kind=Transaction.KIND.deposit,
            status=Transaction.STATUS.completed,
            amount_in=Decimal("5.0000000"),
            amount_out=Decimal("17.0000"),
            stellar_account="GCLIENT",
            stellar_transaction_id="oldhash" * 8,
            completed_at=self.observation_start - timezone.timedelta(days=30),
        )
        new_tx = Transaction.objects.create(
            asset=self.asset,
            kind=Transaction.KIND.deposit,
            status=Transaction.STATUS.completed,
            amount_in=Decimal("5.0000000"),
            amount_out=Decimal("17.0000"),
            stellar_account="GCLIENT",
            stellar_transaction_id="newhash" * 8,
            completed_at=self.observation_start + timezone.timedelta(minutes=30),
        )
        null_hash_tx = Transaction.objects.create(
            asset=self.asset,
            kind=Transaction.KIND.deposit,
            status=Transaction.STATUS.completed,
            amount_in=Decimal("5.0000000"),
            amount_out=Decimal("17.0000"),
            stellar_account="GCLIENT",
            stellar_transaction_id=None,
            completed_at=self.observation_start + timezone.timedelta(minutes=40),
        )

        reconcile_once(self.rpc, self.state, publish_alerts=False)

        self.assertFalse(
            Discrepancy.objects.filter(dedupe_key=f"missing_onchain:{old_tx.id}").exists()
        )
        self.assertFalse(
            Discrepancy.objects.filter(dedupe_key=f"missing_onchain:{null_hash_tx.id}").exists()
        )
        self.assertTrue(
            Discrepancy.objects.filter(
                dedupe_key=f"missing_onchain:{new_tx.id}",
                kind=Discrepancy.Kind.MISSING_ONCHAIN,
            ).exists()
        )

    @patch("reconciliation.reconcile.sync_once")
    def test_balance_mismatch_auto_clears_when_fixed(self, _sync):
        reconcile_once(self.rpc, self.state, publish_alerts=False)
        LedgerEntry.objects.create(
            stellar_tx_hash="h" * 64,
            amount_usdc=Decimal("10.0000000"),
            direction=LedgerEntry.Direction.INBOUND,
            event_id="evt-fix",
            event_type=LedgerEntry.EventType.TRANSFER,
            ledger=100,
            ledger_closed_at=timezone.now(),
            from_address="GCLIENT",
            to_address=self.HOT,
            counterparty="GCLIENT",
        )
        reconcile_once(self.rpc, self.state, publish_alerts=False)
        self.assertFalse(
            Discrepancy.objects.filter(
                kind=Discrepancy.Kind.BALANCE_MISMATCH,
                resolved_at__isnull=True,
            ).exists()
        )

    @patch("reconciliation.reconcile.sync_once")
    def test_skipped_when_sync_lags(self, mock_sync):
        self.state.last_processed_ledger = 90
        self.state.save(update_fields=["last_processed_ledger"])
        self.rpc.usdc_balance.return_value = (Decimal("100.0000000"), 100)

        run = reconcile_once(self.rpc, self.state, publish_alerts=False)
        self.assertEqual(run.status, ReconciliationRun.Status.SKIPPED)
        self.assertEqual(run.ledger_lag, 10)
