"""Serializers for the REST API.

Read serializers expose the same server-computed figures the templates use
(``variant.price``, ``cart.total``); write serializers accept only the inputs
a client is allowed to choose. No serializer ever accepts a price, a discount
or an order total.
"""
from django.contrib.auth import get_user_model, password_validation
from django.db import transaction
from drf_spectacular.utils import OpenApiTypes, extend_schema_field
from rest_framework import serializers

from apps.accounts.models import Address
from apps.cart.models import Cart, CartItem
from apps.catalog.models import Brand, Category, Product, ProductImage, ProductVariant
from apps.coupons.models import Coupon
from apps.marketing.models import Banner, FlashSale, FlashSaleItem, Offer
from apps.orders.models import Order, OrderItem, OrderStatusHistory, ReturnRequest
from apps.payments.models import Payment
from apps.reviews.models import Review, ReviewImage
from apps.wishlist.models import WishlistItem

User = get_user_model()


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------
class RegisterSerializer(serializers.ModelSerializer):
    """POST /api/auth/register/"""

    password = serializers.CharField(write_only=True, style={"input_type": "password"})
    password_confirm = serializers.CharField(write_only=True, style={"input_type": "password"})

    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "first_name",
            "last_name",
            "phone",
            "password",
            "password_confirm",
        )
        read_only_fields = ("id",)

    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def validate(self, attrs):
        if attrs["password"] != attrs.pop("password_confirm"):
            raise serializers.ValidationError({"password_confirm": "Passwords do not match."})
        # Reuse Django's configured validators -- one policy, two front doors.
        password_validation.validate_password(attrs["password"])
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password")
        return User.objects.create_user(
            username=validated_data["email"], password=password, **validated_data
        )


class UserSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source="get_display_name", read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "first_name",
            "last_name",
            "full_name",
            "phone",
            "gender",
            "date_of_birth",
            "avatar",
            "email_verified",
            "marketing_opt_in",
            "date_joined",
        )
        read_only_fields = ("id", "email", "email_verified", "date_joined")


class CustomerListSerializer(serializers.ModelSerializer):
    """GET /api/customers/ -- staff view with order aggregates."""

    full_name = serializers.CharField(source="get_display_name", read_only=True)
    order_count = serializers.IntegerField(read_only=True)
    lifetime_value = serializers.DecimalField(
        max_digits=14, decimal_places=2, read_only=True
    )

    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "full_name",
            "phone",
            "is_active",
            "order_count",
            "lifetime_value",
            "date_joined",
            "last_login",
        )


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)

    def validate_old_password(self, value):
        if not self.context["request"].user.check_password(value):
            raise serializers.ValidationError("Your current password is incorrect.")
        return value

    def validate_new_password(self, value):
        password_validation.validate_password(value, self.context["request"].user)
        return value

    def save(self, **kwargs):
        user = self.context["request"].user
        user.set_password(self.validated_data["new_password"])
        user.save(update_fields=["password"])
        return user


class AddressSerializer(serializers.ModelSerializer):
    single_line = serializers.CharField(read_only=True)

    class Meta:
        model = Address
        fields = (
            "id",
            "label",
            "full_name",
            "phone",
            "line1",
            "line2",
            "landmark",
            "city",
            "state",
            "country",
            "postal_code",
            "is_default",
            "single_line",
            "created_at",
        )
        read_only_fields = ("id", "created_at", "single_line")


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------
class CategorySerializer(serializers.ModelSerializer):
    children = serializers.SerializerMethodField()
    product_count = serializers.IntegerField(read_only=True, required=False)
    full_path = serializers.CharField(read_only=True)

    class Meta:
        model = Category
        fields = (
            "id",
            "name",
            "slug",
            "parent",
            "full_path",
            "description",
            "image",
            "icon_class",
            "is_active",
            "is_featured",
            "sort_order",
            "product_count",
            "children",
        )
        read_only_fields = ("id", "slug", "full_path")

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_children(self, obj):
        """One level of children only -- deeper nesting is fetched on demand."""
        if self.context.get("nested"):
            return []
        children = [c for c in obj.children.all() if c.is_active]
        return CategorySerializer(children, many=True, context={**self.context, "nested": True}).data


class BrandSerializer(serializers.ModelSerializer):
    product_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = Brand
        fields = ("id", "name", "slug", "description", "logo", "website", "is_active", "product_count")
        read_only_fields = ("id", "slug")


class ProductImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImage
        fields = ("id", "image", "alt_text", "is_primary", "sort_order")


class ProductVariantSerializer(serializers.ModelSerializer):
    """Prices and stock are read-only properties computed on the server."""

    label = serializers.CharField(read_only=True)
    price = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    compare_at_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    discount_percent = serializers.IntegerField(read_only=True)
    available_quantity = serializers.IntegerField(read_only=True)
    in_stock = serializers.BooleanField(read_only=True)
    is_low_stock = serializers.BooleanField(read_only=True)

    class Meta:
        model = ProductVariant
        fields = (
            "id",
            "sku",
            "label",
            "size",
            "color",
            "color_hex",
            "extra_attributes",
            "price",
            "compare_at_price",
            "discount_percent",
            "price_override",
            "compare_at_price_override",
            "image",
            "is_active",
            "sort_order",
            "available_quantity",
            "in_stock",
            "is_low_stock",
        )
        extra_kwargs = {
            "price_override": {"write_only": True, "required": False},
            "compare_at_price_override": {"write_only": True, "required": False},
        }


class ProductListSerializer(serializers.ModelSerializer):
    """Compact shape for grids and search results."""

    category_name = serializers.CharField(source="category.name", read_only=True)
    category_slug = serializers.CharField(source="category.slug", read_only=True)
    brand_name = serializers.CharField(source="brand.name", read_only=True, default=None)
    primary_image = serializers.SerializerMethodField()
    discount_percent = serializers.IntegerField(read_only=True)
    in_stock = serializers.BooleanField(read_only=True)
    default_variant_id = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = (
            "id",
            "name",
            "slug",
            "sku",
            "short_description",
            "category_name",
            "category_slug",
            "brand_name",
            "price",
            "compare_at_price",
            "discount_percent",
            "rating_average",
            "rating_count",
            "sold_count",
            "is_featured",
            "is_best_seller",
            "in_stock",
            "primary_image",
            "default_variant_id",
            "created_at",
        )

    @extend_schema_field(OpenApiTypes.URI)
    def get_primary_image(self, obj):
        image = obj.primary_image
        if not image:
            return None
        request = self.context.get("request")
        url = image.image.url
        return request.build_absolute_uri(url) if request else url

    @extend_schema_field(OpenApiTypes.INT)
    def get_default_variant_id(self, obj):
        variant = obj.default_variant
        return variant.pk if variant else None


class ProductDetailSerializer(ProductListSerializer):
    """Full product payload, including images and every variant."""

    images = ProductImageSerializer(many=True, read_only=True)
    variants = ProductVariantSerializer(many=True, read_only=True)
    category = CategorySerializer(read_only=True)
    brand = BrandSerializer(read_only=True)
    total_stock = serializers.IntegerField(read_only=True)

    class Meta(ProductListSerializer.Meta):
        fields = ProductListSerializer.Meta.fields + (
            "description",
            "specifications",
            "tags",
            "warranty",
            "weight_grams",
            "is_returnable",
            "is_cod_available",
            "status",
            "images",
            "variants",
            "category",
            "brand",
            "total_stock",
            "view_count",
        )


class ProductWriteSerializer(serializers.ModelSerializer):
    """Staff-only create/update. Denormalised counters stay read-only."""

    class Meta:
        model = Product
        fields = (
            "id",
            "name",
            "slug",
            "sku",
            "category",
            "brand",
            "short_description",
            "description",
            "specifications",
            "tags",
            "price",
            "compare_at_price",
            "cost_price",
            "tax_rate_percent",
            "status",
            "is_active",
            "is_featured",
            "is_best_seller",
            "is_returnable",
            "is_cod_available",
            "weight_grams",
            "warranty",
            "meta_title",
            "meta_description",
        )
        read_only_fields = ("id",)
        extra_kwargs = {"slug": {"required": False}}

    def validate(self, attrs):
        price = attrs.get("price", getattr(self.instance, "price", None))
        compare = attrs.get(
            "compare_at_price", getattr(self.instance, "compare_at_price", None)
        )
        if price is not None and compare is not None and compare < price:
            raise serializers.ValidationError(
                {"compare_at_price": "The original price cannot be lower than the selling price."}
            )
        return attrs


# ---------------------------------------------------------------------------
# Cart
# ---------------------------------------------------------------------------
class CartItemSerializer(serializers.ModelSerializer):
    variant = ProductVariantSerializer(read_only=True)
    product_name = serializers.CharField(source="variant.product.name", read_only=True)
    product_slug = serializers.CharField(source="variant.product.slug", read_only=True)
    image = serializers.SerializerMethodField()
    unit_price = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    line_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    line_savings = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    max_selectable = serializers.IntegerField(read_only=True)
    has_stock_issue = serializers.BooleanField(read_only=True)

    class Meta:
        model = CartItem
        fields = (
            "id",
            "variant",
            "product_name",
            "product_slug",
            "image",
            "quantity",
            "unit_price",
            "line_total",
            "line_savings",
            "max_selectable",
            "has_stock_issue",
        )

    @extend_schema_field(OpenApiTypes.URI)
    def get_image(self, obj):
        request = self.context.get("request")
        variant = obj.variant
        url = ""
        if variant.image:
            url = variant.image.url
        else:
            primary = variant.product.primary_image
            if primary:
                url = primary.image.url
        if not url:
            return None
        return request.build_absolute_uri(url) if request else url


class CartSerializer(serializers.ModelSerializer):
    """Totals come straight from the model -- never from the client."""

    items = serializers.SerializerMethodField()
    summary = serializers.SerializerMethodField()

    class Meta:
        model = Cart
        fields = ("id", "coupon", "items", "summary", "updated_at")
        read_only_fields = fields

    @extend_schema_field(CartItemSerializer(many=True))
    def get_items(self, obj):
        return CartItemSerializer(obj.live_items(), many=True, context=self.context).data

    @extend_schema_field(serializers.DictField())
    def get_summary(self, obj):
        summary = obj.as_summary()
        return {
            key: (str(value) if hasattr(value, "quantize") else value)
            for key, value in summary.items()
        }


class AddToCartSerializer(serializers.Serializer):
    """POST /api/cart/items/ -- a variant id and a quantity, nothing else."""

    variant_id = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1, default=1)

    def validate_variant_id(self, value):
        variant = (
            ProductVariant.objects.filter(pk=value, is_active=True, product__is_active=True)
            .select_related("product", "inventory")
            .first()
        )
        if variant is None:
            raise serializers.ValidationError("That product variant is not available.")
        self.variant = variant
        return value


class UpdateCartItemSerializer(serializers.Serializer):
    quantity = serializers.IntegerField(min_value=0)


class ApplyCouponSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=32)


# ---------------------------------------------------------------------------
# Wishlist
# ---------------------------------------------------------------------------
class WishlistItemSerializer(serializers.ModelSerializer):
    product = ProductListSerializer(read_only=True)
    in_stock = serializers.BooleanField(read_only=True)

    class Meta:
        model = WishlistItem
        fields = ("id", "product", "variant", "note", "in_stock", "created_at")
        read_only_fields = ("id", "created_at", "in_stock")


class AddToWishlistSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    variant_id = serializers.IntegerField(required=False, allow_null=True)

    def validate_product_id(self, value):
        product = Product.objects.published().filter(pk=value).first()
        if product is None:
            raise serializers.ValidationError("That product is not available.")
        self.product = product
        return value


# ---------------------------------------------------------------------------
# Coupons
# ---------------------------------------------------------------------------
class CouponSerializer(serializers.ModelSerializer):
    discount_label = serializers.CharField(read_only=True)
    is_expired = serializers.BooleanField(read_only=True)
    remaining_uses = serializers.IntegerField(read_only=True)

    class Meta:
        model = Coupon
        fields = (
            "id",
            "code",
            "description",
            "discount_type",
            "value",
            "max_discount_amount",
            "min_order_value",
            "valid_from",
            "valid_to",
            "usage_limit",
            "usage_limit_per_user",
            "used_count",
            "is_active",
            "is_public",
            "first_order_only",
            "applicable_categories",
            "applicable_products",
            "discount_label",
            "is_expired",
            "remaining_uses",
        )
        read_only_fields = ("id", "used_count", "discount_label", "is_expired", "remaining_uses")


class PublicCouponSerializer(serializers.ModelSerializer):
    """What an anonymous shopper may see: the offer, not the internals."""

    discount_label = serializers.CharField(read_only=True)

    class Meta:
        model = Coupon
        fields = ("code", "description", "discount_label", "min_order_value", "valid_to")


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------
class OrderItemSerializer(serializers.ModelSerializer):
    display_title = serializers.CharField(read_only=True)
    line_savings = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    product_slug = serializers.CharField(source="product.slug", read_only=True, default=None)

    class Meta:
        model = OrderItem
        fields = (
            "id",
            "product",
            "product_slug",
            "variant",
            "product_name",
            "variant_label",
            "display_title",
            "sku",
            "image_url",
            "unit_price",
            "unit_mrp",
            "quantity",
            "line_total",
            "line_savings",
            "status",
            "is_returnable",
            "is_reviewed",
        )
        read_only_fields = fields


class OrderStatusHistorySerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = OrderStatusHistory
        fields = ("status", "status_display", "note", "created_at")
        read_only_fields = fields


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = (
            "id",
            "gateway",
            "method",
            "amount",
            "currency",
            "status",
            "gateway_payment_id",
            "paid_at",
            "created_at",
        )
        read_only_fields = fields


class OrderListSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    payment_status_display = serializers.CharField(
        source="get_payment_status_display", read_only=True
    )
    item_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Order
        fields = (
            "id",
            "order_number",
            "status",
            "status_display",
            "payment_status",
            "payment_status_display",
            "payment_method",
            "total_amount",
            "item_count",
            "placed_at",
            "estimated_delivery",
            "tracking_number",
        )
        read_only_fields = fields


class OrderDetailSerializer(OrderListSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    status_history = OrderStatusHistorySerializer(many=True, read_only=True)
    payments = PaymentSerializer(many=True, read_only=True)
    shipping_address = serializers.SerializerMethodField()
    can_be_cancelled = serializers.BooleanField(read_only=True)
    can_be_returned = serializers.BooleanField(read_only=True)
    total_savings = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta(OrderListSerializer.Meta):
        fields = OrderListSerializer.Meta.fields + (
            "email",
            "phone",
            "subtotal",
            "product_discount",
            "coupon_code",
            "coupon_discount",
            "delivery_charge",
            "tax_amount",
            "refunded_amount",
            "total_savings",
            "currency",
            "customer_note",
            "cancel_reason",
            "courier_name",
            "confirmed_at",
            "shipped_at",
            "delivered_at",
            "cancelled_at",
            "shipping_address",
            "items",
            "status_history",
            "payments",
            "can_be_cancelled",
            "can_be_returned",
        )
        read_only_fields = fields

    @extend_schema_field(serializers.DictField())
    def get_shipping_address(self, obj):
        return {
            "full_name": obj.shipping_full_name,
            "phone": obj.shipping_phone,
            "line1": obj.shipping_line1,
            "line2": obj.shipping_line2,
            "landmark": obj.shipping_landmark,
            "city": obj.shipping_city,
            "state": obj.shipping_state,
            "country": obj.shipping_country,
            "postal_code": obj.shipping_postal_code,
        }


class CreateOrderSerializer(serializers.Serializer):
    """POST /api/orders/ -- checkout.

    The client picks an address and a payment method. Everything financial is
    recomputed by ``apps.orders.services.place_order``.
    """

    address_id = serializers.IntegerField()
    payment_method = serializers.ChoiceField(choices=Order.PaymentMethod.choices)
    customer_note = serializers.CharField(required=False, allow_blank=True, max_length=500)
    shipping_method = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=32,
        help_text="Delivery method code. Omitted means the cheapest available.",
    )

    def validate_shipping_method(self, value):
        return value or None

    def validate_address_id(self, value):
        user = self.context["request"].user
        address = Address.objects.filter(pk=value, user=user).first()
        if address is None:
            raise serializers.ValidationError("That address does not belong to your account.")
        self.address = address
        return value


class OrderStatusUpdateSerializer(serializers.Serializer):
    """PUT /api/orders/<id>/status/ -- staff only."""

    status = serializers.ChoiceField(choices=Order.Status.choices)
    note = serializers.CharField(required=False, allow_blank=True, max_length=255)
    tracking_number = serializers.CharField(required=False, allow_blank=True, max_length=64)
    courier_name = serializers.CharField(required=False, allow_blank=True, max_length=100)


class CancelOrderSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255)


class ReturnRequestSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source="order.order_number", read_only=True)
    item_title = serializers.CharField(source="order_item.display_title", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = ReturnRequest
        fields = (
            "id",
            "order",
            "order_number",
            "order_item",
            "item_title",
            "quantity",
            "reason",
            "comment",
            "status",
            "status_display",
            "refund_amount",
            "created_at",
        )
        read_only_fields = (
            "id",
            "order",
            "order_number",
            "item_title",
            "status",
            "status_display",
            "refund_amount",
            "created_at",
        )


class CreateReturnSerializer(serializers.Serializer):
    order_item_id = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1)
    reason = serializers.ChoiceField(choices=ReturnRequest.Reason.choices)
    comment = serializers.CharField(required=False, allow_blank=True, max_length=1000)

    def validate_order_item_id(self, value):
        user = self.context["request"].user
        item = (
            OrderItem.objects.filter(pk=value, order__user=user)
            .select_related("order")
            .first()
        )
        if item is None:
            raise serializers.ValidationError("That order item does not belong to your account.")
        self.order_item = item
        return value


# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------
class ReviewImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReviewImage
        fields = ("id", "image", "caption")


class ReviewSerializer(serializers.ModelSerializer):
    author_name = serializers.CharField(read_only=True)
    images = ReviewImageSerializer(many=True, read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = Review
        fields = (
            "id",
            "product",
            "product_name",
            "rating",
            "title",
            "comment",
            "author_name",
            "verified_purchase",
            "helpful_count",
            "staff_reply",
            "images",
            "created_at",
        )
        read_only_fields = (
            "id",
            "product_name",
            "author_name",
            "verified_purchase",
            "helpful_count",
            "staff_reply",
            "images",
            "created_at",
        )

    def validate(self, attrs):
        """Eligibility is checked here so the API cannot bypass the rule."""
        from apps.reviews.services import can_review_product

        request = self.context["request"]
        product = attrs.get("product") or getattr(self.instance, "product", None)
        if self.instance is None:
            allowed, reason = can_review_product(request.user, product)
            if not allowed:
                raise serializers.ValidationError({"product": str(reason)})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        from apps.reviews.services import create_review

        request = self.context["request"]
        return create_review(
            user=request.user,
            product=validated_data["product"],
            rating=validated_data["rating"],
            title=validated_data.get("title", ""),
            comment=validated_data.get("comment", ""),
        )


# ---------------------------------------------------------------------------
# Marketing
# ---------------------------------------------------------------------------
class BannerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Banner
        fields = (
            "id",
            "title",
            "subtitle",
            "image",
            "mobile_image",
            "link_url",
            "cta_label",
            "position",
            "background_color",
            "sort_order",
        )


class OfferSerializer(serializers.ModelSerializer):
    coupon_code = serializers.CharField(source="coupon.code", read_only=True, default=None)

    class Meta:
        model = Offer
        fields = (
            "id",
            "title",
            "description",
            "badge_text",
            "image",
            "link_url",
            "coupon_code",
            "start_at",
            "end_at",
        )


class FlashSaleItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="variant.product.name", read_only=True)
    product_slug = serializers.CharField(source="variant.product.slug", read_only=True)
    original_price = serializers.DecimalField(
        source="variant.price", max_digits=12, decimal_places=2, read_only=True
    )
    discount_percent = serializers.IntegerField(read_only=True)
    claimed_percent = serializers.IntegerField(read_only=True)
    is_sold_out = serializers.BooleanField(read_only=True)

    class Meta:
        model = FlashSaleItem
        fields = (
            "id",
            "variant",
            "product_name",
            "product_slug",
            "sale_price",
            "original_price",
            "discount_percent",
            "quantity_limit",
            "sold_count",
            "claimed_percent",
            "is_sold_out",
        )


class FlashSaleSerializer(serializers.ModelSerializer):
    items = FlashSaleItemSerializer(many=True, read_only=True)
    seconds_remaining = serializers.IntegerField(read_only=True)

    class Meta:
        model = FlashSale
        fields = (
            "id",
            "name",
            "description",
            "banner_image",
            "start_at",
            "end_at",
            "seconds_remaining",
            "items",
        )
