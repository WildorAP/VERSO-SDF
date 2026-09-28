from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from reconciliation.models import Discrepancy, LedgerEntry


class DiscrepancyResolveTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="recon-admin",
            password="test",
        )
        self.entry = LedgerEntry.objects.create(
            stellar_tx_hash="h" * 64,
            amount_usdc=Decimal("10.0000000"),
            direction=LedgerEntry.Direction.INBOUND,
            event_id="evt-admin",
            event_type=LedgerEntry.EventType.TRANSFER,
            ledger=100,
            ledger_closed_at=timezone.now(),
            from_address="GCLIENT",
            to_address="GHOT",
            counterparty="GCLIENT",
        )
        self.discrepancy = Discrepancy.objects.create(
            kind=Discrepancy.Kind.UNMATCHED_INBOUND,
            severity=Discrepancy.Severity.WARNING,
            dedupe_key="admin:1",
            message="needs review",
            ledger_entry=self.entry,
        )

    def test_resolve_requires_note(self):
        with self.assertRaises(ValueError):
            self.discrepancy.resolve(
                self.user,
                Discrepancy.Resolution.FUNDING,
                "",
            )

    def test_resolve_funding_classifies_entry(self):
        self.discrepancy.resolve(
            self.user,
            Discrepancy.Resolution.FUNDING,
            "Initial hot wallet funding",
        )
        self.discrepancy.refresh_from_db()
        self.entry.refresh_from_db()
        self.assertIsNotNone(self.discrepancy.resolved_at)
        self.assertEqual(self.discrepancy.resolution, Discrepancy.Resolution.FUNDING)
        self.assertEqual(self.entry.match_status, LedgerEntry.MatchStatus.CLASSIFIED)
        self.assertEqual(self.entry.match_kind, Discrepancy.Resolution.FUNDING)
