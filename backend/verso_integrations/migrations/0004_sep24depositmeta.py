# Generated manually for Etapa 3 SEP-24 MVP.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("polaris", "0014_auto_20220211_0624"),
        ("verso_integrations", "0003_alter_fiatdeposit_status"),
    ]

    operations = [
        migrations.CreateModel(
            name="Sep24DepositMeta",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("amount_pen", models.DecimalField(decimal_places=2, max_digits=18)),
                ("tipo_cambio", models.DecimalField(decimal_places=4, max_digits=12)),
                ("amount_usdc", models.DecimalField(decimal_places=7, max_digits=18)),
                (
                    "sell_asset",
                    models.TextField(
                        help_text="SEP-38 asset id for fiat sold by user (iso4217:PEN)."
                    ),
                ),
                (
                    "buy_asset",
                    models.TextField(
                        help_text="SEP-38 asset id received on-chain (stellar:USDC:...)."
                    ),
                ),
                ("bank_instructions", models.JSONField(blank=True, default=dict)),
                (
                    "fiat_confirmed_at",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "transaction",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="verso_deposit_meta",
                        to="polaris.transaction",
                    ),
                ),
            ],
            options={
                "verbose_name": "SEP-24 deposit (PEN on-ramp)",
                "verbose_name_plural": "SEP-24 deposits (PEN on-ramp)",
            },
        ),
    ]
