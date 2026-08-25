"""Populate a development database with a realistic demo store.

    python manage.py seed_data              # add demo data
    python manage.py seed_data --flush      # wipe demo data first
    python manage.py seed_data --orders 40  # more sample orders

Creates categories, brands, products with variants and stock, coupons,
banners, offers, a flash sale, customers, orders across every status, and
verified reviews -- enough to make every page, chart and report show real
numbers.
"""
import random
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.catalog.models import Brand, Category, Product, ProductImage, ProductVariant
from apps.core import images as image_factory
from apps.coupons.models import Coupon
from apps.inventory.models import Inventory
from apps.marketing.models import Banner, FlashSale, FlashSaleItem, Offer
from apps.orders.models import Order, OrderItem, OrderStatusHistory
from apps.orders.services import money
from apps.reviews.models import Review

User = get_user_model()

# ---------------------------------------------------------------------------
# Demo catalog definition
# ---------------------------------------------------------------------------
CATEGORIES = [
    ("Electronics", "bi-cpu", [
        ("Laptops", "bi-laptop"),
        ("Smartphones", "bi-phone"),
        ("Audio", "bi-headphones"),
        ("Wearables", "bi-smartwatch"),
    ]),
    ("Fashion", "bi-bag", [
        ("Men's clothing", "bi-person"),
        ("Women's clothing", "bi-person-dress"),
        ("Footwear", "bi-boot"),
    ]),
    ("Home & Kitchen", "bi-house", [
        ("Cookware", "bi-cup-hot"),
        ("Furniture", "bi-lamp"),
        ("Appliances", "bi-plug"),
    ]),
    ("Sports & Fitness", "bi-bicycle", [
        ("Fitness equipment", "bi-heart-pulse"),
        ("Outdoor gear", "bi-tree"),
    ]),
    ("Books", "bi-book", [
        ("Fiction", "bi-journal"),
        ("Technology", "bi-code-slash"),
    ]),
]

BRANDS = [
    "Aurora", "Nimbus", "Vertex", "Kestrel", "Lumen Labs",
    "Northwind", "Solace", "Ironwood", "Meridian", "Cobalt",
]

SIZES_CLOTHING = ["XS", "S", "M", "L", "XL"]
SIZES_FOOTWEAR = ["UK 6", "UK 7", "UK 8", "UK 9", "UK 10"]
COLORS = [
    ("Midnight", "#0f172a"), ("Slate", "#64748b"), ("Ivory", "#f8fafc"),
    ("Cobalt", "#1d4ed8"), ("Crimson", "#b91c1c"), ("Forest", "#166534"),
    ("Amber", "#f59e0b"), ("Blush", "#f472b6"),
]

PRODUCT_TEMPLATES = {
    "Laptops": [
        ("UltraBook 14 Pro", 89990, 104990, "14-inch, 16 GB RAM, 512 GB SSD"),
        ("WorkStation 16", 134990, 149990, "16-inch creator laptop, 32 GB RAM"),
        ("Everyday 15 Slim", 52990, 61990, "15-inch, all-day battery"),
        ("Gaming Rig 17", 159990, 179990, "17-inch, discrete graphics, 165 Hz"),
    ],
    "Smartphones": [
        ("Pulse 5G", 34999, 41999, "6.5-inch AMOLED, 5000 mAh"),
        ("Pulse Pro Max", 74999, 84999, "Flagship camera, titanium frame"),
        ("Essential Lite", 12999, 15999, "Reliable daily phone on a budget"),
    ],
    "Audio": [
        ("Quiet ANC Headphones", 18990, 24990, "Active noise cancelling, 40h battery"),
        ("Studio Monitor Buds", 7990, 9990, "In-ear monitors with balanced tuning"),
        ("Portable Party Speaker", 11490, 13990, "IPX7, 24-hour playback"),
    ],
    "Wearables": [
        ("Trail Watch GPS", 22990, 27990, "Multi-band GPS, 20-day battery"),
        ("Fitness Band 5", 3499, 4499, "Heart rate, SpO2, sleep tracking"),
    ],
    "Men's clothing": [
        ("Oxford Cotton Shirt", 1899, 2799, "Breathable long-staple cotton"),
        ("Merino Crew Sweater", 3499, 4999, "Lightweight merino wool"),
        ("Tapered Chinos", 2299, 3199, "Stretch twill, everyday fit"),
    ],
    "Women's clothing": [
        ("Linen Wrap Dress", 2999, 4299, "Breathable linen blend"),
        ("Tailored Blazer", 4599, 6499, "Structured shoulder, lined"),
        ("High-Rise Denim", 2799, 3799, "Stretch denim, ankle length"),
    ],
    "Footwear": [
        ("Trail Runner GTX", 6999, 8999, "Waterproof, grippy outsole"),
        ("Classic Leather Sneaker", 4499, 5999, "Full-grain leather upper"),
        ("Everyday Loafer", 3899, 4999, "Cushioned insole, soft leather"),
    ],
    "Cookware": [
        ("Tri-Ply Frying Pan 28cm", 3299, 4499, "Induction ready, oven safe"),
        ("Cast Iron Dutch Oven", 5999, 7999, "Enamelled, 4.5 litre"),
    ],
    "Furniture": [
        ("Ergonomic Task Chair", 15990, 21990, "Lumbar support, mesh back"),
        ("Standing Desk 140cm", 27990, 33990, "Dual motor, memory presets"),
    ],
    "Appliances": [
        ("Air Fryer 5L", 7499, 9999, "Digital presets, dishwasher-safe basket"),
        ("Bean-to-Cup Coffee Machine", 32990, 39990, "Built-in grinder, milk frother"),
    ],
    "Fitness equipment": [
        ("Adjustable Dumbbell Pair", 12990, 16990, "2.5 to 24 kg per hand"),
        ("Yoga Mat Pro 6mm", 1899, 2499, "Non-slip, closed cell"),
    ],
    "Outdoor gear": [
        ("Trekking Backpack 45L", 5499, 7499, "Rain cover, ventilated back"),
        ("Two-Person Tent", 8999, 11999, "3-season, 2.4 kg packed"),
    ],
    "Fiction": [
        ("The Salt Road", 499, 699, "Award-winning literary fiction"),
        ("Nightfall Harbour", 449, 599, "A slow-burn mystery"),
    ],
    "Technology": [
        ("Designing Data Systems", 899, 1199, "Practical distributed systems"),
        ("Clean Python", 749, 999, "Idiomatic, maintainable Python"),
    ],
}

REVIEW_SNIPPETS = [
    ("Exactly as described", "Arrived quickly and matches the photos. Very happy with the quality."),
    ("Great value", "Does everything I needed at a fair price. Would buy again."),
    ("Solid, with one niggle", "Really well made overall. Packaging could be better."),
    ("Better than expected", "I was unsure at this price but the build quality won me over."),
    ("Recommended", "Second one I have bought. Consistent quality both times."),
    ("Good but sizing runs small", "Order a size up. Otherwise excellent."),
    ("Works perfectly", "Set up in minutes and has been reliable since."),
    ("Would buy again", "Delivery was fast and the item was well packed."),
]

FIRST_NAMES = [
    "Aarav", "Diya", "Rohan", "Meera", "Kabir", "Ananya", "Vikram", "Priya",
    "Ishaan", "Sara", "Arjun", "Nisha", "Dev", "Tara", "Ravi", "Leela",
]
LAST_NAMES = [
    "Sharma", "Iyer", "Menon", "Kapoor", "Reddy", "Nair", "Patel", "Bose",
    "Chopra", "Rao", "Gupta", "Mehta",
]

CITIES = [
    ("Bengaluru", "Karnataka", "560001"),
    ("Mumbai", "Maharashtra", "400001"),
    ("Chennai", "Tamil Nadu", "600001"),
    ("Delhi", "Delhi", "110001"),
    ("Pune", "Maharashtra", "411001"),
    ("Hyderabad", "Telangana", "500001"),
]


class Command(BaseCommand):
    help = "Seed the database with a demo catalog, customers, orders and reviews."

    def add_arguments(self, parser):
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete existing demo data (catalog, orders, customers) first.",
        )
        parser.add_argument(
            "--customers", type=int, default=14, help="Number of demo customers."
        )
        parser.add_argument(
            "--orders", type=int, default=45, help="Number of demo orders."
        )
        parser.add_argument(
            "--no-images",
            action="store_true",
            help="Skip generating placeholder imagery (much faster).",
        )
        parser.add_argument(
            "--admin-email", default="admin@lumenstore.test", help="Dev admin email."
        )
        parser.add_argument(
            "--admin-password", default="admin12345", help="Dev admin password."
        )

    @transaction.atomic
    def handle(self, *args, **options):
        random.seed(20260825)  # deterministic runs
        self.with_images = not options["no_images"]
        self.stdout.write(self.style.MIGRATE_HEADING("Seeding demo data"))

        if options["flush"]:
            self._flush()

        admin = self._create_admin(options["admin_email"], options["admin_password"])
        categories = self._create_categories()
        brands = self._create_brands()
        products = self._create_products(categories, brands)
        self._create_coupons(categories)
        self._create_marketing(products)
        customers = self._create_customers(options["customers"])
        orders = self._create_orders(customers, products, options["orders"])
        self._create_reviews(orders)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Demo data ready."))
        self.stdout.write(f"  Admin login : {options['admin_email']} / {options['admin_password']}")
        self.stdout.write(f"  Customer    : {customers[0].email} / customer12345")
        self.stdout.write(f"  Products    : {len(products)}")
        self.stdout.write(f"  Orders      : {len(orders)}")
        self.stdout.write("")
        self.stdout.write("  Storefront  : http://127.0.0.1:8000/")
        self.stdout.write("  Dashboard   : http://127.0.0.1:8000/staff/")
        self.stdout.write("  Django admin: http://127.0.0.1:8000/admin/")
        self.stdout.write("  API docs    : http://127.0.0.1:8000/api/docs/")

    # -- flush ------------------------------------------------------------
    def _flush(self):
        self.stdout.write("  Removing existing demo data...")
        OrderStatusHistory.objects.all().delete()
        OrderItem.objects.all().delete()
        Order.objects.all().delete()
        Review.objects.all().delete()
        FlashSaleItem.objects.all().delete()
        FlashSale.objects.all().delete()
        Banner.objects.all().delete()
        Offer.objects.all().delete()
        Coupon.objects.all().delete()
        Inventory.objects.all().delete()
        ProductVariant.objects.all().delete()
        Product.objects.all().delete()
        Brand.objects.all().delete()
        Category.objects.all().delete()
        User.objects.filter(is_superuser=False).delete()

    # -- users ------------------------------------------------------------
    def _create_admin(self, email, password):
        admin = User.objects.filter(email__iexact=email).first()
        if admin is None:
            admin = User.objects.create_superuser(
                username=email,
                email=email,
                password=password,
                first_name="Store",
                last_name="Admin",
            )
            self.stdout.write(f"  Created superuser {email}")
        else:
            self.stdout.write(f"  Superuser {email} already exists")
        return admin

    def _create_customers(self, count):
        from apps.accounts.models import Address

        customers = []
        for index in range(count):
            first = FIRST_NAMES[index % len(FIRST_NAMES)]
            last = LAST_NAMES[index % len(LAST_NAMES)]
            email = f"{first.lower()}.{last.lower()}{index}@example.com"

            user = User.objects.filter(email=email).first()
            if user is None:
                user = User.objects.create_user(
                    username=email,
                    email=email,
                    password="customer12345",
                    first_name=first,
                    last_name=last,
                    phone=f"9{random.randint(100000000, 999999999)}",
                    email_verified=True,
                )
                # Spread signups across the last 120 days so the customer
                # chart has a real shape.
                User.objects.filter(pk=user.pk).update(
                    created_at=timezone.now() - timedelta(days=random.randint(0, 120)),
                    date_joined=timezone.now() - timedelta(days=random.randint(0, 120)),
                )

            if not user.addresses.exists():
                city, state, pincode = random.choice(CITIES)
                Address.objects.create(
                    user=user,
                    full_name=f"{first} {last}",
                    phone=user.phone or "9876543210",
                    line1=f"{random.randint(1, 120)}, {random.choice(['Lake View', 'Park Road', 'MG Road', 'Green Avenue'])}",
                    line2=random.choice(["", "Apartment 4B", "Block C"]),
                    city=city,
                    state=state,
                    postal_code=pincode,
                    is_default=True,
                )
            customers.append(user)

        self.stdout.write(f"  Customers: {len(customers)}")
        return customers

    # -- catalog ----------------------------------------------------------
    def _create_categories(self):
        created = {}
        for order, (name, icon, children) in enumerate(CATEGORIES):
            parent, _ = Category.objects.get_or_create(
                name=name,
                parent=None,
                defaults={
                    "slug": slugify(name),
                    "icon_class": icon,
                    "is_featured": True,
                    "sort_order": order,
                    "description": f"Everything in {name.lower()}, curated and in stock.",
                },
            )
            if self.with_images and not parent.image:
                parent.image.save(
                    f"{parent.slug}.jpg", image_factory.category_image(name), save=True
                )
            created[name] = parent
            for child_order, (child_name, child_icon) in enumerate(children):
                child, _ = Category.objects.get_or_create(
                    name=child_name,
                    parent=parent,
                    defaults={
                        "slug": slugify(f"{name}-{child_name}"),
                        "icon_class": child_icon,
                        "sort_order": child_order,
                    },
                )
                created[child_name] = child

        self.stdout.write(f"  Categories: {len(created)}")
        return created

    def _create_brands(self):
        brands = []
        for index, name in enumerate(BRANDS):
            brand, _ = Brand.objects.get_or_create(
                name=name,
                defaults={
                    "slug": slugify(name),
                    "is_featured": index < 4,
                    "description": f"{name} builds dependable products with a long warranty.",
                },
            )
            if self.with_images and not brand.logo:
                brand.logo.save(f"{brand.slug}.jpg", image_factory.brand_logo(name), save=True)
            brands.append(brand)
        self.stdout.write(f"  Brands: {len(brands)}")
        return brands

    def _create_products(self, categories, brands):
        products = []
        sku_counter = 1000

        for category_name, templates in PRODUCT_TEMPLATES.items():
            category = categories.get(category_name)
            if category is None:
                continue

            for name, price, mrp, blurb in templates:
                sku_counter += 1
                sku = f"SKU-{sku_counter}"
                if Product.objects.filter(sku=sku).exists():
                    products.append(Product.objects.get(sku=sku))
                    continue

                brand = random.choice(brands)
                published_at = timezone.now() - timedelta(days=random.randint(1, 150))

                product = Product.objects.create(
                    name=name,
                    slug=slugify(f"{name}-{sku_counter}"),
                    sku=sku,
                    category=category,
                    brand=brand,
                    short_description=blurb,
                    description=(
                        f"{name} from {brand.name}. {blurb}.\n\n"
                        "Designed for everyday use, tested for durability, and backed by our "
                        "standard warranty. Ships in recyclable packaging."
                    ),
                    specifications={
                        "Brand": brand.name,
                        "Category": category.name,
                        "Warranty": "1 year manufacturer warranty",
                        "Country of origin": "India",
                    },
                    price=Decimal(price),
                    compare_at_price=Decimal(mrp),
                    cost_price=Decimal(price) * Decimal("0.62"),
                    status=Product.Status.PUBLISHED,
                    is_active=True,
                    is_featured=random.random() < 0.28,
                    is_best_seller=random.random() < 0.22,
                    is_cod_available=price < 50000,
                    warranty="1 year warranty",
                    tags=", ".join(
                        random.sample(
                            [category.name, brand.name, "new", "popular", "gift", "value"], 3
                        )
                    ),
                    weight_grams=random.randint(200, 4000),
                    published_at=published_at,
                )
                Product.objects.filter(pk=product.pk).update(created_at=published_at)

                self._create_variants(product, category_name)
                self._create_images(product, brand.name)
                products.append(product)

        self.stdout.write(f"  Products: {len(products)}")
        return products

    def _create_images(self, product, brand_name):
        """Give each product a small gallery so grids and galleries look real."""
        if not self.with_images or product.images.exists():
            return
        files = image_factory.gallery_for(product.name, count=3, subtitle=brand_name)
        for index, file in enumerate(files):
            ProductImage.objects.create(
                product=product,
                image=file,
                alt_text=f"{product.name} - view {index + 1}",
                is_primary=index == 0,
                sort_order=index,
            )

    def _create_variants(self, product, category_name):
        """Give clothing/footwear real size+colour variants, others a default."""
        if category_name in {"Men's clothing", "Women's clothing"}:
            sizes = SIZES_CLOTHING
            colors = random.sample(COLORS, 3)
        elif category_name == "Footwear":
            sizes = SIZES_FOOTWEAR
            colors = random.sample(COLORS, 2)
        else:
            sizes = [""]
            colors = random.sample(COLORS, 2) if random.random() < 0.4 else [("", "")]

        index = 0
        for color_name, color_hex in colors:
            for size in sizes:
                index += 1
                variant = ProductVariant.objects.create(
                    product=product,
                    sku=f"{product.sku}-{index:02d}",
                    size=size,
                    color=color_name,
                    color_hex=color_hex,
                    is_default=index == 1,
                    sort_order=index,
                )
                # The catalog signal creates the inventory row; set real levels.
                inventory = Inventory.objects.get(variant=variant)
                roll = random.random()
                if roll < 0.08:
                    quantity = 0                      # some genuinely out of stock
                elif roll < 0.22:
                    quantity = random.randint(1, 4)   # some low, to exercise alerts
                else:
                    quantity = random.randint(12, 140)
                inventory.quantity_available = quantity
                inventory.warehouse_location = random.choice(["BLR-A1", "BOM-B2", "DEL-C3"])
                inventory.save(update_fields=["quantity_available", "warehouse_location"])

    # -- coupons ----------------------------------------------------------
    def _create_coupons(self, categories):
        now = timezone.now()
        definitions = [
            {
                "code": "WELCOME10",
                "description": "10% off your first order",
                "discount_type": Coupon.DiscountType.PERCENTAGE,
                "value": Decimal("10"),
                "max_discount_amount": Decimal("1500"),
                "min_order_value": Decimal("999"),
                "first_order_only": True,
                "usage_limit_per_user": 1,
                "valid_to": now + timedelta(days=180),
            },
            {
                "code": "SAVE500",
                "description": "Flat 500 off orders over 4999",
                "discount_type": Coupon.DiscountType.FIXED,
                "value": Decimal("500"),
                "min_order_value": Decimal("4999"),
                "usage_limit": 500,
                "usage_limit_per_user": 3,
                "valid_to": now + timedelta(days=60),
            },
            {
                "code": "FREESHIP",
                "description": "Free delivery, any order value",
                "discount_type": Coupon.DiscountType.FREE_SHIPPING,
                "value": Decimal("0"),
                "min_order_value": Decimal("0"),
                "usage_limit_per_user": 5,
                "valid_to": now + timedelta(days=90),
            },
            {
                "code": "TECH15",
                "description": "15% off electronics",
                "discount_type": Coupon.DiscountType.PERCENTAGE,
                "value": Decimal("15"),
                "max_discount_amount": Decimal("5000"),
                "min_order_value": Decimal("9999"),
                "usage_limit_per_user": 2,
                "valid_to": now + timedelta(days=30),
            },
            {
                "code": "EXPIRED20",
                "description": "Expired campaign (kept to test validation)",
                "discount_type": Coupon.DiscountType.PERCENTAGE,
                "value": Decimal("20"),
                "min_order_value": Decimal("0"),
                "is_public": False,
                "valid_to": now - timedelta(days=5),
            },
        ]

        for definition in definitions:
            code = definition.pop("code")
            coupon, created = Coupon.objects.get_or_create(code=code, defaults=definition)
            if created and code == "TECH15":
                electronics = categories.get("Electronics")
                if electronics:
                    coupon.applicable_categories.add(electronics)

        self.stdout.write(f"  Coupons: {Coupon.objects.count()}")

    # -- marketing --------------------------------------------------------
    def _create_marketing(self, products):
        now = timezone.now()

        banners = [
            ("Fresh tech, fair prices", "Up to 25% off laptops and audio", Banner.Position.HERO, 0),
            ("The wardrobe refresh", "New season styles just landed", Banner.Position.HERO, 1),
            ("Home upgrades", "Cookware and appliances worth keeping", Banner.Position.HERO, 2),
        ]
        for title, subtitle, position, order in banners:
            banner, created = Banner.objects.get_or_create(
                title=title,
                defaults={
                    "subtitle": subtitle,
                    "position": position,
                    "sort_order": order,
                    "link_url": "/products/",
                    "cta_label": "Shop now",
                    "start_at": now - timedelta(days=1),
                    "end_at": now + timedelta(days=60),
                },
            )
            if self.with_images and not banner.image:
                banner.image.save(
                    f"banner-{order}.jpg",
                    image_factory.banner_image(title, subtitle),
                    save=True,
                )

        offers = [
            ("First order? Take 10% off", "Use code WELCOME10 at checkout", "NEW CUSTOMER", "WELCOME10"),
            ("Free delivery this week", "No minimum order value", "LIMITED", "FREESHIP"),
            ("Electronics sale", "15% off, capped at 5,000", "SAVE BIG", "TECH15"),
        ]
        for title, description, badge, code in offers:
            Offer.objects.get_or_create(
                title=title,
                defaults={
                    "description": description,
                    "badge_text": badge,
                    "coupon": Coupon.objects.filter(code=code).first(),
                    "link_url": "/products/",
                    "start_at": now - timedelta(days=1),
                    "end_at": now + timedelta(days=45),
                },
            )

        # A flash sale that is genuinely running right now.
        sale, created = FlashSale.objects.get_or_create(
            name="48-hour flash sale",
            defaults={
                "description": "Hand-picked deals, while stock lasts",
                "start_at": now - timedelta(hours=6),
                "end_at": now + timedelta(hours=42),
            },
        )
        if created:
            in_stock = [
                p for p in products if p.variants.filter(inventory__quantity_available__gt=5).exists()
            ]
            for index, product in enumerate(random.sample(in_stock, min(8, len(in_stock)))):
                variant = product.variants.first()
                if variant is None:
                    continue
                FlashSaleItem.objects.create(
                    flash_sale=sale,
                    variant=variant,
                    sale_price=money(variant.price * Decimal("0.72")),
                    quantity_limit=random.choice([20, 40, 60]),
                    sold_count=random.randint(2, 18),
                    sort_order=index,
                )

        self.stdout.write("  Marketing: banners, offers and a live flash sale")

    # -- orders -----------------------------------------------------------
    def _create_orders(self, customers, products, count):
        """Create orders spread over 120 days across every status.

        Written directly (rather than through place_order) so historical dates
        and finished statuses can be backdated for the charts and reports.
        """
        sellable = []
        for product in products:
            for variant in product.variants.select_related("inventory"):
                if variant.available_quantity > 0:
                    sellable.append(variant)
        if not sellable:
            return []

        status_weights = [
            (Order.Status.DELIVERED, 46),
            (Order.Status.SHIPPED, 12),
            (Order.Status.PROCESSING, 10),
            (Order.Status.CONFIRMED, 10),
            (Order.Status.PENDING, 8),
            (Order.Status.CANCELLED, 8),
            (Order.Status.RETURNED, 6),
        ]
        statuses = [s for s, weight in status_weights for _ in range(weight)]

        orders = []
        for _ in range(count):
            customer = random.choice(customers)
            address = customer.default_address()
            if address is None:
                continue

            placed_at = timezone.now() - timedelta(
                days=random.randint(0, 120), hours=random.randint(0, 23)
            )
            status = random.choice(statuses)
            payment_method = random.choice(
                [
                    Order.PaymentMethod.UPI,
                    Order.PaymentMethod.CARD,
                    Order.PaymentMethod.COD,
                    Order.PaymentMethod.NETBANKING,
                ]
            )

            snapshot = address.as_snapshot()
            order = Order.objects.create(
                user=customer,
                email=customer.email,
                phone=customer.phone,
                status=status,
                payment_method=payment_method,
                payment_status=(
                    Order.PaymentStatus.PAID
                    if status in {Order.Status.DELIVERED, Order.Status.SHIPPED, Order.Status.PROCESSING}
                    else Order.PaymentStatus.PENDING
                ),
                currency="INR",
                placed_at=placed_at,
                shipping_full_name=snapshot["full_name"],
                shipping_phone=snapshot["phone"],
                shipping_line1=snapshot["line1"],
                shipping_line2=snapshot["line2"],
                shipping_city=snapshot["city"],
                shipping_state=snapshot["state"],
                shipping_postal_code=snapshot["postal_code"],
                estimated_delivery=(placed_at + timedelta(days=5)).date(),
            )
            Order.objects.filter(pk=order.pk).update(created_at=placed_at)

            # ---- lines ----
            for variant in random.sample(sellable, random.randint(1, 3)):
                quantity = random.randint(1, 3)
                primary = variant.product.primary_image
                OrderItem.objects.create(
                    order=order,
                    variant=variant,
                    product=variant.product,
                    product_name=variant.product.name,
                    variant_label=variant.label,
                    sku=variant.sku,
                    image_url=primary.image.url if primary else "",
                    unit_price=variant.price,
                    unit_mrp=variant.compare_at_price or variant.price,
                    quantity=quantity,
                    is_returnable=variant.product.is_returnable,
                )

            # ---- totals, computed the same way checkout does ----
            order.recalculate_totals(save=False)
            if random.random() < 0.3:
                coupon = Coupon.objects.filter(code="SAVE500").first()
                if coupon and order.subtotal >= coupon.min_order_value:
                    order.coupon = coupon
                    order.coupon_code = coupon.code
                    order.coupon_discount = coupon.discount_for(order.subtotal)

            discounted = max(order.subtotal - order.coupon_discount, Decimal("0.00"))
            order.delivery_charge = (
                Decimal("0.00") if discounted >= Decimal("999") else Decimal("49.00")
            )
            order.recalculate_totals(save=False)
            order.total_amount = money(discounted + order.delivery_charge + order.tax_amount)

            # ---- status timestamps ----
            if status in {
                Order.Status.CONFIRMED, Order.Status.PROCESSING, Order.Status.SHIPPED,
                Order.Status.DELIVERED, Order.Status.RETURNED,
            }:
                order.confirmed_at = placed_at + timedelta(hours=2)
            if status in {Order.Status.SHIPPED, Order.Status.DELIVERED, Order.Status.RETURNED}:
                order.shipped_at = placed_at + timedelta(days=1)
                order.tracking_number = f"TRK{random.randint(10000000, 99999999)}"
                order.courier_name = random.choice(["BlueDart", "Delhivery", "EcomExpress"])
            if status in {Order.Status.DELIVERED, Order.Status.RETURNED}:
                order.delivered_at = placed_at + timedelta(days=random.randint(2, 6))
                order.payment_status = Order.PaymentStatus.PAID
            if status == Order.Status.CANCELLED:
                order.cancelled_at = placed_at + timedelta(hours=random.randint(1, 20))
                order.cancel_reason = random.choice(
                    ["Changed my mind", "Ordered by mistake", "Found a better price"]
                )
            if status == Order.Status.RETURNED:
                order.returned_at = order.delivered_at + timedelta(days=2)
                order.payment_status = Order.PaymentStatus.REFUND_PENDING

            order.save()

            # Reflect completed sales in stock and product counters.
            if status in {Order.Status.DELIVERED, Order.Status.SHIPPED, Order.Status.PROCESSING}:
                for item in order.items.select_related("variant__inventory"):
                    inventory = getattr(item.variant, "inventory", None)
                    if inventory:
                        inventory.quantity_available = max(
                            inventory.quantity_available - item.quantity, 0
                        )
                        inventory.quantity_sold += item.quantity
                        inventory.save(update_fields=["quantity_available", "quantity_sold"])
                    Product.objects.filter(pk=item.product_id).update(
                        sold_count=Product.objects.get(pk=item.product_id).sold_count + item.quantity
                    )

            OrderStatusHistory.objects.filter(order=order).update(created_at=placed_at)
            orders.append(order)

        self.stdout.write(f"  Orders: {len(orders)}")
        return orders

    # -- reviews ----------------------------------------------------------
    def _create_reviews(self, orders):
        created = 0
        for order in orders:
            if order.status != Order.Status.DELIVERED:
                continue
            for item in order.items.select_related("product"):
                if item.product is None or random.random() > 0.55:
                    continue
                if Review.objects.filter(product=item.product, user=order.user).exists():
                    continue

                title, comment = random.choice(REVIEW_SNIPPETS)
                rating = random.choices([5, 4, 3, 2, 1], weights=[48, 30, 12, 6, 4])[0]
                review = Review.objects.create(
                    product=item.product,
                    user=order.user,
                    order_item=item,
                    rating=rating,
                    title=title,
                    comment=comment,
                    verified_purchase=True,
                    helpful_count=random.randint(0, 24),
                )
                Review.objects.filter(pk=review.pk).update(
                    created_at=order.delivered_at + timedelta(days=random.randint(1, 10))
                )
                item.is_reviewed = True
                item.save(update_fields=["is_reviewed"])
                created += 1

        self.stdout.write(f"  Reviews: {created}")
