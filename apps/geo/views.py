"""Country and currency selectors.

POST-only and CSRF-protected: switching currency changes every price the
visitor sees, so it should not be reachable from a crafted link.
"""
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.geo import services
from apps.geo.locale_context import SESSION_COUNTRY, SESSION_CURRENCY


def _back(request):
    """Return where the visitor came from, refusing off-site redirects."""
    target = request.POST.get("next") or request.META.get("HTTP_REFERER") or "/"
    if not target.startswith("/") or target.startswith("//"):
        target = "/"
    return HttpResponseRedirect(target)


@require_POST
def set_country(request):
    country = services.get_country(request.POST.get("country"), fallback=False)
    if country is None:
        messages.error(request, _("That country is not available."))
        return _back(request)

    request.session[SESSION_COUNTRY] = country.iso2
    # Changing country moves the visitor to that country's currency unless
    # they have deliberately chosen a different one.
    if SESSION_CURRENCY not in request.session:
        request.session[SESSION_CURRENCY] = country.currency.code
    messages.success(
        request,
        _("Shopping in %(country)s. Prices shown in %(currency)s.")
        % {"country": country.name, "currency": country.currency.code},
    )
    return _back(request)


@require_POST
def set_currency(request):
    currency = services.get_currency(
        request.POST.get("currency"), fallback_to_base=False
    )
    if currency is None:
        messages.error(request, _("That currency is not available."))
        return _back(request)

    try:
        services.rate_for(currency)
    except services.CurrencyError:
        messages.error(
            request,
            _("Prices in %(code)s are not available yet.") % {"code": currency.code},
        )
        return _back(request)

    request.session[SESSION_CURRENCY] = currency.code
    messages.success(
        request, _("Prices now shown in %(code)s.") % {"code": currency.code}
    )
    return _back(request)
