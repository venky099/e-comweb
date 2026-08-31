"""Create the admin roles the spec asks for (MST section 35).

    python manage.py seed_roles

Roles are Django groups holding model permissions, so they work everywhere
permissions already work -- the admin, the API and any view using
``permission_required`` -- rather than being a second parallel system.

Idempotent. Re-run it after adding an app and the new permissions are picked
up; permissions granted by hand to a group are left alone unless --strict is
passed.
"""
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db import transaction

VIEW, ADD, CHANGE, DELETE = "view", "add", "change", "delete"
FULL = (VIEW, ADD, CHANGE, DELETE)
EDIT = (VIEW, ADD, CHANGE)
READ = (VIEW,)

# role -> {app_label.model: actions}
ROLES = {
    "Catalogue Manager": {
        "catalog.category": FULL,
        "catalog.brand": FULL,
        "catalog.product": FULL,
        "catalog.productvariant": FULL,
        "catalog.productimage": FULL,
        "inventory.inventory": EDIT,
        "inventory.warehouse": READ,
        "inventory.stockmovement": READ,
        "marketing.banner": FULL,
        "marketing.offer": FULL,
        "marketing.flashsale": FULL,
        "marketing.flashsaleitem": FULL,
    },
    "Order Manager": {
        "orders.order": EDIT,
        "orders.orderitem": READ,
        "orders.orderstatushistory": READ,
        "orders.returnrequest": EDIT,
        "shipping.shipment": EDIT,
        "shipping.shipmentitem": EDIT,
        "shipping.trackingevent": EDIT,
        "inventory.inventory": READ,
        "invoices.invoice": READ,
        "accounts.address": READ,
    },
    "Support Agent": {
        "orders.order": READ,
        "orders.orderitem": READ,
        "orders.returnrequest": EDIT,
        "reviews.review": EDIT,
        "accounts.user": READ,
        "accounts.address": READ,
        "invoices.invoice": READ,
        "shipping.shipment": READ,
        "shipping.trackingevent": READ,
    },
    "Finance": {
        "payments.payment": READ,
        "payments.refund": EDIT,
        "invoices.invoice": READ,
        "orders.order": READ,
        "coupons.coupon": FULL,
        "coupons.couponusage": READ,
        "tax.taxrule": FULL,
        "tax.ordertaxline": READ,
        "geo.currency": EDIT,
        "geo.exchangerate": EDIT,
        "audit.auditlog": READ,
    },
    "Store Administrator": {
        # Everything the other roles can do, plus the settings that shape the
        # storefront. Deliberately not a superuser: a superuser bypasses
        # permission checks entirely, so nothing can be withheld from them.
        "geo.country": FULL,
        "geo.state": FULL,
        "geo.currency": FULL,
        "geo.exchangerate": FULL,
        "shipping.shippingzone": FULL,
        "shipping.shippingmethod": FULL,
        "shipping.shippingrate": FULL,
        "tax.taxrule": FULL,
        "inventory.warehouse": FULL,
        "audit.auditlog": READ,
    },
}


class Command(BaseCommand):
    help = "Create or update the admin roles and their permissions."

    def add_arguments(self, parser):
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Replace each role's permissions instead of adding to them.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        strict = options["strict"]
        missing = []

        for role_name, grants in ROLES.items():
            group, created = Group.objects.get_or_create(name=role_name)
            wanted = []

            for model_path, actions in grants.items():
                app_label, model_name = model_path.split(".")
                for action in actions:
                    codename = f"{action}_{model_name}"
                    permission = Permission.objects.filter(
                        content_type__app_label=app_label, codename=codename
                    ).first()
                    if permission is None:
                        missing.append(f"{app_label}.{codename}")
                        continue
                    wanted.append(permission)

            if strict:
                group.permissions.set(wanted)
            else:
                group.permissions.add(*wanted)

            self.stdout.write(
                f"  {role_name:<22} {group.permissions.count():>3} permissions"
                f"{' (new)' if created else ''}"
            )

        if missing:
            self.stdout.write(
                self.style.WARNING(
                    f"\n  {len(missing)} permission(s) not found -- run migrate first:"
                )
            )
            for name in sorted(set(missing))[:10]:
                self.stdout.write(f"    {name}")

        self.stdout.write(
            self.style.SUCCESS(f"\n{len(ROLES)} roles ready.")
        )
        self.stdout.write(
            "  Assign one in the admin under Users, and tick 'Staff status' "
            "so the account can sign in there."
        )
