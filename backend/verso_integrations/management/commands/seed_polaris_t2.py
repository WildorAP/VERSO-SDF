from django.core.management.base import BaseCommand

from verso_integrations.polaris_setup import seed_polaris_t2


class Command(BaseCommand):
    help = "Seed Polaris Asset, OffChainAsset, ExchangePair, and DeliveryMethod for T2."

    def handle(self, *args, **options):
        summary = seed_polaris_t2()
        for key, value in summary.items():
            self.stdout.write(f"  {key}: {value}")
        self.stdout.write(self.style.SUCCESS("Polaris T2 seed data ready."))
