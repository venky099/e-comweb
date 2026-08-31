"""Checkout flow and customer order management."""
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, ListView, View
from django_ratelimit.decorators import ratelimit

from apps.accounts.forms import AddressForm
from apps.cart.services import clamp_cart_to_stock, get_cart
from apps.shipping import services as shipping_services

from . import services
from . import services as order_services
from .forms import CancelOrderForm, CheckoutAddressForm, CheckoutPaymentForm, ReturnRequestForm
from .models import Order, OrderItem, ReturnRequest

logger = logging.getLogger("ecommerce")

CHECKOUT_ADDRESS_KEY = "checkout_address_id"


def build_tracking_steps(order):
    """The five-step progress bar shown on order detail and tracking pages."""
    flow = [
        (Order.Status.PENDING, _("Order placed"), order.placed_at),
        (Order.Status.CONFIRMED, _("Confirmed"), order.confirmed_at),
        (Order.Status.PROCESSING, _("Processing"), None),
        (Order.Status.SHIPPED, _("Shipped"), order.shipped_at),
        (Order.Status.DELIVERED, _("Delivered"), order.delivered_at),
    ]
    current = order.status_index
    return [
        {
            "status": status,
            "label": label,
            "timestamp": stamp,
            "done": current >= index and current >= 0,
            "active": current == index,
        }
        for index, (status, label, stamp) in enumerate(flow)
    ]


# ---------------------------------------------------------------------------
# Checkout (sequential steps, state kept in the session)
# ---------------------------------------------------------------------------
class CheckoutStepMixin(LoginRequiredMixin):
    """Shared guards: a signed-in user with a non-empty, in-stock cart."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)

        self.cart = get_cart(request)
        if self.cart.is_empty:
            messages.info(request, _("Your cart is empty."))
            return redirect("cart:detail")

        adjusted = clamp_cart_to_stock(self.cart)
        if adjusted:
            messages.warning(
                request,
                _("Some items changed availability. Please review your cart."),
            )
            return redirect("cart:detail")

        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        return {
            "cart": self.cart,
            "items": self.cart.live_items(),
            "summary": self.cart.as_summary(),
            **kwargs,
        }


class CheckoutAddressView(CheckoutStepMixin, View):
    """Step 1 -- choose or add a delivery address."""

    template_name = "orders/checkout_address.html"

    def get(self, request):
        addresses = request.user.addresses.all()
        if not addresses.exists():
            messages.info(request, _("Add a delivery address to continue."))
            return redirect(f"{self._address_create_url()}?next={request.path}")

        selected = request.session.get(CHECKOUT_ADDRESS_KEY)
        default = request.user.default_address()
        initial = {"address": selected or (default.pk if default else None)}

        return render(
            request,
            self.template_name,
            {
                **self.get_context_data(),
                "form": CheckoutAddressForm(user=request.user, initial=initial),
                "address_form": AddressForm(),
                "addresses": addresses,
                "step": 1,
            },
        )

    def post(self, request):
        form = CheckoutAddressForm(request.POST, user=request.user)
        if form.is_valid():
            request.session[CHECKOUT_ADDRESS_KEY] = form.cleaned_data["address"].pk
            return redirect("orders:checkout_payment")

        messages.error(request, _("Please choose a delivery address."))
        return render(
            request,
            self.template_name,
            {
                **self.get_context_data(),
                "form": form,
                "address_form": AddressForm(),
                "addresses": request.user.addresses.all(),
                "step": 1,
            },
        )

    @staticmethod
    def _address_create_url():
        from django.urls import reverse

        return reverse("accounts:address_create")


@method_decorator(
    ratelimit(key="user_or_ip", rate="20/m", method="POST", block=True), name="dispatch"
)
class CheckoutPaymentView(CheckoutStepMixin, View):
    """Step 2 -- review, choose payment, place the order.

    Rate limited: order placement touches stock and coupons, so it is not
    something to let a script hammer.
    """

    template_name = "orders/checkout_payment.html"

    def get_address(self, request):
        address_id = request.session.get(CHECKOUT_ADDRESS_KEY)
        if not address_id:
            return None
        return request.user.addresses.filter(pk=address_id).first()

    def delivery_options(self, address):
        """The delivery choices for this address, and the preselected one.

        Quoted from the same service place_order() will use, so the price
        shown here is the price charged -- there is no second calculation to
        drift out of step.
        """
        items = list(self.cart.live_items())
        country, _state = order_services.destination_for(address.as_snapshot())
        options = shipping_services.quote(items, country, self.cart.subtotal)
        return options, shipping_services.default_option(options)

    def get(self, request):
        address = self.get_address(request)
        if address is None:
            messages.info(request, _("Choose a delivery address first."))
            return redirect("orders:checkout_address")

        cod_allowed = all(i.variant.product.is_cod_available for i in self.cart.live_items())
        options, default = self.delivery_options(address)
        return render(
            request,
            self.template_name,
            {
                **self.get_context_data(),
                "address": address,
                "form": CheckoutPaymentForm(cod_allowed=cod_allowed),
                "cod_allowed": cod_allowed,
                "delivery_options": options,
                "selected_delivery": default.code if default else "",
                "step": 2,
            },
        )

    def post(self, request):
        address = self.get_address(request)
        if address is None:
            messages.error(request, _("Choose a delivery address first."))
            return redirect("orders:checkout_address")

        cod_allowed = all(i.variant.product.is_cod_available for i in self.cart.live_items())
        form = CheckoutPaymentForm(request.POST, cod_allowed=cod_allowed)

        if not form.is_valid():
            messages.error(request, _("Please correct the errors below."))
            # Re-quote so the redisplayed page still offers delivery choices.
            options, _default = self.delivery_options(address)
            return render(
                request,
                self.template_name,
                {
                    **self.get_context_data(),
                    "address": address,
                    "form": form,
                    "cod_allowed": cod_allowed,
                    "delivery_options": options,
                    "selected_delivery": request.POST.get("shipping_method", ""),
                    "step": 2,
                },
            )

        try:
            order = services.place_order(
                user=request.user,
                cart=self.cart,
                address=address,
                payment_method=form.cleaned_data["payment_method"],
                customer_note=form.cleaned_data.get("customer_note", ""),
                # The order is charged in the currency the prices were shown
                # in, and the rate is frozen onto it.
                currency=getattr(getattr(request, "locale", None), "currency", None),
                shipping_method_code=request.POST.get("shipping_method") or None,
            )
        except services.OrderError as exc:
            messages.error(request, str(exc))
            return redirect("cart:detail")

        request.session.pop(CHECKOUT_ADDRESS_KEY, None)

        # COD needs no gateway; everything else goes to the payment step.
        if order.payment_method == Order.PaymentMethod.COD:
            services.confirm_cod(order)
            messages.success(
                request,
                _("Order %(number)s placed. Pay when it arrives.")
                % {"number": order.order_number},
            )
            return redirect("orders:confirmation", order_number=order.order_number)

        return redirect("payments:start", order_number=order.order_number)


class OrderConfirmationView(LoginRequiredMixin, DetailView):
    """Thank-you page shown right after placement."""

    template_name = "orders/confirmation.html"
    context_object_name = "order"
    slug_field = "order_number"
    slug_url_kwarg = "order_number"

    def get_queryset(self):
        return Order.objects.filter(user=self.request.user).prefetch_related("items")


# ---------------------------------------------------------------------------
# Customer order management
# ---------------------------------------------------------------------------
class OrderListView(LoginRequiredMixin, ListView):
    template_name = "orders/list.html"
    context_object_name = "orders"
    paginate_by = 10

    def get_queryset(self):
        queryset = (
            Order.objects.filter(user=self.request.user)
            .prefetch_related("items")
            .order_by("-placed_at")
        )
        status = self.request.GET.get("status")
        if status in dict(Order.Status.choices):
            queryset = queryset.filter(status=status)
        term = (self.request.GET.get("q") or "").strip()
        if term:
            queryset = queryset.filter(
                Q(order_number__icontains=term) | Q(items__product_name__icontains=term)
            ).distinct()
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["status_choices"] = Order.Status.choices
        context["active_status"] = self.request.GET.get("status", "")
        context["search_term"] = self.request.GET.get("q", "")
        return context


class OrderDetailView(LoginRequiredMixin, DetailView):
    """Order detail with the tracking timeline."""

    template_name = "orders/detail.html"
    context_object_name = "order"
    slug_field = "order_number"
    slug_url_kwarg = "order_number"

    def get_queryset(self):
        # Scoped to the owner -- another customer's order number 404s.
        return (
            Order.objects.filter(user=self.request.user)
            .select_related("coupon")
            .prefetch_related("items", "status_history__changed_by", "return_requests", "payments")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        order = self.object
        context["timeline"] = order.status_history.all()
        context["cancel_form"] = CancelOrderForm()
        context["tracking_steps"] = build_tracking_steps(order)
        context["returnable_items"] = [
            item
            for item in order.items.all()
            if item.is_returnable and item.status == OrderItem.ItemStatus.ACTIVE
        ] if order.can_be_returned else []
        return context


@login_required
@require_POST
def cancel_order(request, order_number):
    """Customer-initiated cancellation."""
    order = get_object_or_404(Order, order_number=order_number, user=request.user)
    form = CancelOrderForm(request.POST)

    if not form.is_valid():
        messages.error(request, _("Please select a cancellation reason."))
        return redirect("orders:detail", order_number=order_number)

    try:
        services.cancel_order(order, user=request.user, reason=form.full_reason())
        messages.success(
            request,
            _("Order %(number)s cancelled. Any payment will be refunded within 5-7 days.")
            % {"number": order.order_number},
        )
    except services.OrderError as exc:
        messages.error(request, str(exc))

    return redirect("orders:detail", order_number=order_number)


@login_required
def request_return(request, order_number, item_id):
    """Open a return against one delivered line."""
    order = get_object_or_404(Order, order_number=order_number, user=request.user)
    item = get_object_or_404(OrderItem, pk=item_id, order=order)

    if request.method == "POST":
        form = ReturnRequestForm(request.POST, order_item=item)
        if form.is_valid():
            try:
                services.request_return(
                    order_item=item,
                    quantity=form.cleaned_data["quantity"],
                    reason=form.cleaned_data["reason"],
                    comment=form.cleaned_data.get("comment", ""),
                    user=request.user,
                )
                messages.success(
                    request, _("Return requested. We will email you once it is reviewed.")
                )
                return redirect("orders:detail", order_number=order_number)
            except services.OrderError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, _("Please correct the errors below."))
    else:
        form = ReturnRequestForm(order_item=item)

    return render(
        request,
        "orders/return_request.html",
        {"order": order, "item": item, "form": form},
    )


class ReturnListView(LoginRequiredMixin, ListView):
    """All returns/refunds raised by this customer."""

    template_name = "orders/returns.html"
    context_object_name = "returns"
    paginate_by = 15

    def get_queryset(self):
        return (
            ReturnRequest.objects.filter(order__user=self.request.user)
            .select_related("order", "order_item")
            .order_by("-created_at")
        )


@login_required
def track_order(request, order_number):
    """Standalone tracking page (shareable link within the account)."""
    order = get_object_or_404(
        Order.objects.prefetch_related("status_history", "items"),
        order_number=order_number,
        user=request.user,
    )
    return render(
        request,
        "orders/track.html",
        {
            "order": order,
            "timeline": order.status_history.all(),
            "tracking_steps": build_tracking_steps(order),
        },
    )


@login_required
@require_POST
def reorder(request, order_number):
    """Put a past order's items back into the cart."""
    from apps.cart.services import CartError, add_to_cart

    order = get_object_or_404(
        Order.objects.prefetch_related("items__variant__inventory"),
        order_number=order_number,
        user=request.user,
    )

    added, skipped = 0, 0
    for item in order.items.all():
        if not item.variant_id or not item.variant.is_active:
            skipped += 1
            continue
        try:
            add_to_cart(request, item.variant, item.quantity)
            added += 1
        except CartError:
            skipped += 1

    if added:
        messages.success(request, _("%(n)d item(s) added to your cart.") % {"n": added})
    if skipped:
        messages.warning(
            request, _("%(n)d item(s) are no longer available.") % {"n": skipped}
        )
    if added:
        return redirect("cart:detail")
    return redirect("orders:detail", order_number=order_number)
