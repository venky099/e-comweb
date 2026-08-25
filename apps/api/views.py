"""REST API viewsets.

The API is an additive layer over the same service functions the storefront
uses, so business rules cannot drift between the two.
"""
from django.contrib.auth import get_user_model, login, logout
from django.db.models import Count, Prefetch, Q, Sum
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.accounts.models import Address
from apps.cart.services import CartError, add_to_cart, get_cart
from apps.cart.services import remove_item as cart_remove_item
from apps.cart.services import update_quantity as cart_update_quantity
from apps.catalog.filters import apply_filters, apply_sorting
from apps.catalog.models import Brand, Category, Product, ProductVariant
from apps.catalog.search import search_products
from apps.coupons import services as coupon_services
from apps.coupons.models import Coupon
from apps.dashboard import reports
from apps.marketing.models import Banner, FlashSale, Offer
from apps.orders import services as order_services
from apps.orders.models import Order, ReturnRequest
from apps.reviews.models import Review
from apps.wishlist.models import Wishlist, WishlistItem

from . import serializers as api_serializers
from .permissions import IsOwner, IsOwnerOrReadOnly, IsStaff, IsStaffOrReadOnly

User = get_user_model()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
@extend_schema(tags=["auth"])
class RegisterView(APIView):
    """POST /api/auth/register/ -- create an account and issue JWTs."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "register"
    serializer_class = api_serializers.RegisterSerializer

    @extend_schema(
        request=api_serializers.RegisterSerializer,
        responses={201: api_serializers.UserSerializer},
    )
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "user": api_serializers.UserSerializer(user, context={"request": request}).data,
                "access": str(refresh.access_token),
                "refresh": str(refresh),
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["auth"])
class LoginView(TokenObtainPairView):
    """POST /api/auth/login/ -- obtain a JWT pair.

    Accepts ``username`` as either an email address or a username, matching
    the storefront login form.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request, *args, **kwargs):
        # simplejwt authenticates against USERNAME_FIELD; map an email to it.
        data = request.data.copy()
        identifier = data.get("username") or data.get("email")
        if identifier and "username" not in data:
            data["username"] = identifier
        if identifier and "@" in str(identifier):
            match = User.objects.filter(email__iexact=identifier).first()
            if match:
                data["username"] = match.username
        request._full_data = data

        response = super().post(request, *args, **kwargs)
        if response.status_code == status.HTTP_200_OK:
            user = User.objects.filter(username=data.get("username")).first()
            if user:
                response.data["user"] = api_serializers.UserSerializer(
                    user, context={"request": request}
                ).data
        return response


@extend_schema(tags=["auth"])
class LogoutView(APIView):
    """POST /api/auth/logout/ -- blacklist the refresh token."""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={205: None})
    def post(self, request):
        refresh_token = request.data.get("refresh")
        if refresh_token:
            try:
                RefreshToken(refresh_token).blacklist()
            except Exception:
                return Response(
                    {"detail": "Invalid or already blacklisted refresh token."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        # Session auth users (the browsable API) get signed out too.
        if request.user.is_authenticated and hasattr(request, "session"):
            logout(request)
        return Response(status=status.HTTP_205_RESET_CONTENT)


@extend_schema(tags=["auth"])
class MeView(APIView):
    """GET/PATCH the authenticated user's profile."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=api_serializers.UserSerializer)
    def get(self, request):
        return Response(
            api_serializers.UserSerializer(request.user, context={"request": request}).data
        )

    @extend_schema(
        request=api_serializers.UserSerializer, responses=api_serializers.UserSerializer
    )
    def patch(self, request):
        serializer = api_serializers.UserSerializer(
            request.user, data=request.data, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


@extend_schema(tags=["auth"])
class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=api_serializers.ChangePasswordSerializer, responses={200: None})
    def post(self, request):
        serializer = api_serializers.ChangePasswordSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({"detail": "Password updated."})


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------
@extend_schema(tags=["catalog"])
class CategoryViewSet(viewsets.ModelViewSet):
    """Public read, staff write."""

    serializer_class = api_serializers.CategorySerializer
    permission_classes = [IsStaffOrReadOnly]
    lookup_field = "slug"
    search_fields = ("name", "description")
    ordering_fields = ("sort_order", "name", "created_at")

    def get_queryset(self):
        queryset = Category.objects.prefetch_related("children").annotate(
            product_count=Count("products", filter=Q(products__status=Product.Status.PUBLISHED))
        )
        if not (self.request.user.is_authenticated and self.request.user.is_staff):
            queryset = queryset.filter(is_active=True)
        if self.action == "list" and self.request.query_params.get("roots") == "1":
            queryset = queryset.filter(parent__isnull=True)
        return queryset.order_by("sort_order", "name")

    @extend_schema(responses=api_serializers.ProductListSerializer(many=True))
    @action(detail=True, methods=["get"])
    def products(self, request, slug=None):
        """Products in this category and its subcategories."""
        category = self.get_object()
        queryset = (
            Product.objects.published()
            .with_related()
            .filter(category_id__in=category.descendant_ids())
        )
        page = self.paginate_queryset(queryset)
        serializer = api_serializers.ProductListSerializer(
            page, many=True, context=self.get_serializer_context()
        )
        return self.get_paginated_response(serializer.data)


@extend_schema(tags=["catalog"])
class BrandViewSet(viewsets.ModelViewSet):
    serializer_class = api_serializers.BrandSerializer
    permission_classes = [IsStaffOrReadOnly]
    lookup_field = "slug"
    search_fields = ("name",)
    ordering_fields = ("name",)

    def get_queryset(self):
        queryset = Brand.objects.annotate(
            product_count=Count("products", filter=Q(products__status=Product.Status.PUBLISHED))
        )
        if not (self.request.user.is_authenticated and self.request.user.is_staff):
            queryset = queryset.filter(is_active=True)
        return queryset.order_by("name")


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter("q", str, description="Full-text search term."),
            OpenApiParameter("category", str, description="Category slug (includes children)."),
            OpenApiParameter("brand", str, description="Comma-separated brand slugs."),
            OpenApiParameter("min_price", str),
            OpenApiParameter("max_price", str),
            OpenApiParameter("rating", int, description="Minimum average rating."),
            OpenApiParameter("size", str, description="Comma-separated sizes."),
            OpenApiParameter("color", str, description="Comma-separated colours."),
            OpenApiParameter("availability", str, enum=["in_stock", "on_sale"]),
            OpenApiParameter(
                "sort",
                str,
                enum=["relevance", "newest", "price_asc", "price_desc", "rating", "popularity", "discount"],
            ),
        ]
    )
)
@extend_schema(tags=["catalog"])
class ProductViewSet(viewsets.ModelViewSet):
    """Products.

    Reads are public and reuse the storefront's own filter/sort/search
    helpers, so the API and the HTML listing return the same results.
    """

    permission_classes = [IsStaffOrReadOnly]
    lookup_field = "slug"

    def get_queryset(self):
        user = getattr(self.request, "user", None)
        is_staff = bool(user and user.is_authenticated and user.is_staff)
        base = Product.objects.all() if is_staff else Product.objects.published()

        if self.action != "list":
            # Detail responses need active variants with their stock. Build the
            # prefetch set in one go -- layering a second ``variants`` prefetch
            # on top of ``with_related()`` raises ValueError, which DRF would
            # silently turn into a 404.
            return base.select_related("category", "brand").prefetch_related(
                "images",
                Prefetch(
                    "variants",
                    queryset=ProductVariant.objects.filter(is_active=True)
                    .select_related("inventory")
                    .order_by("sort_order", "id"),
                ),
            )

        queryset = base.with_related()
        params = self.request.query_params
        term = (params.get("q") or "").strip()
        if term:
            queryset = search_products(queryset, term)

        data = {
            key: params.get(key)
            for key in (
                "category",
                "brand",
                "min_price",
                "max_price",
                "rating",
                "size",
                "color",
                "availability",
            )
        }
        data["rating"] = int(data["rating"]) if (data.get("rating") or "").isdigit() else None
        queryset = apply_filters(queryset, data)
        queryset, _sort = apply_sorting(queryset, params.get("sort"), bool(term))
        return queryset

    def get_serializer_class(self):
        if self.action in {"create", "update", "partial_update"}:
            return api_serializers.ProductWriteSerializer
        if self.action == "list":
            return api_serializers.ProductListSerializer
        return api_serializers.ProductDetailSerializer

    @extend_schema(responses=api_serializers.ProductVariantSerializer(many=True))
    @action(detail=True, methods=["get"])
    def variants(self, request, slug=None):
        product = self.get_object()
        variants = product.variants.filter(is_active=True).select_related("inventory")
        return Response(
            api_serializers.ProductVariantSerializer(
                variants, many=True, context=self.get_serializer_context()
            ).data
        )

    @extend_schema(responses=api_serializers.ReviewSerializer(many=True))
    @action(detail=True, methods=["get"])
    def reviews(self, request, slug=None):
        product = self.get_object()
        queryset = Review.objects.for_product(product).with_author().order_by("-created_at")
        page = self.paginate_queryset(queryset)
        serializer = api_serializers.ReviewSerializer(
            page, many=True, context=self.get_serializer_context()
        )
        return self.get_paginated_response(serializer.data)

    @extend_schema(responses=api_serializers.ProductListSerializer(many=True))
    @action(detail=False, methods=["get"])
    def featured(self, request):
        queryset = Product.objects.published().with_related().filter(is_featured=True)[:12]
        return Response(
            api_serializers.ProductListSerializer(
                queryset, many=True, context=self.get_serializer_context()
            ).data
        )

    @extend_schema(responses=api_serializers.ProductListSerializer(many=True))
    @action(detail=False, methods=["get"], url_path="best-sellers")
    def best_sellers(self, request):
        queryset = Product.objects.best_sellers().with_related()[:12]
        return Response(
            api_serializers.ProductListSerializer(
                queryset, many=True, context=self.get_serializer_context()
            ).data
        )


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------
@extend_schema(tags=["account"])
class AddressViewSet(viewsets.ModelViewSet):
    serializer_class = api_serializers.AddressSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    queryset = Address.objects.none()  # schema generation only

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Address.objects.none()
        return self.request.user.addresses.all()

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @extend_schema(request=None, responses=api_serializers.AddressSerializer)
    @action(detail=True, methods=["post"], url_path="set-default")
    def set_default(self, request, pk=None):
        address = self.get_object()
        address.make_default()
        address.refresh_from_db()
        return Response(self.get_serializer(address).data)


# ---------------------------------------------------------------------------
# Cart
# ---------------------------------------------------------------------------
@extend_schema(tags=["cart"])
class CartViewSet(viewsets.ViewSet):
    """The authenticated user's cart.

    Totals are always recomputed server-side; the client can only change
    which variants are in the cart and how many.
    """

    permission_classes = [IsAuthenticated]

    def _cart(self, request):
        return get_cart(request)

    def _cart_response(self, request, http_status=status.HTTP_200_OK, message=None):
        data = api_serializers.CartSerializer(
            self._cart(request), context={"request": request}
        ).data
        if message:
            data["message"] = str(message)
        return Response(data, status=http_status)

    @extend_schema(responses=api_serializers.CartSerializer)
    def list(self, request):
        return self._cart_response(request)

    @extend_schema(
        request=api_serializers.AddToCartSerializer,
        responses={201: api_serializers.CartSerializer},
    )
    @action(detail=False, methods=["post"], url_path="items")
    def add_item(self, request):
        serializer = api_serializers.AddToCartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            _item, message = add_to_cart(
                request, serializer.variant, serializer.validated_data["quantity"]
            )
        except CartError as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST
            )
        return self._cart_response(request, status.HTTP_201_CREATED, message)

    @extend_schema(
        request=api_serializers.UpdateCartItemSerializer,
        responses=api_serializers.CartSerializer,
    )
    @action(detail=False, methods=["put", "patch"], url_path=r"items/(?P<item_id>\d+)")
    def update_item(self, request, item_id=None):
        serializer = api_serializers.UpdateCartItemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            _item, message = cart_update_quantity(
                request, item_id, serializer.validated_data["quantity"]
            )
        except CartError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return self._cart_response(request, message=message)

    @extend_schema(responses=api_serializers.CartSerializer)
    @action(detail=False, methods=["delete"], url_path=r"items/(?P<item_id>\d+)/remove")
    def remove_item(self, request, item_id=None):
        try:
            message = cart_remove_item(request, item_id)
        except CartError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        return self._cart_response(request, message=message)

    @extend_schema(responses=api_serializers.CartSerializer)
    @action(detail=False, methods=["delete"])
    def clear(self, request):
        self._cart(request).clear()
        return self._cart_response(request, message="Cart cleared.")

    @extend_schema(
        request=api_serializers.ApplyCouponSerializer,
        responses=api_serializers.CartSerializer,
    )
    @action(detail=False, methods=["post"], url_path="apply-coupon")
    def apply_coupon(self, request):
        serializer = api_serializers.ApplyCouponSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        cart = self._cart(request)
        try:
            coupon, discount = coupon_services.apply_to_cart(
                cart, serializer.validated_data["code"], user=request.user
            )
        except coupon_services.CouponError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return self._cart_response(
            request, message=f"Coupon {coupon.code} applied ({discount} off)."
        )

    @extend_schema(responses=api_serializers.CartSerializer)
    @action(detail=False, methods=["delete"], url_path="remove-coupon")
    def remove_coupon(self, request):
        coupon_services.remove_from_cart(self._cart(request))
        return self._cart_response(request, message="Coupon removed.")


# ---------------------------------------------------------------------------
# Wishlist
# ---------------------------------------------------------------------------
@extend_schema(
    tags=["wishlist"],
    # A plain ViewSet has no queryset for the generator to infer from, so the
    # path parameter type is declared here.
    parameters=[
        OpenApiParameter(
            "id", int, OpenApiParameter.PATH, description="Wishlist item id."
        )
    ],
)
class WishlistViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def _items(self, request):
        return Wishlist.for_user(request.user).live_items()

    @extend_schema(responses=api_serializers.WishlistItemSerializer(many=True))
    def list(self, request):
        return Response(
            api_serializers.WishlistItemSerializer(
                self._items(request), many=True, context={"request": request}
            ).data
        )

    @extend_schema(
        request=api_serializers.AddToWishlistSerializer,
        responses={201: api_serializers.WishlistItemSerializer},
    )
    def create(self, request):
        serializer = api_serializers.AddToWishlistSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        wishlist = Wishlist.for_user(request.user)
        variant_id = serializer.validated_data.get("variant_id")
        variant = None
        if variant_id:
            variant = ProductVariant.objects.filter(
                pk=variant_id, product=serializer.product
            ).first()

        item, created = WishlistItem.objects.get_or_create(
            wishlist=wishlist,
            product=serializer.product,
            defaults={"variant": variant},
        )
        return Response(
            api_serializers.WishlistItemSerializer(item, context={"request": request}).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @extend_schema(responses={204: None})
    def destroy(self, request, pk=None):
        deleted, _details = WishlistItem.objects.filter(
            pk=pk, wishlist__user=request.user
        ).delete()
        if not deleted:
            return Response(
                {"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND
            )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(request=None, responses={200: None})
    @action(detail=True, methods=["post"], url_path="move-to-cart")
    def move_to_cart(self, request, pk=None):
        item = get_object_or_404(WishlistItem, pk=pk, wishlist__user=request.user)
        variant = item.movable_variant
        if variant is None:
            return Response(
                {"detail": "This product is not available."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            add_to_cart(request, variant, 1)
        except CartError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        item.delete()
        return Response(
            api_serializers.CartSerializer(
                get_cart(request), context={"request": request}
            ).data
        )


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------
@extend_schema(tags=["orders"])
class OrderViewSet(viewsets.ReadOnlyModelViewSet):
    """Customers see their own orders; staff see everything."""

    permission_classes = [IsAuthenticated, IsOwner]
    lookup_field = "order_number"
    ordering_fields = ("placed_at", "total_amount")

    queryset = Order.objects.none()  # schema generation only

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Order.objects.none()
        queryset = Order.objects.with_details()
        if not self.request.user.is_staff:
            queryset = queryset.filter(user=self.request.user)

        status_param = self.request.query_params.get("status")
        if status_param in dict(Order.Status.choices):
            queryset = queryset.filter(status=status_param)
        return queryset.order_by("-placed_at")

    def get_serializer_class(self):
        if self.action == "list":
            return api_serializers.OrderListSerializer
        return api_serializers.OrderDetailSerializer

    @extend_schema(
        request=api_serializers.CreateOrderSerializer,
        responses={201: api_serializers.OrderDetailSerializer},
    )
    def create(self, request):
        """POST /api/orders/ -- place an order from the current cart."""
        serializer = api_serializers.CreateOrderSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)

        cart = get_cart(request)
        try:
            order = order_services.place_order(
                user=request.user,
                cart=cart,
                address=serializer.address,
                payment_method=serializer.validated_data["payment_method"],
                customer_note=serializer.validated_data.get("customer_note", ""),
            )
        except order_services.OrderError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        if order.payment_method == Order.PaymentMethod.COD:
            order_services.confirm_cod(order)
            order.refresh_from_db()

        return Response(
            api_serializers.OrderDetailSerializer(order, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        request=api_serializers.CancelOrderSerializer,
        responses=api_serializers.OrderDetailSerializer,
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request, order_number=None):
        order = self.get_object()
        serializer = api_serializers.CancelOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            order_services.cancel_order(
                order,
                user=request.user,
                reason=serializer.validated_data.get("reason", ""),
                staff_override=request.user.is_staff,
            )
        except order_services.OrderError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        order.refresh_from_db()
        return Response(
            api_serializers.OrderDetailSerializer(order, context={"request": request}).data
        )

    @extend_schema(
        request=api_serializers.OrderStatusUpdateSerializer,
        responses=api_serializers.OrderDetailSerializer,
    )
    @action(
        detail=True,
        methods=["put", "patch"],
        permission_classes=[IsStaff],
    )
    def status(self, request, order_number=None):
        """PUT /api/orders/<order_number>/status/ -- staff only."""
        order = get_object_or_404(Order, order_number=order_number)
        serializer = api_serializers.OrderStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        updates = []
        if data.get("tracking_number"):
            order.tracking_number = data["tracking_number"]
            updates.append("tracking_number")
        if data.get("courier_name"):
            order.courier_name = data["courier_name"]
            updates.append("courier_name")
        if updates:
            order.save(update_fields=updates + ["updated_at"])

        try:
            order_services.transition_order(
                order, data["status"], user=request.user, note=data.get("note", "")
            )
        except order_services.OrderError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        order.refresh_from_db()
        return Response(
            api_serializers.OrderDetailSerializer(order, context={"request": request}).data
        )

    @extend_schema(request=None, responses=api_serializers.CartSerializer)
    @action(detail=True, methods=["post"])
    def reorder(self, request, order_number=None):
        """Refill the cart from a past order."""
        order = self.get_object()
        added = 0
        for item in order.items.select_related("variant__inventory"):
            if not item.variant_id or not item.variant.is_active:
                continue
            try:
                add_to_cart(request, item.variant, item.quantity)
                added += 1
            except CartError:
                continue
        data = api_serializers.CartSerializer(
            get_cart(request), context={"request": request}
        ).data
        data["items_added"] = added
        return Response(data)


@extend_schema(tags=["orders"])
class ReturnRequestViewSet(viewsets.ModelViewSet):
    """Returns and refunds."""

    serializer_class = api_serializers.ReturnRequestSerializer
    permission_classes = [IsAuthenticated, IsOwner]
    http_method_names = ["get", "post", "head", "options"]

    queryset = ReturnRequest.objects.none()  # schema generation only

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return ReturnRequest.objects.none()
        queryset = ReturnRequest.objects.select_related("order", "order_item")
        if not self.request.user.is_staff:
            queryset = queryset.filter(order__user=self.request.user)
        return queryset.order_by("-created_at")

    @extend_schema(
        request=api_serializers.CreateReturnSerializer,
        responses={201: api_serializers.ReturnRequestSerializer},
    )
    def create(self, request, *args, **kwargs):
        serializer = api_serializers.CreateReturnSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        try:
            return_request = order_services.request_return(
                order_item=serializer.order_item,
                quantity=serializer.validated_data["quantity"],
                reason=serializer.validated_data["reason"],
                comment=serializer.validated_data.get("comment", ""),
                user=request.user,
            )
        except order_services.OrderError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            api_serializers.ReturnRequestSerializer(
                return_request, context={"request": request}
            ).data,
            status=status.HTTP_201_CREATED,
        )


# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------
@extend_schema(tags=["reviews"])
class ReviewViewSet(viewsets.ModelViewSet):
    """Public reads; writes require a delivered purchase."""

    serializer_class = api_serializers.ReviewSerializer
    permission_classes = [IsOwnerOrReadOnly]
    filterset_fields = ("product", "rating", "verified_purchase")
    ordering_fields = ("created_at", "rating", "helpful_count")

    queryset = Review.objects.none()  # schema generation only

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Review.objects.none()
        queryset = Review.objects.approved().with_author().select_related("product")
        if self.action in {"update", "partial_update", "destroy"}:
            queryset = Review.objects.filter(user=self.request.user)
        return queryset.order_by("-created_at")

    @extend_schema(request=None, responses={200: None})
    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def helpful(self, request, pk=None):
        from django.db.models import F

        from apps.reviews.models import ReviewHelpfulVote

        review = self.get_object()
        if review.user_id == request.user.id:
            return Response(
                {"detail": "You cannot vote on your own review."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        _vote, created = ReviewHelpfulVote.objects.get_or_create(
            review=review, user=request.user
        )
        if created:
            Review.objects.filter(pk=review.pk).update(helpful_count=F("helpful_count") + 1)
            review.refresh_from_db(fields=["helpful_count"])
        return Response({"helpful_count": review.helpful_count, "voted": True})


# ---------------------------------------------------------------------------
# Coupons (staff) + public offers
# ---------------------------------------------------------------------------
@extend_schema(tags=["coupons"])
class CouponViewSet(viewsets.ModelViewSet):
    """Full CRUD for staff."""

    queryset = Coupon.objects.all().order_by("-created_at")
    serializer_class = api_serializers.CouponSerializer
    permission_classes = [IsStaff]
    search_fields = ("code", "description")
    filterset_fields = ("discount_type", "is_active", "is_public")

    @extend_schema(responses=api_serializers.PublicCouponSerializer(many=True))
    @action(detail=False, methods=["get"], permission_classes=[AllowAny])
    def public(self, request):
        """Publicly advertised coupons -- safe subset of fields."""
        coupons = Coupon.objects.public()[:20]
        return Response(api_serializers.PublicCouponSerializer(coupons, many=True).data)


# ---------------------------------------------------------------------------
# Customers (staff)
# ---------------------------------------------------------------------------
@extend_schema(tags=["staff"])
class CustomerViewSet(viewsets.ReadOnlyModelViewSet):
    """GET /api/customers/ -- staff-only customer list with aggregates."""

    serializer_class = api_serializers.CustomerListSerializer
    permission_classes = [IsStaff]
    search_fields = ("email", "first_name", "last_name", "phone")
    ordering_fields = ("date_joined", "email")

    queryset = User.objects.none()  # schema generation only

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return User.objects.none()
        return (
            User.objects.filter(is_staff=False)
            .annotate(
                order_count=Count("orders", distinct=True),
                lifetime_value=Sum(
                    "orders__total_amount",
                    filter=~Q(orders__status__in=["cancelled", "returned", "refunded"]),
                ),
            )
            .order_by("-date_joined")
        )

    @extend_schema(responses=api_serializers.OrderListSerializer(many=True))
    @action(detail=True, methods=["get"])
    def orders(self, request, pk=None):
        customer = self.get_object()
        queryset = Order.objects.filter(user=customer).order_by("-placed_at")
        page = self.paginate_queryset(queryset)
        serializer = api_serializers.OrderListSerializer(
            page, many=True, context=self.get_serializer_context()
        )
        return self.get_paginated_response(serializer.data)


# ---------------------------------------------------------------------------
# Marketing (public read)
# ---------------------------------------------------------------------------
@extend_schema(tags=["marketing"])
class BannerViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = api_serializers.BannerSerializer
    permission_classes = [AllowAny]
    pagination_class = None

    def get_queryset(self):
        queryset = Banner.objects.live()
        position = self.request.query_params.get("position")
        if position:
            queryset = queryset.filter(position=position)
        return queryset.order_by("sort_order")


@extend_schema(tags=["marketing"])
class OfferViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = api_serializers.OfferSerializer
    permission_classes = [AllowAny]
    pagination_class = None

    def get_queryset(self):
        return Offer.objects.live().select_related("coupon").order_by("sort_order")


@extend_schema(tags=["marketing"])
class FlashSaleViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = api_serializers.FlashSaleSerializer
    permission_classes = [AllowAny]
    pagination_class = None

    def get_queryset(self):
        return FlashSale.objects.live().prefetch_related(
            "items__variant__product", "items__variant__inventory"
        )


# ---------------------------------------------------------------------------
# Dashboard (staff)
# ---------------------------------------------------------------------------
@extend_schema(tags=["staff"])
class DashboardStatsView(APIView):
    """GET /api/dashboard/stats/ -- the same figures the HTML dashboard shows."""

    permission_classes = [IsStaff]

    @extend_schema(responses={200: None})
    def get(self, request):
        stats = reports.live_stats()
        return Response(
            {
                key: (str(value) if hasattr(value, "quantize") else value)
                for key, value in stats.items()
            }
        )


@extend_schema(tags=["staff"])
class DashboardChartView(APIView):
    """GET /api/dashboard/charts/<chart>/ -- JSON series for Chart.js."""

    permission_classes = [IsStaff]

    @extend_schema(responses={200: None})
    def get(self, request, chart):
        try:
            days = max(min(int(request.query_params.get("days", 30)), 365), 7)
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
            return Response({"detail": "Unknown chart."}, status=status.HTTP_404_NOT_FOUND)
        return Response(builder())
