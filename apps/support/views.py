"""Customer-facing support (MST spec section 51)."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.generic import ListView

from apps.support import services
from apps.support.forms import ReplyForm, TicketForm
from apps.support.models import Ticket


class TicketListView(LoginRequiredMixin, ListView):
    template_name = "support/list.html"
    context_object_name = "tickets"
    paginate_by = 20

    def get_queryset(self):
        return Ticket.objects.filter(user=self.request.user).select_related("order")


@login_required
def ticket_create(request):
    form = TicketForm(request.POST or None, request.FILES or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            ticket = services.open_ticket(
                request.user,
                subject=form.cleaned_data["subject"],
                body=form.cleaned_data["body"],
                topic=form.cleaned_data["topic"],
                order=form.cleaned_data.get("order"),
                attachment=form.cleaned_data.get("attachment"),
            )
        except services.SupportError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                _("Ticket %(ref)s opened. We usually reply within a working day.")
                % {"ref": ticket.reference},
            )
            return redirect(ticket.get_absolute_url())

    return render(request, "support/create.html", {"form": form})


@login_required
def ticket_detail(request, reference):
    ticket = get_object_or_404(
        Ticket.objects.select_related("order", "user"), reference=reference
    )
    # 404 rather than 403: confirming someone else's ticket exists is a leak.
    if ticket.user_id != request.user.id and not request.user.is_staff:
        from django.http import Http404

        raise Http404

    form = ReplyForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.add_message(
                ticket,
                request.user,
                form.cleaned_data["body"],
                attachment=form.cleaned_data.get("attachment"),
            )
        except services.SupportError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Reply sent."))
            return redirect(ticket.get_absolute_url())

    return render(
        request,
        "support/detail.html",
        {
            "ticket": ticket,
            "messages_list": services.visible_messages(ticket, request.user),
            "form": form,
        },
    )
