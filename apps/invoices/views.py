"""Viewing and downloading invoices."""
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404

from apps.invoices import services
from apps.invoices.models import Invoice


def _visible_to(request, number):
    """An invoice belongs to its buyer; staff may see any."""
    invoice = get_object_or_404(
        Invoice.objects.select_related("order", "order__user"), number=number
    )
    user = request.user
    if user.is_staff or invoice.order.user_id == user.id:
        return invoice
    # 404 rather than 403: confirming that someone else's invoice exists is
    # itself a small leak.
    raise Http404


@login_required
def invoice_detail(request, number):
    invoice = _visible_to(request, number)
    return HttpResponse(services.render_html(invoice))


@login_required
def invoice_pdf(request, number):
    """The PDF if one has been generated, otherwise the printable page.

    Falling back keeps the link working on an installation with no PDF
    renderer available rather than serving an error.
    """
    invoice = _visible_to(request, number)
    if invoice.pdf:
        return FileResponse(
            invoice.pdf.open("rb"),
            as_attachment=True,
            filename=f"{invoice.number}.pdf",
        )
    response = HttpResponse(services.render_html(invoice))
    response["X-Invoice-Format"] = "html-fallback"
    return response
