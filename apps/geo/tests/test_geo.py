"""Currency, country and locale resolution.

The arithmetic here decides what customers are charged, so it is tested
against the spec's own worked example (section 8, page 9): a ₹5,000 product
shown as $58, £45, AED 213 and SGD 78.
"""
from decimal import Decimal

from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.geo import services
from apps.geo.locale_context import (
    SESSION_COUNTRY,
    SESSION_CURRENCY,
    LocaleMiddleware,
    resolve,
)
from apps.geo.models import Country, Currency, ExchangeRate


def make_currency(code="INR", symbol="₹", base=False, **kwargs):
    return Currency.objects.create(
        code=code, name=code, symbol=symbol, is_base=base, **kwargs
    )


def make_rate(base, quote, rate):
    return ExchangeRate.objects.create(
        base=base, quote=quote, rate=Decimal(rate), effective_from=timezone.now()
    )


class BaseCurrencyTests(TestCase):
    def test_only_one_currency_can_be_the_base(self):
        first = make_currency("INR", base=True)
        second = make_currency("USD", "$", base=True)
        first.refresh_from_db()
        self.assertFalse(first.is_base)
        self.assertTrue(second.is_base)

    def test_missing_base_currency_is_an_error_not_a_guess(self):
        with self.assertRaises(services.CurrencyError):
            services.base_currency()


class RoundingTests(TestCase):
    def test_decimal_places_are_honoured(self):
        usd = make_currency("USD", "$", decimal_places=2)
        self.assertEqual(usd.quantize(Decimal("58.005")), Decimal("58.01"))

    def test_nearest_whole_unit(self):
        jpy = make_currency(
            "JPY", "¥", decimal_places=0, rounding=Currency.Rounding.NEAREST
        )
        self.assertEqual(jpy.quantize(Decimal("58.4")), Decimal("58"))
        self.assertEqual(jpy.quantize(Decimal("58.5")), Decimal("59"))

    def test_charm_pricing_always_rounds_up(self):
        usd = make_currency("USD", "$", rounding=Currency.Rounding.UP)
        self.assertEqual(usd.quantize(Decimal("58.01")), Decimal("59"))

    def test_symbol_placement(self):
        prefix = make_currency("USD", "$")
        suffix = make_currency("SEK", "kr", symbol_is_prefix=False)
        self.assertEqual(prefix.format(Decimal("1234.5")), "$1,234.50")
        self.assertEqual(suffix.format(Decimal("1234.5")), "1,234.50 kr")


class ConversionTests(TestCase):
    def setUp(self):
        self.inr = make_currency("INR", "₹", base=True)
        self.usd = make_currency("USD", "$")
        make_rate(self.inr, self.usd, "0.01160")

    def test_the_base_currency_converts_at_one(self):
        self.assertEqual(services.rate_for(self.inr), Decimal("1"))
        self.assertEqual(
            services.convert(Decimal("5000"), self.inr), Decimal("5000.00")
        )

    def test_matches_the_specs_worked_example(self):
        self.assertEqual(services.convert(Decimal("5000"), self.usd), Decimal("58.00"))

    def test_a_currency_with_no_rate_raises_rather_than_charging_one_to_one(self):
        gbp = make_currency("GBP", "£")
        with self.assertRaises(services.CurrencyError):
            services.rate_for(gbp)

    def test_an_explicit_rate_reproduces_a_historical_amount(self):
        """An order stores its rate; reading it back must not use today's."""
        make_rate(self.inr, self.usd, "0.02000")  # rate moved
        historical = services.convert(
            Decimal("5000"), self.usd, rate=Decimal("0.01160")
        )
        self.assertEqual(historical, Decimal("58.00"))
        self.assertEqual(services.convert(Decimal("5000"), self.usd), Decimal("100.00"))

    def test_the_latest_rate_wins(self):
        make_rate(self.inr, self.usd, "0.01200")
        self.assertEqual(services.rate_for(self.usd), Decimal("0.01200000"))


class LocaleResolutionTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.inr = make_currency("INR", "₹", base=True)
        self.usd = make_currency("USD", "$")
        make_rate(self.inr, self.usd, "0.01160")
        self.india = Country.objects.create(
            iso2="IN", name="India", currency=self.inr, sort_order=0
        )
        self.usa = Country.objects.create(
            iso2="US", name="United States", currency=self.usd, sort_order=1
        )

    def request(self, session=None, **headers):
        request = self.factory.get("/", **headers)
        request.session = session if session is not None else {}
        return request

    def test_defaults_to_the_first_active_country(self):
        locale = resolve(self.request())
        self.assertEqual(locale.country, self.india)
        self.assertEqual(locale.currency, self.inr)

    def test_detects_country_from_a_cdn_header(self):
        locale = resolve(self.request(HTTP_CF_IPCOUNTRY="US"))
        self.assertEqual(locale.country, self.usa)
        self.assertEqual(locale.currency, self.usd)
        self.assertTrue(locale.detected)

    def test_detects_country_from_accept_language(self):
        locale = resolve(self.request(HTTP_ACCEPT_LANGUAGE="en-US,en;q=0.9"))
        self.assertEqual(locale.country, self.usa)

    def test_a_chosen_country_beats_ip_detection(self):
        """Spec section 10: 'do not force IP location'."""
        locale = resolve(
            self.request(session={SESSION_COUNTRY: "IN"}, HTTP_CF_IPCOUNTRY="US")
        )
        self.assertEqual(locale.country, self.india)
        self.assertFalse(locale.detected)

    def test_a_chosen_currency_survives_a_different_country(self):
        locale = resolve(
            self.request(session={SESSION_COUNTRY: "IN", SESSION_CURRENCY: "USD"})
        )
        self.assertEqual(locale.country, self.india)
        self.assertEqual(locale.currency, self.usd)

    def test_an_unrated_currency_falls_back_instead_of_erroring(self):
        make_currency("GBP", "£")
        locale = resolve(self.request(session={SESSION_CURRENCY: "GBP"}))
        self.assertEqual(locale.currency, self.inr)

    def test_an_inactive_currency_is_ignored(self):
        self.usd.is_active = False
        self.usd.save()
        locale = resolve(self.request(session={SESSION_CURRENCY: "USD"}))
        self.assertEqual(locale.currency, self.inr)

    def test_locale_converts_and_formats(self):
        locale = resolve(self.request(session={SESSION_CURRENCY: "USD"}))
        self.assertEqual(locale.money(Decimal("5000")), Decimal("58.00"))
        self.assertEqual(locale.display(Decimal("5000")), "$58.00")

    def test_middleware_resolves_lazily(self):
        seen = {}

        def view(request):
            seen["has_locale"] = hasattr(request, "locale")
            return "response"

        request = self.request()
        LocaleMiddleware(view)(request)
        self.assertTrue(seen["has_locale"])


class SelectorViewTests(TestCase):
    def setUp(self):
        self.inr = make_currency("INR", "₹", base=True)
        self.usd = make_currency("USD", "$")
        make_rate(self.inr, self.usd, "0.01160")
        Country.objects.create(iso2="IN", name="India", currency=self.inr)
        Country.objects.create(iso2="US", name="United States", currency=self.usd)

    def test_setting_a_country_stores_it_and_its_currency(self):
        response = self.client.post(reverse("geo:set_country"), {"country": "US"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session[SESSION_COUNTRY], "US")
        self.assertEqual(self.client.session[SESSION_CURRENCY], "USD")

    def test_setting_a_currency_stores_it(self):
        response = self.client.post(reverse("geo:set_currency"), {"currency": "USD"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session[SESSION_CURRENCY], "USD")

    def test_an_unknown_currency_is_refused(self):
        self.client.post(reverse("geo:set_currency"), {"currency": "XYZ"})
        self.assertNotIn(SESSION_CURRENCY, self.client.session)

    def test_a_currency_without_a_rate_is_refused(self):
        make_currency("GBP", "£")
        self.client.post(reverse("geo:set_currency"), {"currency": "GBP"})
        self.assertNotIn(SESSION_CURRENCY, self.client.session)

    def test_the_selector_will_not_redirect_off_site(self):
        response = self.client.post(
            reverse("geo:set_currency"),
            {"currency": "USD", "next": "https://evil.example.com/"},
        )
        self.assertEqual(response["Location"], "/")

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(reverse("geo:set_currency")).status_code, 405)


class StorefrontCurrencyTests(TestCase):
    """Prices on real pages must follow the selected currency.

    Guards the split the money tags exist to enforce: catalogue prices
    convert, order totals do not.
    """

    def setUp(self):
        from apps.core.tests.factories import create_category, create_product

        self.inr = make_currency("INR", "₹", base=True)
        self.usd = make_currency("USD", "$")
        make_rate(self.inr, self.usd, "0.01160")
        Country.objects.create(iso2="IN", name="India", currency=self.inr)
        Country.objects.create(iso2="US", name="United States", currency=self.usd)
        category = create_category(name="Sarees")
        self.product = create_product(
            category=category, name="Designer Silk Saree", price=Decimal("5000.00")
        )

    def test_listing_shows_the_base_currency_by_default(self):
        response = self.client.get(reverse("catalog:product_list"))
        self.assertContains(response, "₹5,000.00")

    def test_listing_converts_after_switching_currency(self):
        self.client.post(reverse("geo:set_currency"), {"currency": "USD"})
        response = self.client.get(reverse("catalog:product_list"))
        self.assertContains(response, "$58.00")
        self.assertNotContains(response, "₹5,000.00")

    def test_price_filter_bounds_are_read_in_the_visitors_currency(self):
        """Asking for "under $60" must not filter for under 60 rupees."""
        from apps.catalog.filters import ProductFilterForm
        from apps.geo.locale_context import resolve

        request = RequestFactory().get("/")
        request.session = {SESSION_CURRENCY: "USD"}
        form = ProductFilterForm({"max_price": "60"}, locale=resolve(request))
        self.assertTrue(form.is_valid())
        # 60 USD at 0.0116 is about 5172 INR -- the 5000 product is included.
        self.assertGreater(form.cleaned_data["max_price"], Decimal("5000"))
