"""
Raise PEN/USD OffChainAsset precision from 2 to 4 decimals.

SEP-38 rounds price to the sell asset's significant_decimals; with 2 decimals a VERSO Core
rate of 3.3750 was quoted as 3.38 and 1.0010 as 1.00. Existing rows are updated here so the
fix applies on deploy (railpack.json runs migrate) without re-running seed_polaris_t2.
"""

from django.db import migrations

FIAT_IDENTIFIERS = ("PEN", "USD")


def forwards(apps, schema_editor):
    OffChainAsset = apps.get_model("polaris", "OffChainAsset")
    OffChainAsset.objects.filter(scheme="iso4217", identifier__in=FIAT_IDENTIFIERS).update(
        significant_decimals=4
    )


def backwards(apps, schema_editor):
    OffChainAsset = apps.get_model("polaris", "OffChainAsset")
    OffChainAsset.objects.filter(scheme="iso4217", identifier__in=FIAT_IDENTIFIERS).update(
        significant_decimals=2
    )


class Migration(migrations.Migration):
    dependencies = [
        ("polaris", "0014_auto_20220211_0624"),
        ("verso_integrations", "0008_sep24withdrawmeta"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
