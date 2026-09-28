"""Export D3 reconciliation report files."""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from reconciliation.report import generate_report


class Command(BaseCommand):
    help = "Generate D3 report (summary.json + CSV files)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=14)
        parser.add_argument("--out", required=True, help="Output directory path.")

    def handle(self, *args, **options):
        output = Path(options["out"])
        try:
            summary = generate_report(days=options["days"], output_dir=output)
        except RuntimeError as exc:
            raise CommandError(str(exc)) from exc
        flag = summary.get("no_unresolved_discrepancies")
        self.stdout.write(
            self.style.SUCCESS(
                f"Report written to {output} — no_unresolved_discrepancies={flag}"
            )
        )
