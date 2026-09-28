from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase

from reconciliation.models import LedgerEntry, SyncState
from reconciliation.sync import sync_once
from reconciliation.tests.factories import create_sync_state, transfer_event


class SyncTests(TestCase):
    HOT = "GBTV5QYBPGHGT2SVUHCFRRKFFWUWHOEPKH7QAXGTJHFGFYZRIE24UOPB"
    CLIENT = "GCXGLWL7GEPUDCCZABQVLHTZLDWWXPTURGXODJ6JF6BVJSO4KWU45IFG"

    def setUp(self):
        self.state = create_sync_state(
            hot_wallet=self.HOT,
            last_processed_ledger=99,
        )
        self.event = transfer_event(
            event_id="evt-sync-1",
            from_account=self.CLIENT,
            to_account=self.HOT,
        )

    def test_idempotent_insert(self):
        rpc = MagicMock()
        response = MagicMock()
        response.events = [self.event]
        response.cursor = "cursor-1"
        response.latest_ledger = 101
        rpc.get_events.return_value = response

        first = sync_once(rpc, self.state)
        self.state.refresh_from_db()
        second = sync_once(rpc, self.state)
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertEqual(LedgerEntry.objects.count(), 1)
        self.assertEqual(self.state.cursor, "cursor-1")

    @patch("reconciliation.sync.match_pending")
    def test_rpc_error_records_last_error(self, mock_match):
        rpc = MagicMock()
        from reconciliation.rpc_client import RpcClientError

        rpc.get_events.side_effect = RpcClientError("network down")
        with self.assertRaises(RpcClientError):
            sync_once(rpc, self.state)
        self.state.refresh_from_db()
        self.assertIn("network down", self.state.last_error)
        mock_match.assert_not_called()

    def test_dry_run_does_not_persist_cursor_or_entries(self):
        rpc = MagicMock()
        response = MagicMock()
        response.events = [self.event]
        response.cursor = "would-advance-cursor"
        response.latest_ledger = 500
        rpc.get_events.return_value = response

        before_cursor = self.state.cursor
        before_ledger = self.state.last_processed_ledger
        seen = sync_once(rpc, self.state, dry_run=True)

        self.state.refresh_from_db()
        self.assertEqual(seen, 1)
        self.assertEqual(LedgerEntry.objects.count(), 0)
        self.assertEqual(self.state.cursor, before_cursor)
        self.assertEqual(self.state.last_processed_ledger, before_ledger)
        rpc.get_events.assert_called_once()

    @patch("reconciliation.sync.EVENT_PAGE_LIMIT", 1)
    def test_pagination_persists_final_cursor(self):
        rpc = MagicMock()
        page_one = MagicMock()
        page_one.events = [
            transfer_event(
                event_id="evt-page-1",
                from_account=self.CLIENT,
                to_account=self.HOT,
                ledger=100,
            )
        ]
        page_one.cursor = "cursor-page-1"
        page_one.latest_ledger = 100

        page_two = MagicMock()
        page_two.events = [
            transfer_event(
                event_id="evt-page-2",
                from_account=self.CLIENT,
                to_account=self.HOT,
                ledger=101,
            )
        ]
        page_two.cursor = "cursor-page-2"
        page_two.latest_ledger = 101

        page_three = MagicMock()
        page_three.events = []
        page_three.cursor = "cursor-final"
        page_three.latest_ledger = 101

        rpc.get_events.side_effect = [page_one, page_two, page_three]

        inserted = sync_once(rpc, self.state)
        self.state.refresh_from_db()

        self.assertEqual(inserted, 2)
        self.assertEqual(LedgerEntry.objects.count(), 2)
        self.assertEqual(self.state.cursor, "cursor-final")
        self.assertEqual(self.state.last_processed_ledger, 101)
        self.assertEqual(rpc.get_events.call_count, 3)

    def test_start_ledger_only_when_cursor_empty(self):
        rpc = MagicMock()
        response = MagicMock()
        response.events = []
        response.cursor = "new-cursor"
        response.latest_ledger = 100
        rpc.get_events.return_value = response

        sync_once(rpc, self.state)
        _, kwargs = rpc.get_events.call_args
        self.assertEqual(kwargs["start_ledger"], self.state.start_ledger)
        self.assertIsNone(kwargs["cursor"])

        self.state.refresh_from_db()
        rpc.get_events.reset_mock()
        sync_once(rpc, self.state)
        _, kwargs = rpc.get_events.call_args
        self.assertIsNone(kwargs["start_ledger"])
        self.assertEqual(kwargs["cursor"], "new-cursor")
