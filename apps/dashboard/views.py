"""Staff analytics dashboard, chart endpoints and CSV report exports.

These are the only screens the Django admin cannot express well: aggregated
charts and downloadable reports. Everything else lives in ``admin.py``.
"""
import csv
from datetime import datetime

from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView

from apps.orders.models import Order

from . import reports


@method_decorator(staff_member_required, name="dispatch")
class DashboardView(TemplateView):
    """Analytics home for staff."""

    template_name = "dashboard/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        days = self._days()

        context["stats"] = reports.live_stats()
        context["activity"] = reports.recent_activity()
        context["days"] = days
        context["revenue_report"] = reports.revenue_report()
        context["best_sellers"] = reports.product_report("best_sellers", limit=8)
        context["low_stock"] = reports.product_report("low_stock", limit=8)
        context["top_customers"] = reports.customer_report("top", limit=8)
        return context

    def _days(self):
        try:
            days = int(self.request.GET.get("days", 30))
        except (TypeError, ValueError):
            days = 30
        return max(min(days, 365), 7)


@staff_member_required
def chart_data(request, chart):
    """JSON feed for the Chart.js canvases on the dashboard."""
    try:
        days = max(min(int(request.GET.get("days", 30)), 365), 7)
    except (TypeError, ValueError):
        days = 30

    builders = {
        "revenue": lambda: reports.revenue_series(days),
        "customers": lambda: reports.customer_series(days),
        "order-status": reports.order_status_breakdown,
        "categories": reports.category_performance,
        "payment-methods": reports.payment_method_breakdown,
    }

    builder = builders.get(chart)
    if builder is None:
        raise Http404("Unknown chart")

    return JsonResponse(builder())


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
@method_decorator(staff_member_required, name="dispatch")
class ReportsView(TemplateView):
    """Tabbed report browser with CSV export links."""

    template_name = "dashboard/reports.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        report = self.request.GET.get("report", "sales")
        kind = self.request.GET.get("kind", "")
        start, end = self._date_range()

        context["report"] = report
        context["kind"] = kind
        context["start"] = start
        context["end"] = end

        if report == "sales":
            period = self.request.GET.get("period", "daily")
            context["period"] = period
            context["rows"] = reports.sales_report(period, start, end)
        elif report == "product":
            context["rows"] = reports.product_report(kind or "best_sellers")
        elif report == "customer":
            context["rows"] = reports.customer_report(kind or "top")
        elif report == "revenue":
            context["totals"] = reports.revenue_report(start, end)
            context["rows"] = reports.sales_report("monthly", start, end)
        elif report == "inventory":
            context["rows"] = reports.inventory_report(kind or "current")
        else:
            context["rows"] = []

        return context

    def _date_range(self):
        return (
            _parse_date(self.request.GET.get("start")),
            _parse_date(self.request.GET.get("end")),
        )


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _csv_response(filename, header, rows):
    """Stream a list of dicts/sequences out as a CSV attachment."""
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}-{stamp}.csv"'

    writer = csv.writer(response)
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    return response


@staff_member_required
def export_report(request, report):
    """CSV export for any of the dashboard reports."""
    start, end = _parse_date(request.GET.get("start")), _parse_date(request.GET.get("end"))
    kind = request.GET.get("kind", "")

    if report == "sales":
        period = request.GET.get("period", "daily")
        rows = reports.sales_report(period, start, end)
        return _csv_response(
            f"sales-{period}",
            ["Period", "Orders", "Units", "Gross", "Discounts", "Delivery", "Tax", "Net"],
            [
                [
                    r["bucket"],
                    r["orders"],
                    r["units"],
                    r["gross"],
                    r["discounts"],
                    r["delivery"],
                    r["tax"],
                    r["net"],
                ]
                for r in rows
            ],
        )

    if report == "product":
        rows = reports.product_report(kind or "best_sellers", limit=1000)
        if kind in {"out_of_stock", "low_stock"}:
            return _csv_response(
                f"products-{kind}",
                ["SKU", "Product", "Category", "Available", "Reserved", "Extra"],
                [
                    [
                        r["variant__sku"],
                        r["variant__product__name"],
                        r["variant__product__category__name"],
                        r["quantity_available"],
                        r["quantity_reserved"],
                        r.get("quantity_sold", r.get("low_stock_threshold", "")),
                    ]
                    for r in rows
                ],
            )
        return _csv_response(
            f"products-{kind or 'best-sellers'}",
            ["Product", "SKU", "Category", "Units sold", "Revenue", "Orders"],
            [
                [
                    r["product__name"],
                    r["sku"],
                    r["product__category__name"],
                    r["units_sold"],
                    r["revenue"],
                    r["order_count"],
                ]
                for r in rows
            ],
        )

    if report == "customer":
        rows = reports.customer_report(kind or "top", limit=1000)
        if kind in {"new", "inactive"}:
            return _csv_response(
                f"customers-{kind}",
                ["ID", "Email", "First name", "Last name", "Phone", "Joined"],
                [
                    [
                        r["id"],
                        r["email"],
                        r["first_name"],
                        r["last_name"],
                        r["phone"],
                        r["created_at"],
                    ]
                    for r in rows
                ],
            )
        return _csv_response(
            "customers-top",
            ["ID", "Email", "First name", "Last name", "Orders", "Lifetime value", "Last order"],
            [
                [
                    r["id"],
                    r["email"],
                    r["first_name"],
                    r["last_name"],
                    r["order_count"],
                    r["lifetime_value"],
                    r.get("last_order", ""),
                ]
                for r in rows
            ],
        )

    if report == "revenue":
        totals = reports.revenue_report(start, end)
        return _csv_response(
            "revenue",
            ["Metric", "Amount"],
            [
                ["Orders", totals["orders"]],
                ["Gross", totals["gross"]],
                ["Product discounts", totals["product_discounts"]],
                ["Coupon discounts", totals["coupon_discounts"]],
                ["Delivery", totals["delivery"]],
                ["Tax", totals["tax"]],
                ["Net", totals["net"]],
                ["Refunds", totals["refunds"]],
                ["Net after refunds", totals["net_after_refunds"]],
            ],
        )

    if report == "inventory":
        rows = reports.inventory_report(kind or "current", limit=5000)
        if kind == "movement":
            return _csv_response(
                "inventory-movement",
                ["When", "SKU", "Product", "Reason", "Change", "After", "Reference"],
                [
                    [
                        r["created_at"],
                        r["variant__sku"],
                        r["variant__product__name"],
                        r["reason"],
                        r["quantity"],
                        r["quantity_after"],
                        r["reference"],
                    ]
                    for r in rows
                ],
            )
        return _csv_response(
            f"inventory-{kind or 'current'}",
            [
                "SKU",
                "Product",
                "Size",
                "Colour",
                "Available",
                "Reserved",
                "Sold",
                "Threshold",
                "Location",
            ],
            [
                [
                    r["variant__sku"],
                    r["variant__product__name"],
                    r["variant__size"],
                    r["variant__color"],
                    r["quantity_available"],
                    r["quantity_reserved"],
                    r["quantity_sold"],
                    r["low_stock_threshold"],
                    r["warehouse_location"],
                ]
                for r in rows
            ],
        )

    if report == "orders":
        queryset = Order.objects.select_related("user").order_by("-placed_at")
        if start:
            queryset = queryset.filter(placed_at__date__gte=start)
        if end:
            queryset = queryset.filter(placed_at__date__lte=end)
        return _csv_response(
            "orders",
            [
                "Order number",
                "Placed at",
                "Customer",
                "Status",
                "Payment status",
                "Method",
                "Subtotal",
                "Discount",
                "Delivery",
                "Total",
            ],
            [
                [
                    o.order_number,
                    o.placed_at,
                    o.email,
                    o.get_status_display(),
                    o.get_payment_status_display(),
                    o.get_payment_method_display(),
                    o.subtotal,
                    o.coupon_discount,
                    o.delivery_charge,
                    o.total_amount,
                ]
                for o in queryset[:5000]
            ],
        )

    raise Http404("Unknown report")


@staff_member_required
def stats_json(request):
    """Live KPI numbers, for polling the tiles without a full page reload."""
    stats = reports.live_stats()
    return JsonResponse(
        {key: (str(value) if hasattr(value, "quantize") else value) for key, value in stats.items()},
        json_dumps_params={"default": str},
    )
