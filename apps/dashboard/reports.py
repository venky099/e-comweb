"""Analytics and report aggregations.

Every figure here comes from a real queryset via ``aggregate``/``annotate``.
Nothing is hardcoded, cached-stale or estimated.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import Avg, Count, DecimalField, F, Max, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncDate, TruncMonth, TruncWeek
from django.utils import timezone

from apps.catalog.models import Category, Product
from apps.inventory.models import Inventory, StockMovement
from apps.orders.models import Order, OrderItem, ReturnRequest

User = get_user_model()

ZERO = Decimal("0.00")
MONEY = DecimalField(max_digits=14, decimal_places=2)

#: Statuses excluded from revenue: money that came back or never arrived.
NON_REVENUE_STATUSES = [
    Order.Status.CANCELLED,
    Order.Status.RETURNED,
    Order.Status.REFUNDED,
]


def revenue_queryset():
    """Orders that count towards sales."""
    return Order.objects.exclude(status__in=NON_REVENUE_STATUSES)


# ---------------------------------------------------------------------------
# Headline stats
# ---------------------------------------------------------------------------
def live_stats():
    """The KPI tiles at the top of the dashboard."""
    now = timezone.now()
    today = timezone.localdate()
    month_start = today.replace(day=1)
    week_start = today - timedelta(days=today.weekday())

    orders = revenue_queryset()

    money_stats = orders.aggregate(
        total_sales=Coalesce(Sum("total_amount"), Value(ZERO), output_field=MONEY),
        today_sales=Coalesce(
            Sum("total_amount", filter=Q(placed_at__date=today)), Value(ZERO), output_field=MONEY
        ),
        week_sales=Coalesce(
            Sum("total_amount", filter=Q(placed_at__date__gte=week_start)),
            Value(ZERO),
            output_field=MONEY,
        ),
        month_sales=Coalesce(
            Sum("total_amount", filter=Q(placed_at__date__gte=month_start)),
            Value(ZERO),
            output_field=MONEY,
        ),
        average_order_value=Coalesce(Avg("total_amount"), Value(ZERO), output_field=MONEY),
    )

    count_stats = Order.objects.aggregate(
        total_orders=Count("id"),
        today_orders=Count("id", filter=Q(placed_at__date=today)),
        pending=Count("id", filter=Q(status=Order.Status.PENDING)),
        confirmed=Count("id", filter=Q(status=Order.Status.CONFIRMED)),
        processing=Count("id", filter=Q(status=Order.Status.PROCESSING)),
        shipped=Count("id", filter=Q(status=Order.Status.SHIPPED)),
        delivered=Count("id", filter=Q(status=Order.Status.DELIVERED)),
        cancelled=Count("id", filter=Q(status=Order.Status.CANCELLED)),
        returned=Count("id", filter=Q(status=Order.Status.RETURNED)),
        awaiting_payment=Count("id", filter=Q(payment_status=Order.PaymentStatus.PENDING)),
    )

    customer_stats = User.objects.filter(is_staff=False).aggregate(
        total_customers=Count("id"),
        new_today=Count("id", filter=Q(created_at__date=today)),
        new_this_month=Count("id", filter=Q(created_at__date__gte=month_start)),
        active_customers=Count("id", filter=Q(orders__isnull=False), distinct=True),
    )

    product_stats = Product.objects.aggregate(
        total_products=Count("id"),
        published=Count("id", filter=Q(status=Product.Status.PUBLISHED)),
        drafts=Count("id", filter=Q(status=Product.Status.DRAFT)),
    )

    inventory_stats = {
        "out_of_stock": Inventory.objects.out_of_stock().count(),
        "low_stock": Inventory.objects.low_stock().count(),
    }

    returns_pending = ReturnRequest.objects.filter(
        status__in=[ReturnRequest.Status.REQUESTED, ReturnRequest.Status.APPROVED]
    ).count()

    refunds = Order.objects.aggregate(
        refunded_total=Coalesce(Sum("refunded_amount"), Value(ZERO), output_field=MONEY)
    )

    return {
        **money_stats,
        **count_stats,
        **customer_stats,
        **product_stats,
        **inventory_stats,
        **refunds,
        "returns_pending": returns_pending,
        "generated_at": now,
    }


# ---------------------------------------------------------------------------
# Chart series
# ---------------------------------------------------------------------------
def revenue_series(days=30):
    """Daily revenue and order count for the last ``days`` days.

    Days with no orders are filled with zeros so the line chart has no gaps.
    """
    start = timezone.localdate() - timedelta(days=days - 1)
    rows = (
        revenue_queryset()
        .filter(placed_at__date__gte=start)
        .annotate(day=TruncDate("placed_at"))
        .values("day")
        .annotate(
            revenue=Coalesce(Sum("total_amount"), Value(ZERO), output_field=MONEY),
            orders=Count("id"),
        )
        .order_by("day")
    )
    by_day = {row["day"]: row for row in rows}

    labels, revenue, orders = [], [], []
    for offset in range(days):
        day = start + timedelta(days=offset)
        row = by_day.get(day)
        labels.append(day.strftime("%d %b"))
        revenue.append(float(row["revenue"]) if row else 0.0)
        orders.append(row["orders"] if row else 0)

    return {"labels": labels, "revenue": revenue, "orders": orders}


def customer_series(days=30):
    """New customer signups per day."""
    start = timezone.localdate() - timedelta(days=days - 1)
    rows = (
        User.objects.filter(is_staff=False, created_at__date__gte=start)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(count=Count("id"))
        .order_by("day")
    )
    by_day = {row["day"]: row["count"] for row in rows}

    labels, counts = [], []
    for offset in range(days):
        day = start + timedelta(days=offset)
        labels.append(day.strftime("%d %b"))
        counts.append(by_day.get(day, 0))
    return {"labels": labels, "customers": counts}


def order_status_breakdown():
    """Donut chart of orders by status."""
    rows = Order.objects.values("status").annotate(count=Count("id")).order_by("-count")
    labels = dict(Order.Status.choices)
    return {
        "labels": [str(labels.get(r["status"], r["status"])) for r in rows],
        "data": [r["count"] for r in rows],
    }


def category_performance(limit=8):
    """Revenue by top-level category."""
    rows = (
        OrderItem.objects.exclude(order__status__in=NON_REVENUE_STATUSES)
        .filter(product__isnull=False)
        .values("product__category__name")
        .annotate(
            revenue=Coalesce(Sum("line_total"), Value(ZERO), output_field=MONEY),
            units=Coalesce(Sum("quantity"), Value(0)),
        )
        .order_by("-revenue")[:limit]
    )
    return {
        "labels": [r["product__category__name"] or "Uncategorised" for r in rows],
        "revenue": [float(r["revenue"]) for r in rows],
        "units": [r["units"] for r in rows],
    }


def payment_method_breakdown():
    rows = (
        revenue_queryset()
        .values("payment_method")
        .annotate(
            count=Count("id"),
            revenue=Coalesce(Sum("total_amount"), Value(ZERO), output_field=MONEY),
        )
        .order_by("-revenue")
    )
    labels = dict(Order.PaymentMethod.choices)
    return {
        "labels": [str(labels.get(r["payment_method"], r["payment_method"])) for r in rows],
        "data": [float(r["revenue"]) for r in rows],
        "counts": [r["count"] for r in rows],
    }


# ---------------------------------------------------------------------------
# Tabular reports
# ---------------------------------------------------------------------------
def sales_report(period="daily", start=None, end=None):
    """Sales grouped by day/week/month."""
    truncate = {"daily": TruncDate, "weekly": TruncWeek, "monthly": TruncMonth}.get(
        period, TruncDate
    )
    queryset = revenue_queryset()
    if start:
        queryset = queryset.filter(placed_at__date__gte=start)
    if end:
        queryset = queryset.filter(placed_at__date__lte=end)

    return list(
        queryset.annotate(bucket=truncate("placed_at"))
        .values("bucket")
        .annotate(
            orders=Count("id"),
            gross=Coalesce(Sum("subtotal"), Value(ZERO), output_field=MONEY),
            discounts=Coalesce(
                Sum(F("coupon_discount") + F("product_discount")), Value(ZERO), output_field=MONEY
            ),
            delivery=Coalesce(Sum("delivery_charge"), Value(ZERO), output_field=MONEY),
            tax=Coalesce(Sum("tax_amount"), Value(ZERO), output_field=MONEY),
            net=Coalesce(Sum("total_amount"), Value(ZERO), output_field=MONEY),
            units=Coalesce(Sum("items__quantity"), Value(0)),
        )
        .order_by("-bucket")
    )


def product_report(kind="best_sellers", limit=50):
    """Best sellers, slow movers or out-of-stock lines."""
    if kind == "out_of_stock":
        return list(
            Inventory.objects.out_of_stock()
            .with_variant()
            .values(
                "variant__sku",
                "variant__product__name",
                "variant__product__category__name",
                "quantity_available",
                "quantity_reserved",
                "quantity_sold",
            )[:limit]
        )

    if kind == "low_stock":
        return list(
            Inventory.objects.low_stock()
            .with_variant()
            .values(
                "variant__sku",
                "variant__product__name",
                "variant__product__category__name",
                "quantity_available",
                "quantity_reserved",
                "low_stock_threshold",
            )[:limit]
        )

    base = (
        OrderItem.objects.exclude(order__status__in=NON_REVENUE_STATUSES)
        .filter(product__isnull=False)
        .values("product__id", "product__name", "sku", "product__category__name")
        .annotate(
            units_sold=Coalesce(Sum("quantity"), Value(0)),
            revenue=Coalesce(Sum("line_total"), Value(ZERO), output_field=MONEY),
            order_count=Count("order", distinct=True),
        )
    )
    ordering = "-units_sold" if kind == "best_sellers" else "units_sold"
    return list(base.order_by(ordering)[:limit])


def customer_report(kind="top", limit=50):
    """Top spenders, newest signups, or customers who never ordered."""
    base = User.objects.filter(is_staff=False)

    if kind == "new":
        return list(
            base.order_by("-created_at").values(
                "id", "email", "first_name", "last_name", "phone", "created_at"
            )[:limit]
        )

    if kind == "inactive":
        return list(
            base.filter(orders__isnull=True).values(
                "id", "email", "first_name", "last_name", "phone", "created_at"
            )[:limit]
        )

    return list(
        base.annotate(
            order_count=Count("orders", filter=~Q(orders__status__in=NON_REVENUE_STATUSES)),
            lifetime_value=Coalesce(
                Sum(
                    "orders__total_amount",
                    filter=~Q(orders__status__in=NON_REVENUE_STATUSES),
                ),
                Value(ZERO),
                output_field=MONEY,
            ),
            last_order=Max("orders__placed_at"),
        )
        .filter(order_count__gt=0)
        .order_by("-lifetime_value")
        .values(
            "id",
            "email",
            "first_name",
            "last_name",
            "order_count",
            "lifetime_value",
            "last_order",
        )[:limit]
    )


def revenue_report(start=None, end=None):
    """Gross -> discounts -> delivery -> tax -> refunds -> net."""
    queryset = revenue_queryset()
    if start:
        queryset = queryset.filter(placed_at__date__gte=start)
    if end:
        queryset = queryset.filter(placed_at__date__lte=end)

    totals = queryset.aggregate(
        orders=Count("id"),
        gross=Coalesce(Sum("subtotal"), Value(ZERO), output_field=MONEY),
        product_discounts=Coalesce(Sum("product_discount"), Value(ZERO), output_field=MONEY),
        coupon_discounts=Coalesce(Sum("coupon_discount"), Value(ZERO), output_field=MONEY),
        delivery=Coalesce(Sum("delivery_charge"), Value(ZERO), output_field=MONEY),
        tax=Coalesce(Sum("tax_amount"), Value(ZERO), output_field=MONEY),
        net=Coalesce(Sum("total_amount"), Value(ZERO), output_field=MONEY),
    )

    refunded = Order.objects.aggregate(
        refunds=Coalesce(Sum("refunded_amount"), Value(ZERO), output_field=MONEY)
    )["refunds"]

    totals["refunds"] = refunded
    totals["net_after_refunds"] = totals["net"] - refunded
    return totals


def inventory_report(kind="current", limit=200):
    """Current levels, or recent stock movement."""
    if kind == "movement":
        return list(
            StockMovement.objects.select_related("variant__product")
            .values(
                "created_at",
                "variant__sku",
                "variant__product__name",
                "reason",
                "quantity",
                "quantity_after",
                "reference",
            )
            .order_by("-created_at")[:limit]
        )

    queryset = Inventory.objects.with_variant()
    if kind == "low":
        queryset = queryset.low_stock()
    elif kind == "out":
        queryset = queryset.out_of_stock()

    return list(
        queryset.values(
            "variant__sku",
            "variant__product__name",
            "variant__size",
            "variant__color",
            "quantity_available",
            "quantity_reserved",
            "quantity_sold",
            "low_stock_threshold",
            "warehouse_location",
        )[:limit]
    )


def recent_activity(limit=10):
    """Feed shown beside the charts."""
    return {
        "orders": Order.objects.select_related("user").order_by("-placed_at")[:limit],
        "returns": ReturnRequest.objects.select_related("order", "order_item").order_by(
            "-created_at"
        )[:limit],
        "low_stock": Inventory.objects.low_stock().with_variant()[:limit],
        "new_customers": User.objects.filter(is_staff=False).order_by("-created_at")[:limit],
    }


def top_categories(limit=5):
    return list(
        Category.objects.annotate(
            product_count=Count("products", filter=Q(products__status=Product.Status.PUBLISHED))
        )
        .filter(product_count__gt=0)
        .order_by("-product_count")[:limit]
    )
