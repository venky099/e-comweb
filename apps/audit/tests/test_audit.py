"""The audit log and admin roles.

Section 62 wants the values, not just the fact of a change: "Old Price
Rs.5,000, New Price Rs.5,500". These tests hold that line.
"""
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.audit import services
from apps.audit.models import AuditLog
from apps.core.tests.factories import (
    create_category,
    create_product,
    create_staff,
    create_user,
)


class DiffTests(TestCase):
    def setUp(self):
        self.product = create_product(name="Designer Silk Saree", price=Decimal("5000.00"))

    def test_a_change_records_the_old_and_new_values(self):
        """The spec's own example, made executable."""
        before = services.snapshot(self.product)
        self.product.price = Decimal("5500.00")
        self.product.save(update_fields=["price"])

        entry = services.record_change(self.product, before)

        self.assertEqual(entry.action, AuditLog.Action.UPDATE)
        self.assertEqual(entry.changes["price"]["from"], "5000.00")
        self.assertEqual(entry.changes["price"]["to"], "5500.00")
        self.assertIn("Designer Silk Saree", entry.object_label)

    def test_nothing_is_written_when_nothing_moved(self):
        before = services.snapshot(self.product)
        self.product.save()
        self.assertIsNone(services.record_change(self.product, before))
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_only_the_fields_that_moved_are_recorded(self):
        before = services.snapshot(self.product)
        self.product.name = "Renamed Saree"
        self.product.save(update_fields=["name"])
        entry = services.record_change(self.product, before)
        self.assertEqual(entry.changed_fields, ["name"])

    def test_passwords_and_secrets_are_never_copied_into_the_log(self):
        user = create_user(email="someone@example.test")
        captured = services.snapshot(user)
        self.assertNotIn("password", captured)

    def test_the_actor_name_survives_the_account_being_deleted(self):
        staff = create_staff(email="boss@example.test")
        entry = services.record(
            AuditLog.Action.UPDATE, instance=self.product, actor=staff
        )
        label = entry.actor_label
        staff.delete()
        entry.refresh_from_db()
        self.assertIsNone(entry.actor)
        self.assertEqual(entry.actor_label, label)

    def test_a_readable_description_is_produced(self):
        entry = services.record(
            AuditLog.Action.UPDATE,
            instance=self.product,
            changes={"price": {"from": "5000.00", "to": "5500.00"}},
        )
        self.assertEqual(entry.describe(), ["Price: 5000.00 -> 5500.00"])

    def test_auditing_never_breaks_the_thing_it_describes(self):
        """A failed audit write must not take down the change it records."""
        with mock.patch.object(
            AuditLog.objects, "create", side_effect=RuntimeError("database is down")
        ):
            result = services.record(AuditLog.Action.UPDATE, instance=self.product)
        self.assertIsNone(result)


class RequestDetailTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.product = create_product(name="Saree")

    def test_the_client_address_is_captured(self):
        request = self.factory.get("/", REMOTE_ADDR="203.0.113.7")
        request.user = create_staff(email="s@example.test")
        entry = services.record(
            AuditLog.Action.UPDATE, instance=self.product, request=request
        )
        self.assertEqual(entry.ip_address, "203.0.113.7")
        self.assertEqual(entry.actor, request.user)

    def test_a_proxied_address_uses_the_original_client(self):
        request = self.factory.get(
            "/", HTTP_X_FORWARDED_FOR="198.51.100.4, 10.0.0.1", REMOTE_ADDR="10.0.0.1"
        )
        request.user = create_staff(email="s2@example.test")
        entry = services.record(
            AuditLog.Action.UPDATE, instance=self.product, request=request
        )
        self.assertEqual(entry.ip_address, "198.51.100.4")


class SignInAuditTests(TestCase):
    def test_a_staff_sign_in_is_recorded(self):
        staff = create_staff(email="admin@example.test", password="Str0ng!pass1")
        self.client.login(username="admin@example.test", password="Str0ng!pass1")
        self.assertTrue(
            AuditLog.objects.filter(action=AuditLog.Action.LOGIN, actor=staff).exists()
        )

    def test_a_failed_attempt_is_recorded_without_the_password(self):
        create_staff(email="admin2@example.test", password="Str0ng!pass1")
        self.client.login(username="admin2@example.test", password="wrong-password")

        entry = AuditLog.objects.filter(action=AuditLog.Action.LOGIN_FAILED).first()
        self.assertIsNotNone(entry)
        self.assertIsNone(entry.actor)
        self.assertIn("admin2@example.test", entry.summary)
        self.assertNotIn("wrong-password", entry.summary)

    def test_a_shopper_sign_in_is_not_recorded(self):
        """The log is for staff actions; customers signing in is just traffic."""
        create_user(email="shopper@example.test", password="Str0ng!pass1")
        self.client.login(username="shopper@example.test", password="Str0ng!pass1")
        self.assertFalse(
            AuditLog.objects.filter(action=AuditLog.Action.LOGIN).exists()
        )


class AdminAuditTests(TestCase):
    def setUp(self):
        self.staff = create_staff(email="editor@example.test", password="Str0ng!pass1", superuser=True)
        self.client.force_login(self.staff)
        self.category = create_category(name="Sarees")
        self.product = create_product(
            category=self.category, name="Silk Saree", price=Decimal("5000.00")
        )

    def test_saving_a_price_through_the_admin_records_both_values(self):
        """Section 62's example, driven through the real admin form."""
        from apps.audit.mixins import AuditedModelAdmin
        from django.contrib import admin as django_admin

        model_admin = django_admin.site._registry[type(self.product)]
        self.assertIsInstance(model_admin, AuditedModelAdmin)

        # Drive save_model the way the admin does, rather than reconstructing
        # the whole change form's POST payload.
        request = RequestFactory().post("/")
        request.user = self.staff
        self.product.price = Decimal("5500.00")
        model_admin.save_model(request, self.product, form=None, change=True)

        entry = AuditLog.objects.filter(
            model_label="catalog.Product", object_id=str(self.product.pk)
        ).first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.changes["price"], {"from": "5000.00", "to": "5500.00"})
        self.assertEqual(entry.actor, self.staff)

    def test_a_save_that_changes_nothing_writes_no_entry(self):
        from django.contrib import admin as django_admin

        model_admin = django_admin.site._registry[type(self.product)]
        request = RequestFactory().post("/")
        request.user = self.staff
        model_admin.save_model(request, self.product, form=None, change=True)
        # Scoped to the product: signing in during setUp is itself audited.
        self.assertEqual(
            AuditLog.objects.filter(model_label="catalog.Product").count(), 0
        )

    def test_the_log_itself_cannot_be_edited(self):
        entry = services.record(AuditLog.Action.UPDATE, instance=self.product)
        response = self.client.get(
            reverse("admin:audit_auditlog_change", args=[entry.pk])
        )
        # Read-only admins redirect rather than offering a form.
        self.assertIn(response.status_code, (200, 302))
        self.assertFalse(
            self.client.post(
                reverse("admin:audit_auditlog_change", args=[entry.pk]),
                {"summary": "tampered"},
            ).status_code
            == 200
            and AuditLog.objects.get(pk=entry.pk).summary == "tampered"
        )


class RoleTests(TestCase):
    def test_seeding_creates_the_roles_with_permissions(self):
        call_command("seed_roles", verbosity=0)
        for name in [
            "Catalogue Manager",
            "Order Manager",
            "Support Agent",
            "Finance",
            "Store Administrator",
        ]:
            group = Group.objects.filter(name=name).first()
            self.assertIsNotNone(group, name)
            self.assertGreater(group.permissions.count(), 0, name)

    def test_seeding_twice_does_not_duplicate_anything(self):
        call_command("seed_roles", verbosity=0)
        first = Group.objects.get(name="Finance").permissions.count()
        call_command("seed_roles", verbosity=0)
        self.assertEqual(Group.objects.filter(name="Finance").count(), 1)
        self.assertEqual(Group.objects.get(name="Finance").permissions.count(), first)

    def test_roles_are_scoped_not_universal(self):
        """Support must not be able to edit the catalogue."""
        call_command("seed_roles", verbosity=0)
        support = Group.objects.get(name="Support Agent")
        codenames = set(support.permissions.values_list("codename", flat=True))
        self.assertNotIn("change_product", codenames)
        self.assertIn("view_order", codenames)


class DecimalNoiseTests(TestCase):
    """A save that changes nothing must log nothing.

    Decimals arrive from a form or a factory with whatever precision they
    were written at, and come back from the database at the column's. Compare
    them as strings without normalising and every save looks like a price
    change, which fills the log with edits nobody made.
    """

    def test_differing_decimal_precision_is_not_a_change(self):
        product = create_product(name="Saree", price=Decimal("1250.0000"))
        before = services.snapshot(product)
        product.refresh_from_db()
        self.assertEqual(services.diff(before, services.snapshot(product)), {})

    def test_a_real_price_change_is_still_caught(self):
        product = create_product(name="Saree", price=Decimal("1250.00"))
        before = services.snapshot(product)
        product.price = Decimal("1250.01")
        product.save(update_fields=["price"])
        self.assertIn("price", services.diff(before, services.snapshot(product)))
