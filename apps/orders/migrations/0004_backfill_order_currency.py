"""Give existing orders a currency, a rate and charged amounts.

Orders placed before multi-currency existed have base-currency figures and
nothing in the charged columns. Left alone they would read as orders of zero
value, and any report grouping by currency would silently skip them.

They were all charged in the base currency at an implicit rate of 1, so that
is what gets written. Reversible: the reverse simply clears the columns the
forward step filled.
"""
from decimal import Decimal

from django.db import migrations


def backfill(apps, schema_editor):
    Order = apps.get_model("orders", "Order")
    Currency = apps.get_model("geo", "Currency")

    base = Currency.objects.filter(is_base=True).first()
    code = base.code if base is not None else "INR"

    # Only touch rows the new columns have never been written for. An order
    # with a charged total already set was placed after this migration's
    # feature landed and must not be rewritten.
    stale = Order.objects.filter(charged_total=Decimal("0.00"))
    for order in stale.iterator(chunk_size=500):
        order.currency = order.currency or code
        order.base_currency = code
        order.exchange_rate = Decimal("1")
        order.charged_subtotal = order.subtotal
        order.charged_discount = order.product_discount + order.coupon_discount
        order.charged_delivery_charge = order.delivery_charge
        order.charged_tax_amount = order.tax_amount
        order.charged_total = order.total_amount
        order.save(
            update_fields=[
                "currency",
                "base_currency",
                "exchange_rate",
                "charged_subtotal",
                "charged_discount",
                "charged_delivery_charge",
                "charged_tax_amount",
                "charged_total",
            ]
        )


def clear(apps, schema_editor):
    Order = apps.get_model("orders", "Order")
    Order.objects.update(
        exchange_rate=Decimal("1"),
        charged_subtotal=Decimal("0.00"),
        charged_discount=Decimal("0.00"),
        charged_delivery_charge=Decimal("0.00"),
        charged_tax_amount=Decimal("0.00"),
        charged_total=Decimal("0.00"),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0003_order_base_currency_order_charged_delivery_charge_and_more"),
        ("geo", "0002_alter_exchangerate_options"),
    ]

    operations = [migrations.RunPython(backfill, clear)]
