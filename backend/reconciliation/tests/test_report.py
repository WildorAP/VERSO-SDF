import json
import tempfile
from decimal import Decimal
from pathlib import Path

from django.test import TestCase
from django.utils import timezone

from reconciliation.models import Discrepancy, ReconciliationRun
from reconciliation.report import generate_report
from reconciliation.tests.factories import create_sync_state


class ReportTests(TestCase):
    def setUp(self):
        create_sync_state()

    def test_no_unresolved_true_when_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = generate_report(days=14, output_dir=Path(tmp))
            self.assertTrue(summary["no_unresolved_discrepancies"])
            self.assertTrue((Path(tmp) / "summary.json").exists())
            self.assertTrue((Path(tmp) / "ledger_entries.csv").exists())
            self.assertTrue((Path(tmp) / "discrepancies.csv").exists())
            self.assertTrue((Path(tmp) / "runs.csv").exists())

    def test_no_unresolved_false_with_open(self):
        Discrepancy.objects.create(
            kind=Discrepancy.Kind.UNMATCHED_INBOUND,
            severity=Discrepancy.Severity.WARNING,
            dedupe_key="open:1",
            message="open",
        )
        with tempfile.TemporaryDirectory() as tmp:
            summary = generate_report(days=14, output_dir=Path(tmp))
            self.assertFalse(summary["no_unresolved_discrepancies"])

    def test_test_discrepancies_excluded_from_open_count(self):
        Discrepancy.objects.create(
            kind=Discrepancy.Kind.TEST,
            severity=Discrepancy.Severity.CRITICAL,
            dedupe_key="test:open",
            message="test",
        )
        with tempfile.TemporaryDirectory() as tmp:
            summary = generate_report(days=14, output_dir=Path(tmp))
            self.assertTrue(summary["no_unresolved_discrepancies"])

    def test_max_ledger_lag_uses_run_field(self):
        ReconciliationRun.objects.create(
            ledger=500,
            ledger_lag=12,
            onchain_balance=Decimal("0"),
            internal_balance=Decimal("0"),
            delta=Decimal("0"),
            status=ReconciliationRun.Status.OK,
        )
        ReconciliationRun.objects.create(
            ledger=600,
            ledger_lag=3,
            onchain_balance=Decimal("0"),
            internal_balance=Decimal("0"),
            delta=Decimal("0"),
            status=ReconciliationRun.Status.OK,
        )
        with tempfile.TemporaryDirectory() as tmp:
            summary = generate_report(days=14, output_dir=Path(tmp))
            self.assertEqual(summary["max_ledger_lag"], 12)

    def test_heartbeat_gaps_over_stale_threshold(self):
        now = timezone.now()
        ReconciliationRun.objects.create(
            ledger=100,
            onchain_balance=Decimal("0"),
            internal_balance=Decimal("0"),
            delta=Decimal("0"),
            status=ReconciliationRun.Status.OK,
        )
        first = ReconciliationRun.objects.latest("run_at")
        ReconciliationRun.objects.filter(pk=first.pk).update(
            run_at=now - timezone.timedelta(minutes=20)
        )
        ReconciliationRun.objects.create(
            ledger=101,
            onchain_balance=Decimal("0"),
            internal_balance=Decimal("0"),
            delta=Decimal("0"),
            status=ReconciliationRun.Status.OK,
        )
        with self.settings(RECON_MAX_STALE_SECONDS=300):
            with tempfile.TemporaryDirectory() as tmp:
                summary = generate_report(days=14, output_dir=Path(tmp))
                self.assertEqual(len(summary["heartbeat_gaps_over_stale_threshold"]), 1)
