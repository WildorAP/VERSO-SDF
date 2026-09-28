# Generated manually for D3 review fixes

import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reconciliation", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="syncstate",
            name="observation_started_at",
            field=models.DateTimeField(
                default=django.utils.timezone.now,
                help_text="Only Polaris txs completed after this time are checked for missing_onchain.",
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="reconciliationrun",
            name="ledger_lag",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
