"""Fire a synthetic discrepancy alert (D3 evidence)."""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from reconciliation.alerts import get_alert_backend
from reconciliation.models import Discrepancy


class Command(BaseCommand):
    help = "Create a test discrepancy and publish alert metrics."

    def add_arguments(self, parser):
        parser.add_argument(
            "--resolve",
            action="store_true",
            help="Close the test discrepancy as false_positive after alerting.",
        )

    def handle(self, *args, **options):
        discrepancy = Discrepancy.objects.filter(
            dedupe_key="test:synthetic",
            resolved_at__isnull=True,
        ).first()
        if discrepancy is None:
            discrepancy = Discrepancy.objects.create(
                kind=Discrepancy.Kind.TEST,
                severity=Discrepancy.Severity.CRITICAL,
                dedupe_key="test:synthetic",
                message="Synthetic D3 alert test (SNS only; excluded from open count)",
            )
        backend = get_alert_backend()
        backend.notify_discrepancy(discrepancy)
        backend.publish_test_alarm()
        self.stdout.write(
            "Test alert published (SNS + CriticalOpenDiscrepancies=1). "
            "Capture email and CloudWatch ALARM on verso-recon-open-discrepancies."
        )
        if options["resolve"]:
            discrepancy.resolved_at = timezone.now()
            discrepancy.resolution = Discrepancy.Resolution.FALSE_POSITIVE
            discrepancy.resolution_note = "Synthetic test"
            discrepancy.save(
                update_fields=["resolved_at", "resolution", "resolution_note"]
            )
