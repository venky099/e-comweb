"""Wishlist behaviour and ownership."""
from django.test import TestCase
from django.urls import reverse

from apps.cart.models import CartItem
from apps.core.tests.factories import create_product, create_user, variant_of
from apps.wishlist.models import Wishlist, WishlistItem


class WishlistTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.product = create_product(price="800.00", stock=5)
        self.client.force_login(self.user)

    def test_a_wishlist_is_created_with_the_account(self):
        self.assertTrue(Wishlist.objects.filter(user=self.user).exists())

    def test_toggle_adds_then_removes(self):
        url = reverse("wishlist:toggle")

        self.client.post(url, {"product_id": self.product.pk})
        self.assertTrue(
            WishlistItem.objects.filter(wishlist__user=self.user, product=self.product).exists()
        )

        self.client.post(url, {"product_id": self.product.pk})
        self.assertFalse(
            WishlistItem.objects.filter(wishlist__user=self.user, product=self.product).exists()
        )

    def test_a_product_cannot_be_saved_twice(self):
        wishlist = Wishlist.for_user(self.user)
        WishlistItem.objects.create(wishlist=wishlist, product=self.product)

        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                WishlistItem.objects.create(wishlist=wishlist, product=self.product)

    def test_move_to_cart(self):
        wishlist = Wishlist.for_user(self.user)
        item = WishlistItem.objects.create(wishlist=wishlist, product=self.product)

        response = self.client.post(reverse("wishlist:move_to_cart", args=[item.pk]))
        self.assertRedirects(response, reverse("cart:detail"))

        self.assertFalse(WishlistItem.objects.filter(pk=item.pk).exists())
        self.assertTrue(CartItem.objects.filter(cart__user=self.user).exists())

    def test_out_of_stock_item_cannot_move_to_cart(self):
        sold_out = create_product(price="100.00", stock=0)
        wishlist = Wishlist.for_user(self.user)
        item = WishlistItem.objects.create(wishlist=wishlist, product=sold_out)

        self.client.post(reverse("wishlist:move_to_cart", args=[item.pk]))
        self.assertTrue(WishlistItem.objects.filter(pk=item.pk).exists())
        self.assertFalse(CartItem.objects.filter(cart__user=self.user).exists())

    def test_move_all_skips_unavailable_items(self):
        wishlist = Wishlist.for_user(self.user)
        WishlistItem.objects.create(wishlist=wishlist, product=self.product)
        WishlistItem.objects.create(
            wishlist=wishlist, product=create_product(price="100.00", stock=0)
        )

        self.client.post(reverse("wishlist:move_all_to_cart"))
        self.assertEqual(CartItem.objects.filter(cart__user=self.user).count(), 1)
        self.assertEqual(WishlistItem.objects.filter(wishlist=wishlist).count(), 1)

    def test_clear_empties_the_wishlist(self):
        wishlist = Wishlist.for_user(self.user)
        WishlistItem.objects.create(wishlist=wishlist, product=self.product)

        self.client.post(reverse("wishlist:clear"))
        self.assertEqual(wishlist.items.count(), 0)

    def test_a_customer_cannot_remove_another_customers_item(self):
        other = create_user()
        item = WishlistItem.objects.create(
            wishlist=Wishlist.for_user(other), product=self.product
        )

        response = self.client.post(reverse("wishlist:remove", args=[item.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(WishlistItem.objects.filter(pk=item.pk).exists())

    def test_wishlist_page_shows_saved_items(self):
        WishlistItem.objects.create(
            wishlist=Wishlist.for_user(self.user), product=self.product
        )
        response = self.client.get(reverse("wishlist:detail"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.product.name)

    def test_empty_wishlist_shows_an_empty_state(self):
        response = self.client.get(reverse("wishlist:detail"))
        self.assertContains(response, "Your wishlist is empty")

    def test_anonymous_toggle_is_redirected_to_login(self):
        self.client.logout()
        response = self.client.post(
            reverse("wishlist:toggle"), {"product_id": self.product.pk}
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])
