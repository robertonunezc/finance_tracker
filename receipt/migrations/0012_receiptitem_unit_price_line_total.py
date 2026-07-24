# Generated manually on 2026-07-24

from decimal import Decimal

from django.db import migrations, models


def populate_line_total(apps, schema_editor):
    ReceiptItem = apps.get_model("receipt", "ReceiptItem")
    for item in ReceiptItem.objects.all().iterator():
        unit_price = item.unit_price
        quantity = item.quantity or 1
        if unit_price is None:
            line_total = Decimal("0.00")
        else:
            line_total = Decimal(str(unit_price)) * Decimal(str(quantity))
        item.line_total = line_total.quantize(Decimal("0.01"))
        item.save(update_fields=["line_total"])


class Migration(migrations.Migration):

    dependencies = [
        ("receipt", "0011_alter_receiptitem_category"),
    ]

    operations = [
        migrations.RenameField(
            model_name="receiptitem",
            old_name="price",
            new_name="unit_price",
        ),
        migrations.AlterField(
            model_name="receiptitem",
            name="unit_price",
            field=models.DecimalField(
                max_digits=10,
                decimal_places=2,
                null=True,
                blank=True,
            ),
        ),
        migrations.AddField(
            model_name="receiptitem",
            name="line_total",
            field=models.DecimalField(
                max_digits=10,
                decimal_places=2,
                default=Decimal("0.00"),
            ),
            preserve_default=False,
        ),
        migrations.RunPython(populate_line_total, migrations.RunPython.noop),
    ]
