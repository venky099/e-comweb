"""Expose the request's locale to every template."""
from apps.geo import services


def locale(request):
    current = getattr(request, "locale", None)
    if current is None:
        return {}
    return {
        "LOCALE": current,
        "ACTIVE_CURRENCY": current.currency,
        "ACTIVE_COUNTRY": current.country,
        "AVAILABLE_CURRENCIES": services.active_currencies(),
        "AVAILABLE_COUNTRIES": services.active_countries(),
        # Staff reporting is company accounting and stays in the base
        # currency whatever the viewer is browsing in.
        "BASE_CURRENCY": services.display_currency(),
    }
