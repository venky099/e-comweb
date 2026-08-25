"""Small, explicit object factories for the test suite.

Deliberately plain functions rather than a factory library: the tests read
better when the data they depend on is visible at the call site.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.accounts.models import Address
from apps.catalog.models import Brand, Category, Product, ProductVariant
from apps.coupons.models import Coupon
from apps.inventory.models import Inventory

User = get_user_model()

_counter = {"n": 0}


def _next():
    _counter["n"] += 1
    return _counter["n"]


def create_user(email=None, password="TestPass!2345", **kwargs):
    index = _next()
    email = email or f"customer{index}@example.test"
    return User.objects.create_user(
        username=email,
        email=email,
        password=password,
        first_name=kwargs.pop("first_name", f"Test{index}"),
        last_name=kwargs.pop("last_name", "User"),
        **kwargs,
    )


def create_staff(email=None, password="StaffPass!2345", superuser=False, **kwargs):
    index = _next()
    email = email or f"staff{index}@example.test"
    if superuser:
        return User.objects.create_superuser(
            username=email, email=email, password=password, **kwargs
        )
    return User.objects.create_user(
        username=email, email=email, password=password, is_staff=True, **kwargs
    )


def create_address(user, **kwargs):
    defaults = {
        "full_name": user.get_full_name() or "Test User",
        "phone": "9876543210",
        "line1": "12 Test Street",
        "city": "Bengaluru",
        "state": "Karnataka",
        "postal_code": "560001",
    }
    defaults.update(kwargs)
    return Address.objects.create(user=user, **defaults)


def create_category(name=None, **kwargs):
    index = _next()
    return Category.objects.create(name=name or f"Category {index}", **kwargs)


def create_brand(name=None, **kwargs):
    index = _next()
    return Brand.objects.create(name=name or f"Brand {index}", **kwargs)


def create_product(category=None, price="1000.00", stock=10, **kwargs):
    """A published product with one variant and ``stock`` units available."""
    index = _next()
    category = category or create_category()

    product = Product.objects.create(
        name=kwargs.pop("name", f"Test Product {index}"),
        sku=kwargs.pop("sku", f"TEST-SKU-{index}"),
        category=category,
        price=Decimal(price),
        compare_at_price=kwargs.pop("compare_at_price", Decimal(price) * Decimal("1.25")),
        status=kwargs.pop("status", Product.Status.PUBLISHED),
        is_active=kwargs.pop("is_active", True),
        published_at=timezone.now(),
        **kwargs,
    )
    variant = create_variant(product, stock=stock)
    product.default_test_variant = variant  # convenience handle for tests
    return product


def create_variant(product, stock=10, **kwargs):
    """Add a variant with ``stock`` sellable units.

    ``(product, size, color)`` is unique, so when no options are given and the
    product already has an optionless variant, a distinguishing size is
    generated rather than letting the test blow up on a constraint.
    """
    index = _next()
    size = kwargs.pop("size", None)
    color = kwargs.pop("color", None)

    if size is None and color is None:
        exists = ProductVariant.objects.filter(product=product, size="", color="").exists()
        size, color = (f"V{index}", "") if exists else ("", "")

    variant = ProductVariant.objects.create(
        product=product,
        sku=kwargs.pop("sku", f"{product.sku}-V{index}"),
        size=size or "",
        color=color or "",
        **kwargs,
    )
    # The catalog signal creates the inventory row; set the level explicitly.
    inventory = Inventory.objects.get(variant=variant)
    inventory.quantity_available = stock
    inventory.save(update_fields=["quantity_available"])
    return variant


def variant_of(product):
    return product.variants.first()


def create_coupon(code=None, **kwargs):
    index = _next()
    defaults = {
        "discount_type": Coupon.DiscountType.PERCENTAGE,
        "value": Decimal("10"),
        "min_order_value": Decimal("0"),
        "usage_limit_per_user": 5,
        "valid_from": timezone.now() - timezone.timedelta(days=1),
    }
    defaults.update(kwargs)
    return Coupon.objects.create(code=code or f"TEST{index}", **defaults)
