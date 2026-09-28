from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from reconciliation.alerts import LogBackend
from reconciliation.models import Discrepancy, ReconciliationRun, SyncState
from reconciliation.tests.factories import create_sync_state


class AlertTests(TestCase):
    def test_log_backend_marks_alerted(self):
        discrepancy = Discrepancy.objects.create(
            kind=Discrepancy.Kind.TEST,
            severity=Discrepancy.Severity.CRITICAL,
            dedupe_key="test:1",
            message="test",
        )
        state = create_sync_state(
            hot_wallet="G" * 56,
            asset_contract_id="C" * 56,
            start_ledger=1,
            last_processed_ledger=1,
            opening_balance=Decimal("0"),
        )
        run = ReconciliationRun.objects.create(
            ledger=1,
            onchain_balance=Decimal("0"),
            internal_balance=Decimal("0"),
            delta=Decimal("0"),
            status=ReconciliationRun.Status.OK,
        )
        backend = LogBackend()
        backend.publish_run(run, state)
        backend.notify_discrepancy(discrepancy)
        discrepancy.refresh_from_db()
        self.assertIsNotNone(discrepancy.alerted_at)

    @patch("boto3.client")
    def test_cloudwatch_errors_do_not_propagate(self, mock_boto_client):
        from reconciliation.alerts import CloudWatchBackend

        mock_boto_client.return_value.put_metric_data.side_effect = RuntimeError("aws down")
        backend = CloudWatchBackend()
        state = create_sync_state(
            hot_wallet="G" * 56,
            asset_contract_id="C" * 56,
            start_ledger=1,
            last_processed_ledger=1,
            opening_balance=Decimal("0"),
        )
        run = ReconciliationRun.objects.create(
            ledger=1,
            onchain_balance=Decimal("0"),
            internal_balance=Decimal("0"),
            delta=Decimal("0"),
            status=ReconciliationRun.Status.OK,
        )
        backend.publish_run(run, state)

    @patch("boto3.client")
    def test_publish_test_alarm_sends_critical_metric(self, mock_boto_client):
        from reconciliation.alerts import CloudWatchBackend

        cloudwatch = mock_boto_client.return_value
        backend = CloudWatchBackend()
        backend.publish_test_alarm()
        metric_names = [
            item["MetricName"]
            for call in cloudwatch.put_metric_data.call_args_list
            for item in call.kwargs["MetricData"]
        ]
        self.assertIn("CriticalOpenDiscrepancies", metric_names)
