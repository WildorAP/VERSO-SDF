from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("verso_integrations", "0006_sep24depositmeta_transfer_receipt"),
    ]

    operations = [
        migrations.AddField(
            model_name="sep24depositmeta",
            name="fiat_currency",
            field=models.CharField(
                choices=[("PEN", "PEN"), ("USD", "USD")],
                db_index=True,
                default="PEN",
                help_text="Fiat currency sold by the client in this SEP-24 on-ramp.",
                max_length=3,
            ),
        ),
        migrations.AlterField(
            model_name="sep24depositmeta",
            name="amount_pen",
            field=models.DecimalField(
                decimal_places=2,
                help_text="Fiat amount the client should transfer (PEN or USD).",
                max_digits=18,
            ),
        ),
        migrations.AlterField(
            model_name="sep24depositmeta",
            name="sell_asset",
            field=models.TextField(
                help_text="SEP-38 asset id for fiat sold by user (iso4217:PEN or iso4217:USD).",
            ),
        ),
        migrations.AlterField(
            model_name="sep24depositmeta",
            name="transfer_declared_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text="When the client confirmed they sent the fiat transfer.",
                null=True,
            ),
        ),
        migrations.AlterModelOptions(
            name="sep24depositmeta",
            options={
                "verbose_name": "SEP-24 deposit (fiat on-ramp)",
                "verbose_name_plural": "SEP-24 deposits (fiat on-ramp)",
            },
        ),
    ]
