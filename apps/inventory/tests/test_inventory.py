"""Inventory service-layer behaviour."""
from django.test import TestCase

from apps.core.tests.factories import create_product, create_staff, variant_of
from apps.inventory import services
from apps.inventory.models import Inventory, StockMovement


class InventoryModelTests(TestCase):
    def setUp(self):
        self.product = create_product(stock=10)
        self.variant = variant_of(self.product)
        self.inventory = self.variant.inventory

    def test_inventory_row_is_created_with_each_variant(self):
        self.assertTrue(Inventory.objects.filter(variant=self.variant).exists())

    def test_sellable_quantity_excludes_reservations(self):
        self.inventory.quantity_reserved = 4
        self.inventory.save(update_fields=["quantity_reserved"])
        self.assertEqual(self.inventory.sellable_quantity, 6)

    def test_stock_status_labels(self):
        self.assertEqual(self.inventory.stock_status, "in_stock")

        self.inventory.quantity_available = 3
        self.inventory.save(update_fields=["quantity_available"])
        self.assertEqual(self.inventory.stock_status, "low_stock")

        self.inventory.quantity_available = 0
        self.inventory.save(update_fields=["quantity_available"])
        self.assertEqual(self.inventory.stock_status, "out_of_stock")

    def test_sellable_never_goes_negative(self):
        self.inventory.quantity_available = 2
        self.inventory.quantity_reserved = 5
        self.inventory.save(update_fields=["quantity_available", "quantity_reserved"])
        self.assertEqual(self.inventory.sellable_quantity, 0)


class StockServiceTests(TestCase):
    def setUp(self):
        self.staff = create_staff(superuser=True)
        self.product = create_product(stock=10)
        self.variant = variant_of(self.product)

    def _inventory(self):
        return Inventory.objects.get(variant=self.variant)

    def test_reserve_then_commit(self):
        services.reserve(self.variant, 3, reference="ORD-1")
        inventory = self._inventory()
        self.assertEqual(inventory.quantity_reserved, 3)
        self.assertEqual(inventory.quantity_available, 10)

        services.commit(self.variant, 3, reference="ORD-1")
        inventory = self._inventory()
        self.assertEqual(inventory.quantity_reserved, 0)
        self.assertEqual(inventory.quantity_available, 7)
        self.assertEqual(inventory.quantity_sold, 3)

    def test_reserve_then_release(self):
        services.reserve(self.variant, 4, reference="ORD-2")
        services.release(self.variant, 4, reference="ORD-2")

        inventory = self._inventory()
        self.assertEqual(inventory.quantity_reserved, 0)
        self.assertEqual(inventory.quantity_available, 10)

    def test_cannot_reserve_beyond_sellable(self):
        with self.assertRaises(services.InsufficientStock):
            services.reserve(self.variant, 11)

    def test_reservations_stack_and_block_oversell(self):
        services.reserve(self.variant, 6)
        with self.assertRaises(services.InsufficientStock):
            services.reserve(self.variant, 5)

    def test_backorder_allows_reserving_beyond_stock(self):
        inventory = self._inventory()
        inventory.allow_backorder = True
        inventory.save(update_fields=["allow_backorder"])

        services.reserve(self.variant, 50)
        self.assertEqual(self._inventory().quantity_reserved, 50)

    def test_restore_puts_units_back(self):
        services.reserve(self.variant, 3)
        services.commit(self.variant, 3)
        services.restore(self.variant, 3)

        inventory = self._inventory()
        self.assertEqual(inventory.quantity_available, 10)
        self.assertEqual(inventory.quantity_sold, 0)

    def test_restock_adds_units(self):
        services.restock(self.variant, 15, note="PO-99", user=self.staff)
        inventory = self._inventory()
        self.assertEqual(inventory.quantity_available, 25)
        self.assertIsNotNone(inventory.restocked_at)

    def test_adjust_sets_an_absolute_figure(self):
        services.adjust(self.variant, 4, note="Stocktake", user=self.staff)
        self.assertEqual(self._inventory().quantity_available, 4)

    def test_adjust_records_the_signed_delta(self):
        services.adjust(self.variant, 4, note="Stocktake", user=self.staff)
        movement = StockMovement.objects.filter(
            variant=self.variant, reason=StockMovement.Reason.ADJUSTMENT
        ).first()
        self.assertEqual(movement.quantity, -6)

    def test_zero_and_negative_quantities_are_no_ops(self):
        self.assertIsNone(services.reserve(self.variant, 0))
        self.assertIsNone(services.commit(self.variant, -3))
        self.assertEqual(self._inventory().quantity_available, 10)

    def test_every_operation_is_logged(self):
        services.reserve(self.variant, 2, reference="ORD-3")
        services.commit(self.variant, 2, reference="ORD-3")
        services.restock(self.variant, 5)

        reasons = list(
            StockMovement.objects.filter(variant=self.variant)
            .order_by("id")
            .values_list("reason", flat=True)
        )
        self.assertEqual(
            reasons,
            [
                StockMovement.Reason.RESERVATION,
                StockMovement.Reason.SALE,
                StockMovement.Reason.PURCHASE,
            ],
        )

    def test_movement_records_the_resulting_level(self):
        services.restock(self.variant, 5)
        movement = StockMovement.objects.filter(variant=self.variant).latest("id")
        self.assertEqual(movement.quantity_after, 15)

    def test_check_availability_flags_shortfalls(self):
        class FakeItem:
            def __init__(self, variant, quantity):
                self.variant = variant
                self.quantity = quantity

        problems = services.check_availability([FakeItem(self.variant, 20)])
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0][1], 10)

        self.assertEqual(services.check_availability([FakeItem(self.variant, 5)]), [])

    def test_inactive_product_is_unavailable(self):
        class FakeItem:
            def __init__(self, variant, quantity):
                self.variant = variant
                self.quantity = quantity

        self.product.is_active = False
        self.product.save(update_fields=["is_active"])
        self.variant.refresh_from_db()

        problems = services.check_availability([FakeItem(self.variant, 1)])
        self.assertEqual(len(problems), 1)


class LowStockQuerysetTests(TestCase):
    def test_low_stock_and_out_of_stock_filters(self):
        create_product(stock=0)
        create_product(stock=3)
        create_product(stock=100)

        self.assertEqual(Inventory.objects.out_of_stock().count(), 1)
        self.assertEqual(Inventory.objects.low_stock().count(), 1)

    def test_reserved_units_can_make_a_variant_out_of_stock(self):
        product = create_product(stock=5)
        variant = variant_of(product)
        services.reserve(variant, 5)

        self.assertEqual(Inventory.objects.out_of_stock().count(), 1)
