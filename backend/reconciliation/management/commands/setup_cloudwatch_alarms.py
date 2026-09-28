"""Create or update CloudWatch alarms for reconciliation metrics."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from reconciliation.config import cloudwatch_namespace, recon_environment, sns_topic_arn


class Command(BaseCommand):
    help = "Create CloudWatch alarms + optional SNS topic (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--create-topic", action="store_true")
        parser.add_argument("--email", default="", help="Email for SNS subscription.")

    def handle(self, *args, **options):
        try:
            import boto3
        except ImportError as exc:
            raise CommandError("boto3 is required for setup_cloudwatch_alarms.") from exc

        namespace = cloudwatch_namespace()
        env = recon_environment()
        dimensions = [{"Name": "Environment", "Value": env}]
        sns = boto3.client("sns")
        cloudwatch = boto3.client("cloudwatch")

        topic_arn = sns_topic_arn()
        if options["create_topic"]:
            topic_arn = sns.create_topic(Name=f"verso-anchor-recon-{env}")["TopicArn"]
            email = (options["email"] or "").strip()
            if email:
                sns.subscribe(TopicArn=topic_arn, Protocol="email", Endpoint=email)
            self.stdout.write(f"SNS topic: {topic_arn}")

        if not topic_arn:
            raise CommandError("Set RECON_SNS_TOPIC_ARN or pass --create-topic.")

        alarm_actions = [topic_arn]
        alarm_specs = [
            (
                "verso-recon-open-discrepancies",
                "CriticalOpenDiscrepancies",
                0,
                "GreaterThanThreshold",
                60,
                1,
                "Maximum",
                None,
            ),
            (
                "verso-recon-balance-delta",
                "BalanceDeltaAbs",
                0,
                "GreaterThanThreshold",
                60,
                1,
                "Maximum",
                None,
            ),
            (
                "verso-recon-heartbeat",
                "Heartbeat",
                1,
                "LessThanThreshold",
                300,
                1,
                "SampleCount",
                "breaching",
            ),
            (
                "verso-recon-ledger-lag",
                "LedgerLag",
                60,
                "GreaterThanThreshold",
                60,
                3,
                "Maximum",
                None,
            ),
        ]
        for name, metric, threshold, comparison, period, eval_periods, statistic, missing in alarm_specs:
            kwargs = {
                "AlarmName": name,
                "Namespace": namespace,
                "MetricName": metric,
                "Dimensions": dimensions,
                "Period": period,
                "EvaluationPeriods": eval_periods,
                "Threshold": threshold,
                "ComparisonOperator": comparison,
                "Statistic": statistic,
                "AlarmActions": alarm_actions,
                "OKActions": alarm_actions,
            }
            if missing:
                kwargs["TreatMissingData"] = missing
            if name == "verso-recon-ledger-lag":
                kwargs["DatapointsToAlarm"] = 2
            cloudwatch.put_metric_alarm(**kwargs)
            self.stdout.write(self.style.SUCCESS(f"Alarm {name} OK"))
