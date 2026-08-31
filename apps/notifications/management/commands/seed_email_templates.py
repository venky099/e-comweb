"""Load the built-in messages into the database so admins can edit them.

    python manage.py seed_email_templates

The file templates already work without this -- it exists so an
administrator can open the admin, see the wording that is actually going
out, and change it (section 43) rather than having to guess what exists.

Idempotent, and deliberately non-destructive: a template someone has already
edited is left exactly as they left it unless --overwrite is passed.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.notifications.models import EmailTemplate

TEMPLATES = [
    (
        "order_confirmed",
        "Order confirmed",
        "Your order {{ order.order_number }} is confirmed",
        "When an order is paid or a cash-on-delivery order is placed.",
    ),
    (
        "order_shipped",
        "Order shipped",
        "Your order {{ order.order_number }} is on its way",
        "When the first parcel for an order is dispatched.",
    ),
    (
        "order_out_for_delivery",
        "Out for delivery",
        "Your order {{ order.order_number }} arrives today",
        "When a parcel goes out for delivery.",
    ),
    (
        "order_delivered",
        "Order delivered",
        "Your order {{ order.order_number }} has been delivered",
        "When every parcel for an order has been delivered.",
    ),
    (
        "order_cancelled",
        "Order cancelled",
        "Your order {{ order.order_number }} has been cancelled",
        "When an order is cancelled, by the customer or by staff.",
    ),
    (
        "order_refunded",
        "Refund complete",
        "Refund for order {{ order.order_number }}",
        "When a refund has been processed.",
    ),
]

BODY = """<p style="font-size:16px;font-weight:600;margin:0 0 10px;">{name}</p>
<p style="margin:0 0 10px;">Hello {{{{ user.first_name|default:"there" }}}},</p>
<p style="margin:0 0 10px;">
  This is an update about your order <strong>{{{{ order.order_number }}}}</strong>.
</p>
<p style="margin:0 0 10px;color:#4a515e;">
  Total {{{{ order.currency }}}} {{{{ order.charged_total }}}}.
</p>
<p style="margin:18px 0 0;font-size:12px;color:#6b7382;">
  You can edit this message in the admin under Email templates.
</p>"""


class Command(BaseCommand):
    help = "Load the built-in email wording into editable database templates."

    def add_arguments(self, parser):
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Replace templates that have already been edited.",
        )
        parser.add_argument(
            "--language", default="en", help="Language code to seed (default: en)."
        )

    @transaction.atomic
    def handle(self, *args, **options):
        language = options["language"]
        created = skipped = updated = 0

        for code, name, subject, description in TEMPLATES:
            body = BODY.format(name=name)
            existing = EmailTemplate.objects.filter(code=code, language=language).first()

            if existing and not options["overwrite"]:
                skipped += 1
                continue
            if existing:
                existing.name = name
                existing.subject = subject
                existing.body = body
                existing.description = description
                existing.save()
                updated += 1
                continue

            EmailTemplate.objects.create(
                code=code,
                language=language,
                name=name,
                subject=subject,
                body=body,
                description=description,
                is_active=True,
            )
            created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Email templates: {created} created, {updated} updated, "
                f"{skipped} left as edited."
            )
        )
