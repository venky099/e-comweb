"""Guards against template mistakes that render as visible text.

These are the failures that slip past status-code smoke tests: the page still
returns 200, it just has raw template syntax printed on it.
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from apps.core.tests.factories import create_category, create_product

TEMPLATE_DIR = Path(settings.BASE_DIR) / "templates"


def template_files():
    return sorted(TEMPLATE_DIR.rglob("*.html"))


class TemplateSyntaxGuardTests(TestCase):
    def test_no_multiline_hash_comments(self):
        """``{# ... #}`` is single-line only in Django.

        Spanning one across lines does not comment anything out -- the text
        renders verbatim on the page. Use ``{% comment %}`` for prose.
        """
        offenders = []
        for path in template_files():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "{#" in line and "#}" not in line:
                    offenders.append(f"{path.relative_to(TEMPLATE_DIR)}:{number}: {line.strip()[:70]}")

        self.assertEqual(
            offenders,
            [],
            "Multi-line {# #} comments render as visible text. Use "
            "{% comment %}...{% endcomment %} instead:\n  " + "\n  ".join(offenders),
        )

    def test_comment_tags_are_balanced(self):
        offenders = []
        for path in template_files():
            body = path.read_text(encoding="utf-8")
            opens = len(re.findall(r"{%\s*comment\s*%}", body))
            closes = len(re.findall(r"{%\s*endcomment\s*%}", body))
            if opens != closes:
                offenders.append(f"{path.relative_to(TEMPLATE_DIR)}: {opens} open, {closes} close")
        self.assertEqual(offenders, [], "Unbalanced comment tags:\n  " + "\n  ".join(offenders))

    # Block/endblock balance is deliberately not checked here: Django raises
    # TemplateSyntaxError for that at parse time, and RenderedOutputTests below
    # exercises every page, so an imbalance fails loudly on its own.


class RenderedOutputTests(TestCase):
    """Fetch the real pages and assert no template syntax leaked into them."""

    @classmethod
    def setUpTestData(cls):
        category = create_category(name="Guarded")
        cls.product = create_product(category=category, name="Guarded Product", stock=5)

    def assert_clean(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        body = response.content.decode()

        for marker in ("{#", "#}", "{% comment %}", "{% endcomment %}", "{{", "{%"):
            self.assertNotIn(
                marker,
                body,
                f"{url} rendered raw template syntax {marker!r} -- "
                "something is being printed instead of executed.",
            )

    def test_public_pages_render_without_template_syntax(self):
        urls = [
            reverse("core:home"),
            reverse("catalog:product_list"),
            reverse("catalog:search") + "?q=guarded",
            self.product.get_absolute_url(),
            reverse("cart:detail"),
            reverse("accounts:login"),
            reverse("accounts:register"),
            reverse("core:about"),
            reverse("core:contact"),
            reverse("core:policy", args=["returns"]),
            reverse("catalog:brand_list"),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assert_clean(url)

    def test_authenticated_pages_render_without_template_syntax(self):
        from apps.core.tests.factories import create_address, create_user

        user = create_user()
        create_address(user)
        self.client.force_login(user)

        for name in (
            "accounts:dashboard",
            "accounts:profile",
            "accounts:address_list",
            "wishlist:detail",
            "orders:list",
            "orders:returns",
            "reviews:mine",
        ):
            with self.subTest(view=name):
                self.assert_clean(reverse(name))

    def test_payment_page_renders_without_template_syntax(self):
        """The mock-gateway page carries prose that once leaked as raw text."""
        from apps.cart.models import Cart, CartItem
        from apps.core.tests.factories import create_address, create_user
        from apps.orders import services
        from apps.orders.models import Order

        user = create_user()
        address = create_address(user)
        cart = Cart.objects.create(user=user)
        CartItem.objects.create(cart=cart, variant=self.product.variants.first(), quantity=1)
        order = services.place_order(user, cart, address, Order.PaymentMethod.UPI)

        self.client.force_login(user)
        self.assert_clean(reverse("payments:start", args=[order.order_number]))
