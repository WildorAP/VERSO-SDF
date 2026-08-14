# Generated manually for tipo_cambio + computed amount_usdc

from decimal import Decimal

from django.db import migrations, models


def recalculate_amount_usdc(apps, schema_editor):
    FiatDeposit = apps.get_model("verso_integrations", "FiatDeposit")
    quantize_usdc = Decimal("0.0000001")
    quantize_tc = Decimal("0.0001")
    for deposit in FiatDeposit.objects.all():
        if deposit.amount_usdc and deposit.amount_usdc > 0:
            deposit.tipo_cambio = (deposit.amount_pen / deposit.amount_usdc).quantize(
                quantize_tc
            )
        deposit.amount_usdc = (deposit.amount_pen / deposit.tipo_cambio).quantize(
            quantize_usdc
        )
        deposit.save(update_fields=["tipo_cambio", "amount_usdc"])


class Migration(migrations.Migration):

    dependencies = [
        ("verso_integrations", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="fiatdeposit",
            name="tipo_cambio",
            field=models.DecimalField(
                decimal_places=4,
                default=Decimal("3.7500"),
                help_text="Tipo de cambio: PEN por 1 USDC (ej. 3.7500).",
                max_digits=12,
            ),
            preserve_default=False,
        ),
        migrations.RunPython(recalculate_amount_usdc, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="fiatdeposit",
            name="amount_usdc",
            field=models.DecimalField(
                decimal_places=7,
                editable=False,
                help_text="USDC calculado: amount_pen / tipo_cambio (7 decimales Stellar).",
                max_digits=18,
            ),
        ),
    ]
