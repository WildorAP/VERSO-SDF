# Generated manually — SEP-24 bank transfer receipt upload.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("verso_integrations", "0005_alter_fiatdeposit_tipo_cambio"),
    ]

    operations = [
        migrations.AddField(
            model_name="sep24depositmeta",
            name="transfer_declared_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text="When the client confirmed they sent the PEN transfer.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="sep24depositmeta",
            name="transfer_receipt",
            field=models.FileField(
                blank=True,
                help_text="Bank transfer voucher uploaded by the client.",
                null=True,
                upload_to="sep24/receipts/%Y/%m/",
            ),
        ),
    ]
