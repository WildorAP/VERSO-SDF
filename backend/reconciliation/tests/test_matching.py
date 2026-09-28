from decimal import Decimal
from unittest.mock import patch
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from polaris.models import Asset, Transaction

from reconciliation.matching import match_pending, refresh_pending_fiat
from reconciliation.models import Discrepancy, LedgerEntry
from reconciliation.tests.factories import create_sync_state, memo_hash_for_uuid, transfer_event
from verso_integrations.models import FiatDeposit, Sep24DepositMeta, Sep24WithdrawMeta
from verso_integrations.polaris_setup import seed_polaris_t2


class MatchingTests(TestCase):
    HOT = "GBTV5QYBPGHGT2SVUHCFRRKFFWUWHOEPKH7QAXGTJHFGFYZRIE24UOPB"
    CLIENT = "GCXGLWL7GEPUDCCZABQVLHTZLDWWXPTURGXODJ6JF6BVJSO4KWU45IFG"

    @classmethod
    def setUpTestData(cls):
        seed_polaris_t2()
        cls.asset = Asset.objects.get(code="USDC")

    def _entry(self, **kwargs) -> LedgerEntry:
        defaults = {
            "stellar_tx_hash": "hash" * 8,
            "amount_usdc": Decimal("10.0000000"),
            "direction": LedgerEntry.Direction.INBOUND,
            "event_id": "evt-match-1",
            "event_type": LedgerEntry.EventType.TRANSFER,
            "ledger": 100,
            "ledger_closed_at": timezone.now(),
            "from_address": self.CLIENT,
            "to_address": self.HOT,
            "counterparty": self.CLIENT,
            "match_status": LedgerEntry.MatchStatus.PENDING,
        }
        defaults.update(kwargs)
        return LedgerEntry.objects.create(**defaults)

    def test_withdraw_match_by_memo(self):
        tx_id = uuid.uuid4()
        tx = Transaction.objects.create(
            asset=self.asset,
            kind=Transaction.KIND.withdrawal,
            status=Transaction.STATUS.pending_user_transfer_start,
            amount_in=Decimal("10.0000000"),
            amount_out=Decimal("34.0000"),
            stellar_account=self.CLIENT,
            memo=memo_hash_for_uuid(tx_id),
            memo_type=Transaction.MEMO_TYPES.hash,
            id=tx_id,
        )
        entry = self._entry(
            event_id="evt-memo",
            memo_raw=tx.memo,
            memo_type="hash",
            stellar_tx_hash="withdrawhash" * 4,
        )
        matched = match_pending()
        entry.refresh_from_db()
        self.assertEqual(matched, 1)
        self.assertEqual(entry.match_status, LedgerEntry.MatchStatus.MATCHED)
        self.assertEqual(entry.polaris_transaction_id, tx.id)
        self.assertEqual(entry.anchor_callback_id, str(tx.id))

    def test_withdraw_match_by_stellar_hash(self):
        tx = Transaction.objects.create(
            asset=self.asset,
            kind=Transaction.KIND.withdrawal,
            status=Transaction.STATUS.completed,
            amount_in=Decimal("10.0000000"),
            amount_out=Decimal("34.0000"),
            stellar_account=self.CLIENT,
            stellar_transaction_id="withdrawbyhash" * 4,
        )
        entry = self._entry(
            event_id="evt-withdraw-hash",
            direction=LedgerEntry.Direction.INBOUND,
            memo_raw="",
            memo_type="",
            stellar_tx_hash=tx.stellar_transaction_id,
        )
        match_pending()
        entry.refresh_from_db()
        self.assertEqual(entry.match_status, LedgerEntry.MatchStatus.MATCHED)
        self.assertEqual(entry.match_kind, "sep24_withdrawal")

    def test_deposit_match_by_stellar_hash(self):
        tx_hash = "deposithash" * 4
        tx = Transaction.objects.create(
            asset=self.asset,
            kind=Transaction.KIND.deposit,
            status=Transaction.STATUS.completed,
            amount_in=Decimal("10.0000000"),
            amount_out=Decimal("34.0000"),
            stellar_account=self.CLIENT,
            stellar_transaction_id=tx_hash,
        )
        entry = self._entry(
            event_id="evt-deposit-hash",
            direction=LedgerEntry.Direction.OUTBOUND,
            from_address=self.HOT,
            to_address=self.CLIENT,
            counterparty=self.CLIENT,
            stellar_tx_hash=tx_hash,
        )
        match_pending()
        entry.refresh_from_db()
        self.assertEqual(entry.match_status, LedgerEntry.MatchStatus.MATCHED)
        self.assertEqual(entry.match_kind, "sep24_deposit")

    def test_fiat_deposit_match_by_hash(self):
        tx_hash = "fiatdeposithash" * 4
        deposit = FiatDeposit.objects.create(
            stellar_tx_hash=tx_hash,
            amount_pen=Decimal("34.00"),
            tipo_cambio=Decimal("3.4000"),
            stellar_account=self.CLIENT,
        )
        entry = self._entry(
            event_id="evt-fiat-deposit",
            direction=LedgerEntry.Direction.OUTBOUND,
            from_address=self.HOT,
            to_address=self.CLIENT,
            counterparty=self.CLIENT,
            stellar_tx_hash=tx_hash,
        )
        match_pending()
        entry.refresh_from_db()
        self.assertEqual(entry.match_status, LedgerEntry.MatchStatus.MATCHED)
        self.assertEqual(entry.match_kind, "fiat_deposit")
        self.assertEqual(entry.fiat_deposit_id, deposit.pk)

    @patch("reconciliation.matching.fetch_client_by_stellar_key", return_value={"kyc_status": "approved"})
    def test_unmatched_after_grace(self, _mock_kyc):
        with self.settings(RECON_MATCH_GRACE_SECONDS=0):
            entry = self._entry(
                event_id="evt-unmatched",
                ledger_closed_at=timezone.now() - timezone.timedelta(hours=1),
            )
            match_pending()
            entry.refresh_from_db()
            self.assertEqual(entry.match_status, LedgerEntry.MatchStatus.UNMATCHED)
            self.assertTrue(
                Discrepancy.objects.filter(
                    kind=Discrepancy.Kind.UNMATCHED_INBOUND,
                    ledger_entry=entry,
                ).exists()
            )

    @patch("reconciliation.matching.fetch_client_by_stellar_key", return_value={"kyc_status": "approved"})
    def test_late_match_auto_clears_unmatched_discrepancy(self, _mock_kyc):
        with self.settings(RECON_MATCH_GRACE_SECONDS=0):
            tx_id = uuid.uuid4()
            tx = Transaction.objects.create(
                asset=self.asset,
                kind=Transaction.KIND.withdrawal,
                status=Transaction.STATUS.pending_user_transfer_start,
                amount_in=Decimal("10.0000000"),
                amount_out=Decimal("34.0000"),
                stellar_account=self.CLIENT,
                memo=memo_hash_for_uuid(tx_id),
                memo_type=Transaction.MEMO_TYPES.hash,
                id=tx_id,
            )
            entry = self._entry(
                event_id="evt-late-match",
                ledger_closed_at=timezone.now() - timezone.timedelta(hours=1),
            )
            match_pending()
            self.assertTrue(
                Discrepancy.objects.filter(
                    kind=Discrepancy.Kind.UNMATCHED_INBOUND,
                    ledger_entry=entry,
                    resolved_at__isnull=True,
                ).exists()
            )

            entry.memo_raw = tx.memo
            entry.memo_type = "hash"
            entry.save(update_fields=["memo_raw", "memo_type", "updated_at"])
            match_pending()
            entry.refresh_from_db()
            self.assertEqual(entry.match_status, LedgerEntry.MatchStatus.MATCHED)
            self.assertFalse(
                Discrepancy.objects.filter(
                    kind=Discrepancy.Kind.UNMATCHED_INBOUND,
                    ledger_entry=entry,
                    resolved_at__isnull=True,
                ).exists()
            )

    def test_amount_mismatch_on_withdraw(self):
        tx_id = uuid.uuid4()
        tx = Transaction.objects.create(
            asset=self.asset,
            kind=Transaction.KIND.withdrawal,
            status=Transaction.STATUS.pending_user_transfer_start,
            amount_in=Decimal("9.0000000"),
            amount_out=Decimal("34.0000"),
            stellar_account=self.CLIENT,
            memo=memo_hash_for_uuid(tx_id),
            memo_type=Transaction.MEMO_TYPES.hash,
            id=tx_id,
        )
        entry = self._entry(
            event_id="evt-amount-mismatch",
            amount_usdc=Decimal("10.0000000"),
            memo_raw=tx.memo,
            memo_type="hash",
        )
        match_pending()
        self.assertTrue(
            Discrepancy.objects.filter(
                kind=Discrepancy.Kind.AMOUNT_MISMATCH,
                ledger_entry=entry,
                resolved_at__isnull=True,
            ).exists()
        )

    @patch("reconciliation.matching.fetch_client_by_stellar_key", return_value={"kyc_status": "approved"})
    def test_enrich_pen_vs_usd(self, _mock_kyc):
        tx_pen = Transaction.objects.create(
            asset=self.asset,
            kind=Transaction.KIND.deposit,
            status=Transaction.STATUS.completed,
            amount_in=Decimal("10.0000000"),
            amount_out=Decimal("34.0000"),
            stellar_account=self.CLIENT,
            stellar_transaction_id="penhash" * 8,
        )
        Sep24DepositMeta.objects.create(
            transaction=tx_pen,
            fiat_currency=Sep24DepositMeta.FiatCurrency.PEN,
            amount_pen=Decimal("34.00"),
            tipo_cambio=Decimal("3.4000"),
            amount_usdc=Decimal("10.0000000"),
            sell_asset="iso4217:PEN",
            buy_asset="stellar:USDC:test",
            fiat_confirmed_at=timezone.now(),
        )
        entry_pen = self._entry(
            event_id="evt-pen",
            direction=LedgerEntry.Direction.OUTBOUND,
            from_address=self.HOT,
            to_address=self.CLIENT,
            counterparty=self.CLIENT,
            stellar_tx_hash=tx_pen.stellar_transaction_id,
        )

        tx_usd = Transaction.objects.create(
            asset=self.asset,
            kind=Transaction.KIND.withdrawal,
            status=Transaction.STATUS.completed,
            amount_in=Decimal("10.0000000"),
            amount_out=Decimal("10.0000"),
            stellar_account=self.CLIENT,
            stellar_transaction_id="usdhash" * 8,
        )
        Sep24WithdrawMeta.objects.create(
            transaction=tx_usd,
            fiat_currency=Sep24WithdrawMeta.FiatCurrency.USD,
            amount_pen=Decimal("10.00"),
            tipo_cambio=Decimal("1.0000"),
            amount_usdc=Decimal("10.0000000"),
            sell_asset="stellar:USDC:test",
            buy_asset="iso4217:USD",
            fiat_sent_at=timezone.now(),
        )
        entry_usd = self._entry(
            event_id="evt-usd",
            direction=LedgerEntry.Direction.INBOUND,
            stellar_tx_hash=tx_usd.stellar_transaction_id,
        )

        match_pending()
        entry_pen.refresh_from_db()
        entry_usd.refresh_from_db()
        self.assertEqual(entry_pen.amount_pen, Decimal("34.00"))
        self.assertIsNone(entry_pen.amount_usd)
        self.assertEqual(entry_usd.amount_usd, Decimal("10.00"))
        self.assertIsNone(entry_usd.amount_pen)

    @patch("reconciliation.matching.fetch_client_by_stellar_key", return_value={"kyc_status": "approved"})
    def test_refresh_pending_fiat_picks_up_fiat_sent_later(self, _mock_kyc):
        tx = Transaction.objects.create(
            asset=self.asset,
            kind=Transaction.KIND.withdrawal,
            status=Transaction.STATUS.pending_anchor,
            amount_in=Decimal("10.0000000"),
            stellar_account=self.CLIENT,
            stellar_transaction_id="latefiat" * 8,
        )
        meta = Sep24WithdrawMeta.objects.create(
            transaction=tx,
            fiat_currency=Sep24WithdrawMeta.FiatCurrency.PEN,
            amount_pen=Decimal("34.00"),
            tipo_cambio=Decimal("3.4000"),
            amount_usdc=Decimal("10.0000000"),
            sell_asset="stellar:USDC:test",
            buy_asset="iso4217:PEN",
        )
        entry = self._entry(event_id="evt-late-fiat", stellar_tx_hash=tx.stellar_transaction_id)

        match_pending()
        entry.refresh_from_db()
        self.assertEqual(entry.fiat_status, LedgerEntry.FiatStatus.PENDING)

        meta.fiat_sent_at = timezone.now()
        meta.save(update_fields=["fiat_sent_at"])
        self.assertEqual(refresh_pending_fiat(), 1)
        entry.refresh_from_db()
        self.assertEqual(entry.fiat_status, LedgerEntry.FiatStatus.SENT)
        self.assertEqual(refresh_pending_fiat(), 0)

    @patch(
        "reconciliation.matching.fetch_client_by_stellar_key",
        side_effect=ValueError("kyc down"),
    )
    def test_kyc_value_error_sets_unknown(self, _mock_kyc):
        entry = self._entry(event_id="evt-kyc-fail")
        match_pending()
        entry.refresh_from_db()
        self.assertEqual(entry.kyc_status, "unknown")
