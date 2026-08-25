"""Dashboard access control, aggregate correctness and CSV exports."""
import csv
import io
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.cart.models import Cart, CartItem
from apps.core.tests.factories import (
    create_address,
    create_product,
    create_staff,
    create_user,
    variant_of,
)
from apps.dashboard import reports
from apps.orders import services as order_services
from apps.orders.models import Order


def place_and_deliver(user, product, quantity=1, staff=None):
    address = user.default_address() or create_address(user)
    cart, _ = Cart.objects.get_or_create(user=user)
    CartItem.objects.create(cart=cart, variant=variant_of(product), quantity=quantity)
    order = order_services.place_order(user, cart, address, Order.PaymentMethod.COD)

    staff = staff or create_staff(superuser=True)
    for status in (
        Order.Status.CONFIRMED,
        Order.Status.PROCESSING,
        Order.Status.SHIPPED,
        Order.Status.DELIVERED,
    ):
        order_services.transition_order(order, status, user=staff)
    order.refresh_from_db()
    return order


class DashboardAccessTests(TestCase):
    URLS = [
        "dashboard:index",
        "dashboard:reports",
        "dashboard:stats_json",
    ]

    def test_anonymous_is_redirected(self):
        for name in self.URLS:
            with self.subTest(view=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)

    def test_customer_is_refused(self):
        self.client.force_login(create_user())
        for name in self.URLS:
            with self.subTest(view=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)

    def test_staff_is_allowed(self):
        self.client.force_login(create_staff(superuser=True))
        for name in self.URLS:
            with self.subTest(view=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)

    def test_customer_cannot_export_reports(self):
        self.client.force_login(create_user())
        response = self.client.get(reverse("dashboard:export", args=["sales"]))
        self.assertEqual(response.status_code, 302)


class LiveStatsTests(TestCase):
    """Figures must come from real querysets, not placeholders."""

    def setUp(self):
        self.staff = create_staff(superuser=True)
        self.customer = create_user()
        create_address(self.customer)
        self.product = create_product(price="1000.00", stock=20)

    def test_empty_store_reports_zeroes_not_errors(self):
        stats = reports.live_stats()
        self.assertEqual(stats["total_sales"], Decimal("0.00"))
        self.assertEqual(stats["total_orders"], 0)
        self.assertEqual(stats["returns_pending"], 0)

    def test_delivered_order_counts_towards_sales(self):
        order = place_and_deliver(self.customer, self.product, staff=self.staff)

        stats = reports.live_stats()
        self.assertEqual(stats["total_orders"], 1)
        self.assertEqual(stats["delivered"], 1)
        self.assertEqual(stats["total_sales"], order.total_amount)
        self.assertEqual(stats["today_sales"], order.total_amount)

    def test_cancelled_order_is_excluded_from_revenue(self):
        address = create_address(create_user())
        buyer = address.user
        cart = Cart.objects.create(user=buyer)
        CartItem.objects.create(cart=cart, variant=variant_of(self.product), quantity=1)
        order = order_services.place_order(buyer, cart, address, Order.PaymentMethod.COD)
        order_services.cancel_order(order, user=buyer, reason="Changed my mind")

        stats = reports.live_stats()
        self.assertEqual(stats["total_sales"], Decimal("0.00"))
        self.assertEqual(stats["cancelled"], 1)
        self.assertEqual(stats["total_orders"], 1)  # still an order, just not revenue

    def test_out_of_stock_and_low_stock_counts(self):
        create_product(price="100.00", stock=0)
        create_product(price="100.00", stock=2)

        stats = reports.live_stats()
        self.assertGreaterEqual(stats["out_of_stock"], 1)
        self.assertGreaterEqual(stats["low_stock"], 1)

    def test_customer_count_excludes_staff(self):
        stats = reports.live_stats()
        self.assertEqual(stats["total_customers"], 1)  # self.customer only


class ChartDataTests(TestCase):
    def setUp(self):
        self.staff = create_staff(superuser=True)
        self.client.force_login(self.staff)

    def test_every_chart_endpoint_returns_json(self):
        for chart in ("revenue", "customers", "order-status", "categories", "payment-methods"):
            with self.subTest(chart=chart):
                response = self.client.get(reverse("dashboard:chart_data", args=[chart]))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Type"], "application/json")

    def test_unknown_chart_returns_404(self):
        response = self.client.get(reverse("dashboard:chart_data", args=["nonsense"]))
        self.assertEqual(response.status_code, 404)

    def test_revenue_series_fills_missing_days_with_zero(self):
        series = reports.revenue_series(days=7)
        self.assertEqual(len(series["labels"]), 7)
        self.assertEqual(len(series["revenue"]), 7)
        self.assertTrue(all(isinstance(value, float) for value in series["revenue"]))

    def test_range_is_clamped(self):
        response = self.client.get(
            reverse("dashboard:chart_data", args=["revenue"]), {"days": "99999"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["labels"]), 365)

    def test_bad_range_falls_back_to_default(self):
        response = self.client.get(
            reverse("dashboard:chart_data", args=["revenue"]), {"days": "abc"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["labels"]), 30)


class ReportTests(TestCase):
    def setUp(self):
        self.staff = create_staff(superuser=True)
        self.customer = create_user()
        create_address(self.customer)
        self.product = create_product(price="1000.00", stock=20, name="Reported Product")
        self.order = place_and_deliver(self.customer, self.product, 2, staff=self.staff)
        self.client.force_login(self.staff)

    def test_sales_report_groups_by_period(self):
        rows = reports.sales_report("daily")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["orders"], 1)
        self.assertEqual(rows[0]["units"], 2)
        self.assertEqual(rows[0]["net"], self.order.total_amount)

    def test_product_report_best_sellers(self):
        rows = reports.product_report("best_sellers")
        self.assertEqual(rows[0]["product__name"], "Reported Product")
        self.assertEqual(rows[0]["units_sold"], 2)

    def test_customer_report_top_spenders(self):
        rows = reports.customer_report("top")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["email"], self.customer.email)
        self.assertEqual(rows[0]["lifetime_value"], self.order.total_amount)

    def test_customer_report_inactive(self):
        create_user()  # never ordered
        rows = reports.customer_report("inactive")
        self.assertEqual(len(rows), 1)

    def test_revenue_report_totals(self):
        totals = reports.revenue_report()
        self.assertEqual(totals["orders"], 1)
        self.assertEqual(totals["gross"], Decimal("2000.00"))
        self.assertEqual(totals["net"], self.order.total_amount)
        self.assertEqual(totals["refunds"], Decimal("0.00"))

    def test_inventory_report_lists_variants(self):
        rows = reports.inventory_report("current")
        self.assertTrue(any(r["variant__product__name"] == "Reported Product" for r in rows))

    def test_inventory_movement_report_is_populated(self):
        rows = reports.inventory_report("movement")
        self.assertTrue(rows)
        self.assertIn("reason", rows[0])

    def test_report_pages_render_for_every_kind(self):
        cases = [
            {"report": "sales", "period": "daily"},
            {"report": "product", "kind": "best_sellers"},
            {"report": "product", "kind": "low_stock"},
            {"report": "customer", "kind": "top"},
            {"report": "customer", "kind": "new"},
            {"report": "revenue"},
            {"report": "inventory", "kind": "current"},
            {"report": "inventory", "kind": "movement"},
        ]
        for params in cases:
            with self.subTest(**params):
                response = self.client.get(reverse("dashboard:reports"), params)
                self.assertEqual(response.status_code, 200)


class CsvExportTests(TestCase):
    def setUp(self):
        self.staff = create_staff(superuser=True)
        self.customer = create_user()
        create_address(self.customer)
        self.product = create_product(price="1000.00", stock=20, name="Exported Product")
        self.order = place_and_deliver(self.customer, self.product, staff=self.staff)
        self.client.force_login(self.staff)

    def _rows(self, response):
        content = response.content.decode("utf-8")
        return list(csv.reader(io.StringIO(content)))

    def test_every_export_returns_a_csv_attachment(self):
        for report in ("sales", "product", "customer", "revenue", "inventory", "orders"):
            with self.subTest(report=report):
                response = self.client.get(reverse("dashboard:export", args=[report]))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Type"], "text/csv")
                self.assertIn("attachment", response["Content-Disposition"])
                self.assertGreaterEqual(len(self._rows(response)), 1)

    def test_orders_export_contains_the_order(self):
        response = self.client.get(reverse("dashboard:export", args=["orders"]))
        rows = self._rows(response)
        self.assertEqual(rows[0][0], "Order number")
        self.assertTrue(any(self.order.order_number in row for row in rows[1:]))

    def test_revenue_export_lines_up_with_the_report(self):
        response = self.client.get(reverse("dashboard:export", args=["revenue"]))
        rows = {row[0]: row[1] for row in self._rows(response)[1:]}
        self.assertEqual(Decimal(rows["Net"]), self.order.total_amount)

    def test_unknown_report_returns_404(self):
        response = self.client.get(reverse("dashboard:export", args=["nonsense"]))
        self.assertEqual(response.status_code, 404)
