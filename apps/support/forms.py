from django import forms
from django.utils.translation import gettext_lazy as _

from apps.support.models import Ticket


class TicketForm(forms.Form):
    """Opening a ticket. Deliberately short -- a long form deters asking."""

    topic = forms.ChoiceField(
        choices=Ticket.Topic.choices, initial=Ticket.Topic.OTHER, label=_("What is it about?")
    )
    order = forms.ModelChoiceField(
        queryset=Ticket._meta.get_field("order").related_model.objects.none(),
        required=False,
        label=_("Related order"),
        empty_label=_("Not about a specific order"),
    )
    subject = forms.CharField(max_length=150, label=_("Subject"))
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 6}), label=_("How can we help?"))
    attachment = forms.FileField(required=False, label=_("Attach a photo or file"))

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Only ever offer this customer's own orders.
        if user is not None:
            self.fields["order"].queryset = user.orders.order_by("-placed_at")[:50]
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, (forms.TextInput, forms.Textarea, forms.Select)):
                widget.attrs.setdefault("class", "form-control")


class ReplyForm(forms.Form):
    body = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 4, "class": "form-control"}),
        label=_("Reply"),
    )
    attachment = forms.FileField(required=False)
