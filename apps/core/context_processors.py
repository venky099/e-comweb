"""Template context available on every page."""
from django.conf import settings


def site_context(request):
    """Branding, contact details and money/policy constants.

    Templates read these instead of hardcoding a currency symbol or a delivery
    threshold, so changing settings changes the whole storefront.
    """
    return {
        "SITE_NAME": settings.SITE_NAME,
        "SITE_TAGLINE": settings.SITE_TAGLINE,
        "SUPPORT_EMAIL": settings.SUPPORT_EMAIL,
        "SUPPORT_PHONE": settings.SUPPORT_PHONE,
        "CURRENCY_SYMBOL": settings.CURRENCY_SYMBOL,
        "FREE_DELIVERY_THRESHOLD": settings.FREE_DELIVERY_THRESHOLD,
        "DELIVERY_CHARGE": settings.DELIVERY_CHARGE,
        "RETURN_WINDOW_DAYS": settings.RETURN_WINDOW_DAYS,
        "ORDER_CANCEL_WINDOW_HOURS": settings.ORDER_CANCEL_WINDOW_HOURS,
        "MAX_CART_QUANTITY_PER_ITEM": settings.MAX_CART_QUANTITY_PER_ITEM,
    }
