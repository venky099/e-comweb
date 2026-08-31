"""Notifications and email templates (MST sections 42, 43 and 33).

The rule that matters more than any individual message: nothing here may
break what triggered it. An order that succeeded is not undone because the
mail server was down or nobody wrote the template.
"""
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.test import TestCase

from apps.cart.models import Cart, CartItem
from apps.core.tests.factories import (
    create_address,
    create_category,
    create_product,
    create_staff,
    create_user,
    create_variant,
)
from apps.geo.models import Country, Currency
from apps.inventory.models import Inventory
from apps.notifications import services
from apps.notifications.models import EmailTemplate, Notification
from apps.orders import services as order_services
from apps.orders.models import Order


class TemplateResolutionTests(TestCase):
    def setUp(self):
        self.user = create_user(email="buyer@example.test")

    def test_a_database_template_is_used_when_present(self):
        EmailTemplate.objects.create(
            code="order_confirmed",
            language="en",
            name="Confirmed",
            subject="Custom subject {{ order_number }}",
            body="<p>Custom body</p>",
        )
        subject, body = services.render("order_confirmed", {"order_number": "MST-1"})
        self.assertEqual(subject, "Custom subject MST-1")
        self.assertIn("Custom body", body)

    def test_the_file_template_is_the_fallback(self):
        subject, body = services.render("order_confirmed", {})
        self.assertIsNotNone(body)
        self.assertIn("confirmed", body.lower())

    def test_an_unknown_code_renders_nothing_rather_than_raising(self):
        subject, body = services.render("no_such_message", {})
        self.assertIsNone(subject)
        self.assertIsNone(body)

    def test_an_inactive_template_falls_through_to_the_file(self):
        EmailTemplate.objects.create(
            code="order_confirmed",
            language="en",
            name="Off",
            subject="Should not be used",
            body="nope",
            is_active=False,
        )
        subject, _body = services.render("order_confirmed", {})
        self.assertNotEqual(subject, "Should not be used")

    def test_the_default_language_covers_a_missing_translation(self):
        EmailTemplate.objects.create(
            code="order_shipped", language="en", name="EN", subject="Shipped", body="x"
        )
        subject, _body = services.render("order_shipped", {}, language="ta")
        self.assertEqual(subject, "Shipped")

    def test_a_broken_template_does_not_raise(self):
        EmailTemplate.objects.create(
            code="order_shipped",
            language="en",
            name="Broken",
            subject="{% invalid %}",
            body="x",
        )
        subject, body = services.render("order_shipped", {})
        self.assertIsNone(subject)
        self.assertIsNone(body)


class SendingTests(TestCase):
    def setUp(self):
        self.user = create_user(email="buyer@example.test")
        mail.outbox.clear()

    def test_a_message_is_sent_and_recorded(self):
        sent = services.send(
            "order_confirmed",
            to=self.user.email,
            context={"subject": "Order confirmed"},
            user=self.user,
            notify={"title": "Order confirmed", "url": "/orders/1/"},
        )
        self.assertTrue(sent)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(Notification.objects.filter(user=self.user).count(), 1)

    def test_a_missing_template_sends_nothing_and_does_not_raise(self):
        self.assertFalse(services.send("no_such_message", to=self.user.email))
        self.assertEqual(len(mail.outbox), 0)

    def test_a_failing_mail_server_is_swallowed(self):
        """The order that triggered this must not be rolled back."""
        with mock.patch(
            "apps.notifications.services.EmailMultiAlternatives.send",
            side_effect=OSError("connection refused"),
        ):
            self.assertFalse(
                services.send("order_confirmed", to=self.user.email, user=self.user)
            )

    def test_an_in_app_notification_can_be_recorded_without_email(self):
        services.record(self.user, title="Back in stock", body="Silk Saree")
        self.assertEqual(services.unread_count(self.user), 1)

    def test_marking_everything_read(self):
        for index in range(3):
            services.record(self.user, title=f"Note {index}")
        self.assertEqual(services.unread_count(self.user), 3)
        services.mark_all_read(self.user)
        self.assertEqual(services.unread_count(self.user), 0)

    def test_an_anonymous_visitor_has_no_unread_count(self):
        self.assertEqual(services.unread_count(None), 0)


class OrderEventTests(TestCase):
    def setUp(self):
        inr = Currency.objects.create(code="INR", name="Rupee", symbol="₹", is_base=True)
        Country.objects.create(iso2="IN", name="India", currency=inr)

        self.user = create_user(email="buyer@example.test")
        address = create_address(self.user, country="India", state="Karnataka")
        category = create_category(name="Sarees")
        product = create_product(category=category, name="Silk Saree", price=Decimal("1000"))
        variant = create_variant(product, stock=10)

        cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(cart=cart, variant=variant, quantity=1)
        self.order = order_services.place_order(
            self.user, cart, address, Order.PaymentMethod.COD
        )
        mail.outbox.clear()
        Notification.objects.all().delete()

    def test_confirming_an_order_notifies_the_customer(self):
        order_services.confirm_cod(self.order)
        self.assertTrue(
            Notification.objects.filter(
                user=self.user, kind=Notification.Kind.ORDER
            ).exists()
        )
        self.assertGreaterEqual(len(mail.outbox), 1)

    def test_the_notification_links_to_the_order(self):
        order_services.confirm_cod(self.order)
        note = Notification.objects.filter(user=self.user).first()
        self.assertIn(self.order.order_number, note.url + note.title)

    def test_a_status_with_no_message_notifies_nothing(self):
        order_services.confirm_cod(self.order)
        Notification.objects.all().delete()
        order_services.transition_order(self.order, Order.Status.PROCESSING)
        self.assertEqual(Notification.objects.count(), 0)


class LowStockTests(TestCase):
    def setUp(self):
        self.staff = create_staff(email="ops@example.test")
        product = create_product(name="Silk Saree")
        self.variant = create_variant(product, stock=100)
        self.inventory = Inventory.objects.get(variant=self.variant)
        Notification.objects.all().delete()

    def test_dropping_to_the_reorder_level_alerts_staff(self):
        self.inventory.quantity_available = 2
        self.inventory.save(update_fields=["quantity_available"])
        self.assertTrue(
            Notification.objects.filter(
                user=self.staff, kind=Notification.Kind.STOCK
            ).exists()
        )

    def test_healthy_stock_alerts_nobody(self):
        self.inventory.quantity_available = 90
        self.inventory.save(update_fields=["quantity_available"])
        self.assertEqual(Notification.objects.count(), 0)

    def test_the_same_alert_is_not_repeated_while_unread(self):
        """Stock changes on every order; a flooded inbox is an ignored one."""
        for level in (3, 2, 1):
            self.inventory.quantity_available = level
            self.inventory.save(update_fields=["quantity_available"])
        self.assertEqual(
            Notification.objects.filter(kind=Notification.Kind.STOCK).count(), 1
        )
