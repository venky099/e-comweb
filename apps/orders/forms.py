"""Checkout, cancellation and return forms."""
from django import forms
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import Address

from .models import Order, ReturnRequest


class CheckoutAddressForm(forms.Form):
    """Step 1: pick a saved address.

    The queryset is scoped to the signed-in user, so posting somebody else's
    address id fails validation rather than leaking it.
    """

    address = forms.ModelChoiceField(
        queryset=Address.objects.none(),
        widget=forms.RadioSelect,
        empty_label=None,
        label=_("Deliver to"),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["address"].queryset = Address.objects.filter(user=user)


class CheckoutPaymentForm(forms.Form):
    """Step 2: payment method and an optional delivery note."""

    payment_method = forms.ChoiceField(
        choices=Order.PaymentMethod.choices,
        widget=forms.RadioSelect,
        label=_("Payment method"),
        initial=Order.PaymentMethod.UPI,
    )
    customer_note = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 2,
                "class": "form-control",
                "placeholder": _("Delivery instructions (optional)"),
            }
        ),
        max_length=500,
        label=_("Order note"),
    )
    terms_accepted = forms.BooleanField(
        required=True,
        label=_("I agree to the Terms of Sale and the Return Policy"),
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def __init__(self, *args, cod_allowed=True, **kwargs):
        super().__init__(*args, **kwargs)
        if not cod_allowed:
            # One non-COD item in the basket disables COD for the whole order.
            self.fields["payment_method"].choices = [
                (value, label)
                for value, label in Order.PaymentMethod.choices
                if value != Order.PaymentMethod.COD
            ]


class CancelOrderForm(forms.Form):
    REASONS = [
        ("changed_mind", _("I changed my mind")),
        ("found_cheaper", _("Found a better price elsewhere")),
        ("ordered_by_mistake", _("Ordered by mistake")),
        ("delivery_too_slow", _("Delivery is taking too long")),
        ("other", _("Other")),
    ]

    reason = forms.ChoiceField(choices=REASONS, label=_("Why are you cancelling?"))
    comment = forms.CharField(
        required=False,
        max_length=255,
        widget=forms.Textarea(attrs={"rows": 2, "class": "form-control"}),
        label=_("Anything else we should know?"),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reason"].widget.attrs["class"] = "form-select"

    def full_reason(self):
        label = dict(self.REASONS).get(self.cleaned_data["reason"], "")
        comment = self.cleaned_data.get("comment", "")
        return f"{label}: {comment}".strip(": ") if comment else str(label)


class ReturnRequestForm(forms.ModelForm):
    """Return one line. Quantity is bounded by what was actually delivered."""

    class Meta:
        model = ReturnRequest
        fields = ("quantity", "reason", "comment")
        widgets = {
            "comment": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
        }
        labels = {
            "quantity": _("How many are you returning?"),
            "reason": _("Reason"),
            "comment": _("Tell us more"),
        }

    def __init__(self, *args, order_item=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.order_item = order_item
        self.fields["reason"].widget.attrs["class"] = "form-select"
        self.fields["quantity"].widget.attrs["class"] = "form-control"

        if order_item is not None:
            already = sum(
                r.quantity
                for r in order_item.return_requests.exclude(
                    status=ReturnRequest.Status.REJECTED
                )
            )
            remaining = max(order_item.quantity - already, 0)
            self.fields["quantity"].widget.attrs["max"] = remaining
            self.fields["quantity"].widget.attrs["min"] = 1
            self.fields["quantity"].initial = remaining
            self._remaining = remaining

    def clean_quantity(self):
        quantity = self.cleaned_data["quantity"]
        remaining = getattr(self, "_remaining", None)
        if remaining is not None and quantity > remaining:
            raise forms.ValidationError(
                _("You can return at most %(n)d of this item.") % {"n": remaining}
            )
        return quantity


class OrderStatusForm(forms.Form):
    """Staff-side status update (used by the dashboard, not the admin)."""

    status = forms.ChoiceField(choices=Order.Status.choices, label=_("New status"))
    tracking_number = forms.CharField(required=False, max_length=64)
    courier_name = forms.CharField(required=False, max_length=100)
    note = forms.CharField(required=False, max_length=255)

    def __init__(self, *args, order=None, **kwargs):
        super().__init__(*args, **kwargs)
        if order is not None:
            allowed = order.TRANSITIONS.get(order.status, set())
            labels = dict(Order.Status.choices)
            self.fields["status"].choices = [(s, labels[s]) for s in sorted(allowed)]
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")
        self.fields["status"].widget.attrs["class"] = "form-select"
