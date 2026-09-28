"""Alert backends for reconciliation (log + CloudWatch/SNS)."""

from __future__ import annotations

import logging
from typing import Protocol

from django.utils import timezone

from reconciliation.config import (
    alert_backend,
    cloudwatch_namespace,
    recon_environment,
    sns_topic_arn,
)
from reconciliation.models import Discrepancy, ReconciliationRun, SyncState
from verso_integrations.root import stellar_expert_tx_url

logger = logging.getLogger(__name__)


class AlertBackend(Protocol):
    def publish_run(self, run: ReconciliationRun, state: SyncState) -> None: ...

    def notify_discrepancy(self, discrepancy: Discrepancy) -> None: ...

    def publish_test_alarm(self) -> None: ...


class LogBackend:
    def publish_run(self, run: ReconciliationRun, state: SyncState) -> None:
        level = logging.ERROR if run.status != ReconciliationRun.Status.OK else logging.INFO
        logger.log(
            level,
            "reconciliation_run status=%s delta=%s open=%s lag=%s",
            run.status,
            run.delta,
            run.open_discrepancies,
            run.ledger - state.last_processed_ledger,
        )

    def notify_discrepancy(self, discrepancy: Discrepancy) -> None:
        logger.warning(
            "discrepancy kind=%s severity=%s message=%s",
            discrepancy.kind,
            discrepancy.severity,
            discrepancy.message,
        )
        discrepancy.alerted_at = timezone.now()
        discrepancy.save(update_fields=["alerted_at"])

    def publish_test_alarm(self) -> None:
        logger.warning(
            "TEST ALARM: CriticalOpenDiscrepancies=1 published for CloudWatch evidence"
        )


class CloudWatchBackend:
    def __init__(self):
        import boto3

        self.cloudwatch = boto3.client("cloudwatch")
        self.sns = boto3.client("sns")
        self.namespace = cloudwatch_namespace()
        self.environment = recon_environment()
        self.topic_arn = sns_topic_arn()

    def _dimensions(self):
        return [{"Name": "Environment", "Value": self.environment}]

    def publish_run(self, run: ReconciliationRun, state: SyncState) -> None:
        critical_open = Discrepancy.objects.filter(
            resolved_at__isnull=True,
            severity=Discrepancy.Severity.CRITICAL,
        ).exclude(kind=Discrepancy.Kind.TEST).count()
        lag = run.ledger_lag if run.ledger_lag is not None else max(0, run.ledger - state.last_processed_ledger)
        try:
            self.cloudwatch.put_metric_data(
                Namespace=self.namespace,
                MetricData=[
                    {
                        "MetricName": "OpenDiscrepancies",
                        "Dimensions": self._dimensions(),
                        "Value": run.open_discrepancies,
                        "Unit": "Count",
                    },
                    {
                        "MetricName": "CriticalOpenDiscrepancies",
                        "Dimensions": self._dimensions(),
                        "Value": critical_open,
                        "Unit": "Count",
                    },
                    {
                        "MetricName": "BalanceDeltaAbs",
                        "Dimensions": self._dimensions(),
                        "Value": float(abs(run.delta)),
                        "Unit": "None",
                    },
                    {
                        "MetricName": "LedgerLag",
                        "Dimensions": self._dimensions(),
                        "Value": lag,
                        "Unit": "Count",
                    },
                    {
                        "MetricName": "Heartbeat",
                        "Dimensions": self._dimensions(),
                        "Value": 1,
                        "Unit": "Count",
                    },
                ],
            )
        except Exception as exc:
            logger.exception("CloudWatch publish_run failed: %s", exc)

    def notify_discrepancy(self, discrepancy: Discrepancy) -> None:
        tx_url = ""
        if discrepancy.ledger_entry_id:
            tx_url = stellar_expert_tx_url(discrepancy.ledger_entry.stellar_tx_hash)
        subject = f"[VERSO anchor][{self.environment}] {discrepancy.kind}"
        body = (
            f"{discrepancy.message}\n"
            f"severity={discrepancy.severity}\n"
            f"dedupe_key={discrepancy.dedupe_key}\n"
            f"{tx_url}\n"
        )
        if self.topic_arn:
            try:
                self.sns.publish(TopicArn=self.topic_arn, Subject=subject, Message=body)
            except Exception as exc:
                logger.exception("SNS notify failed: %s", exc)
        discrepancy.alerted_at = timezone.now()
        discrepancy.save(update_fields=["alerted_at"])

    def publish_test_alarm(self) -> None:
        """Publish CriticalOpenDiscrepancies=1 so the CloudWatch alarm enters ALARM."""
        try:
            self.cloudwatch.put_metric_data(
                Namespace=self.namespace,
                MetricData=[
                    {
                        "MetricName": "CriticalOpenDiscrepancies",
                        "Dimensions": self._dimensions(),
                        "Value": 1,
                        "Unit": "Count",
                    },
                    {
                        "MetricName": "Heartbeat",
                        "Dimensions": self._dimensions(),
                        "Value": 1,
                        "Unit": "Count",
                    },
                ],
            )
        except Exception as exc:
            logger.exception("CloudWatch publish_test_alarm failed: %s", exc)
        if self.topic_arn:
            try:
                self.sns.publish(
                    TopicArn=self.topic_arn,
                    Subject=f"[VERSO anchor][{self.environment}] reconciliation test alarm",
                    Message="Synthetic D3 test — CriticalOpenDiscrepancies=1",
                )
            except Exception as exc:
                logger.exception("SNS test publish failed: %s", exc)


def get_alert_backend() -> AlertBackend:
    name = alert_backend()
    if name == "cloudwatch":
        return CloudWatchBackend()
    return LogBackend()
