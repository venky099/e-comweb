"""Parcels, partial shipments and tracking (MST spec section 31).

The properties worth pinning: an order can leave in more than one box, the
same goods cannot be shipped twice, and an order is only delivered once every
parcel is.
"""
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.cart.models import Cart, CartItem
from apps.core.tests.factories import (
    create_address,
    create_category,
    create_product,
    create_user,
    create_variant,
)
from apps.geo.models import Country, Currency
from apps.orders import services as order_services
from apps.orders.models import Order
from apps.shipping import fulfilment
from apps.shipping.shipments import Shipment, ShipmentItem


class ShipmentTestCase(TestCase):
    def setUp(self):
        self.inr = Currency.objects.create(
            code="INR", name="Rupee", symbol="₹", is_base=True
        )
        Country.objects.create(iso2="IN", name="India", currency=self.inr)

        self.user = create_user(email="buyer@example.test")
        self.address = create_address(self.user, country="India", state="Karnataka")
        category = create_category(name="Sarees")
        product = create_product(
            category=category, name="Silk Saree", price=Decimal("1000.00")
        )
        self.variant = create_variant(product, stock=20)
        self.variant.weight_grams = 300
        self.variant.save(update_fields=["weight_grams"])

        cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(cart=cart, variant=self.variant, quantity=3)
        self.order = order_services.place_order(
            self.user, cart, self.address, Order.PaymentMethod.COD
        )
        order_services.confirm_cod(self.order)
        self.order.refresh_from_db()
        self.line = self.order.items.first()


class CreationTests(ShipmentTestCase):
    def test_shipping_everything_outstanding_by_default(self):
        shipment = fulfilment.create_shipment(self.order, carrier="Bluedart")
        self.assertEqual(shipment.items.count(), 1)
        self.assertEqual(shipment.items.first().quantity, 3)
        self.assertEqual(fulfilment.remaining_to_ship(self.order), {})

    def test_the_parcel_number_uses_the_shipment_series(self):
        shipment = fulfilment.create_shipment(self.order)
        prefix, kind, _year, _seq = shipment.number.split("-")
        self.assertEqual((prefix, kind), ("MST", "SHP"))

    def test_the_parcel_weight_is_the_sum_of_what_is_in_it(self):
        shipment = fulfilment.create_shipment(self.order)
        self.assertEqual(shipment.weight_grams, 900)

    def test_an_order_can_leave_in_two_parcels(self):
        first = fulfilment.create_shipment(self.order, items={self.line: 2})
        self.assertEqual(fulfilment.remaining_to_ship(self.order), {self.line: 1})

        second = fulfilment.create_shipment(self.order)
        self.assertNotEqual(first.number, second.number)
        self.assertEqual(second.items.first().quantity, 1)
        self.assertEqual(fulfilment.remaining_to_ship(self.order), {})

    def test_the_same_goods_cannot_be_shipped_twice(self):
        fulfilment.create_shipment(self.order)
        with self.assertRaises(fulfilment.ShipmentError):
            fulfilment.create_shipment(self.order)

    def test_over_shipping_is_refused_rather_than_clamped(self):
        with self.assertRaises(fulfilment.ShipmentError):
            fulfilment.create_shipment(self.order, items={self.line: 4})

    def test_a_parcel_cannot_contain_nothing(self):
        with self.assertRaises(fulfilment.ShipmentError):
            fulfilment.create_shipment(self.order, items={self.line: 0})

    def test_customs_details_are_prepared_for_international_parcels(self):
        shipment = fulfilment.create_shipment(self.order)
        self.assertEqual(shipment.declared_value, Decimal("3000.00"))
        self.assertIn("Silk Saree", shipment.contents_description)


class TrackingTests(ShipmentTestCase):
    def setUp(self):
        super().setUp()
        self.shipment = fulfilment.create_shipment(self.order, carrier="Bluedart")

    def test_an_event_moves_the_parcel_and_stamps_the_time(self):
        fulfilment.record_event(
            self.shipment, Shipment.Status.DISPATCHED, location="Bengaluru"
        )
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, Shipment.Status.DISPATCHED)
        self.assertIsNotNone(self.shipment.dispatched_at)

    def test_repeated_carrier_updates_are_all_kept(self):
        """Carriers resend; the history should show that they did."""
        for _ in range(3):
            fulfilment.record_event(self.shipment, Shipment.Status.IN_TRANSIT)
        self.assertEqual(self.shipment.events.count(), 3)

    def test_dispatching_a_parcel_marks_the_order_shipped(self):
        fulfilment.record_event(self.shipment, Shipment.Status.DISPATCHED)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.SHIPPED)

    def test_delivering_the_only_parcel_delivers_the_order(self):
        fulfilment.record_event(self.shipment, Shipment.Status.DISPATCHED)
        fulfilment.record_event(self.shipment, Shipment.Status.DELIVERED)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.DELIVERED)
        self.shipment.refresh_from_db()
        self.assertIsNotNone(self.shipment.delivered_at)

    def test_one_delivered_parcel_does_not_deliver_a_split_order(self):
        """The order is still partly in transit -- do not close it."""
        self.shipment.delete()
        first = fulfilment.create_shipment(self.order, items={self.line: 2})
        second = fulfilment.create_shipment(self.order, items={self.line: 1})

        fulfilment.record_event(first, Shipment.Status.DELIVERED)
        self.order.refresh_from_db()
        self.assertNotEqual(self.order.status, Order.Status.DELIVERED)

        fulfilment.record_event(second, Shipment.Status.DELIVERED)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.DELIVERED)

    def test_the_latest_event_is_readable_from_the_parcel(self):
        fulfilment.record_event(
            self.shipment,
            Shipment.Status.IN_TRANSIT,
            description="Left the facility",
            occurred_at=timezone.now() - timezone.timedelta(hours=2),
        )
        fulfilment.record_event(
            self.shipment, Shipment.Status.OUT_FOR_DELIVERY, description="On the van"
        )
        self.assertEqual(self.shipment.latest_event.description, "On the van")

    def test_a_carrier_payload_is_kept_verbatim(self):
        payload = {"code": "IT", "raw": "scan at hub"}
        event = fulfilment.record_event(
            self.shipment, Shipment.Status.IN_TRANSIT, raw=payload
        )
        self.assertEqual(event.raw, payload)

    def test_a_parcel_update_survives_an_order_that_cannot_transition(self):
        """The parcel really did move, whatever the order's state machine says."""
        self.order.status = Order.Status.CANCELLED
        self.order.save(update_fields=["status"])
        fulfilment.record_event(self.shipment, Shipment.Status.DISPATCHED)
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, Shipment.Status.DISPATCHED)
