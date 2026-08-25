"""Verified-purchase review rules and rating aggregation."""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.cart.models import Cart, CartItem
from apps.core.tests.factories import (
    create_address,
    create_product,
    create_staff,
    create_user,
    variant_of,
)
from apps.orders import services as order_services
from apps.orders.models import Order
from apps.reviews import services as review_services
from apps.reviews.models import Review, ReviewHelpfulVote


def deliver_an_order(user, product, quantity=1):
    """Take a product all the way to Delivered for ``user``."""
    address = create_address(user)
    cart = Cart.objects.create(user=user)
    CartItem.objects.create(cart=cart, variant=variant_of(product), quantity=quantity)
    order = order_services.place_order(user, cart, address, Order.PaymentMethod.COD)

    staff = create_staff(superuser=True)
    for status in (
        Order.Status.CONFIRMED,
        Order.Status.PROCESSING,
        Order.Status.SHIPPED,
        Order.Status.DELIVERED,
    ):
        order_services.transition_order(order, status, user=staff)
    order.refresh_from_db()
    return order


class ReviewEligibilityTests(TestCase):
    def setUp(self):
        self.buyer = create_user()
        self.stranger = create_user()
        self.product = create_product(price="500.00", stock=10)

    def test_customer_who_received_the_product_may_review(self):
        deliver_an_order(self.buyer, self.product)
        allowed, reason = review_services.can_review_product(self.buyer, self.product)
        self.assertTrue(allowed, reason)

    def test_customer_who_never_bought_it_may_not_review(self):
        allowed, reason = review_services.can_review_product(self.stranger, self.product)
        self.assertFalse(allowed)
        self.assertIn("received", str(reason))

    def test_undelivered_order_does_not_grant_a_review(self):
        address = create_address(self.buyer)
        cart = Cart.objects.create(user=self.buyer)
        CartItem.objects.create(cart=cart, variant=variant_of(self.product), quantity=1)
        order_services.place_order(self.buyer, cart, address, Order.PaymentMethod.COD)

        allowed, _reason = review_services.can_review_product(self.buyer, self.product)
        self.assertFalse(allowed)

    def test_a_customer_may_review_a_product_only_once(self):
        deliver_an_order(self.buyer, self.product)
        review_services.create_review(self.buyer, self.product, 5, "Great", "Loved it")

        allowed, reason = review_services.can_review_product(self.buyer, self.product)
        self.assertFalse(allowed)
        self.assertIn("already reviewed", str(reason))

    def test_service_refuses_to_create_an_ineligible_review(self):
        with self.assertRaises(PermissionError):
            review_services.create_review(self.stranger, self.product, 5, "Fake", "Nope")

    def test_created_review_is_marked_verified(self):
        deliver_an_order(self.buyer, self.product)
        review = review_services.create_review(self.buyer, self.product, 4, "Good", "Solid")
        self.assertTrue(review.verified_purchase)
        self.assertIsNotNone(review.order_item)


class ReviewViewTests(TestCase):
    def setUp(self):
        self.buyer = create_user()
        self.product = create_product(price="500.00", stock=10)
        deliver_an_order(self.buyer, self.product)
        self.url = reverse("reviews:write", args=[self.product.slug])

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_eligible_customer_sees_the_form(self):
        self.client.force_login(self.buyer)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your rating")

    def test_ineligible_customer_is_redirected_to_the_product(self):
        self.client.force_login(create_user())
        response = self.client.get(self.url)
        self.assertRedirects(response, self.product.get_absolute_url())

    def test_submitting_a_review(self):
        self.client.force_login(self.buyer)
        response = self.client.post(
            self.url, {"rating": 5, "title": "Excellent", "comment": "Would buy again"}
        )
        self.assertRedirects(response, self.product.get_absolute_url())

        review = Review.objects.get(product=self.product, user=self.buyer)
        self.assertEqual(review.rating, 5)
        self.assertTrue(review.verified_purchase)

    def test_rating_outside_one_to_five_is_rejected(self):
        self.client.force_login(self.buyer)
        response = self.client.post(self.url, {"rating": 9, "comment": "Nonsense"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Review.objects.filter(product=self.product).exists())

    def test_customer_can_delete_only_their_own_review(self):
        self.client.force_login(self.buyer)
        self.client.post(self.url, {"rating": 4, "comment": "Fine"})
        review = Review.objects.get(user=self.buyer)

        self.client.force_login(create_user())
        response = self.client.post(reverse("reviews:delete", args=[review.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Review.objects.filter(pk=review.pk).exists())

        self.client.force_login(self.buyer)
        self.client.post(reverse("reviews:delete", args=[review.pk]))
        self.assertFalse(Review.objects.filter(pk=review.pk).exists())


class RatingAggregationTests(TestCase):
    """Denormalised rating columns must track the approved reviews exactly."""

    def setUp(self):
        self.product = create_product(price="500.00", stock=50)

    def _review(self, rating):
        buyer = create_user()
        deliver_an_order(buyer, self.product)
        return review_services.create_review(buyer, self.product, rating, "T", "C")

    def test_average_and_count_update_on_create(self):
        self._review(5)
        self._review(3)

        self.product.refresh_from_db()
        self.assertEqual(self.product.rating_count, 2)
        self.assertEqual(self.product.rating_average, Decimal("4.00"))

    def test_average_updates_on_delete(self):
        first = self._review(5)
        self._review(1)
        first.delete()

        self.product.refresh_from_db()
        self.assertEqual(self.product.rating_count, 1)
        self.assertEqual(self.product.rating_average, Decimal("1.00"))

    def test_hidden_review_is_excluded_from_the_average(self):
        review = self._review(1)
        self._review(5)

        review.is_approved = False
        review.save(update_fields=["is_approved"])

        self.product.refresh_from_db()
        self.assertEqual(self.product.rating_count, 1)
        self.assertEqual(self.product.rating_average, Decimal("5.00"))

    def test_no_reviews_means_zero(self):
        self.product.refresh_from_db()
        self.assertEqual(self.product.rating_count, 0)
        self.assertEqual(self.product.rating_average, Decimal("0.00"))


class HelpfulVoteTests(TestCase):
    def setUp(self):
        self.author = create_user()
        self.voter = create_user()
        self.product = create_product(price="500.00", stock=10)
        deliver_an_order(self.author, self.product)
        self.review = review_services.create_review(
            self.author, self.product, 5, "Great", "Loved it"
        )

    def test_vote_increments_once_per_user(self):
        self.client.force_login(self.voter)
        url = reverse("reviews:helpful", args=[self.review.pk])

        self.client.post(url)
        self.review.refresh_from_db()
        self.assertEqual(self.review.helpful_count, 1)

        self.client.post(url)  # clicking again must not inflate the count
        self.review.refresh_from_db()
        self.assertEqual(self.review.helpful_count, 1)
        self.assertEqual(ReviewHelpfulVote.objects.filter(review=self.review).count(), 1)

    def test_author_cannot_vote_on_their_own_review(self):
        self.client.force_login(self.author)
        response = self.client.post(reverse("reviews:helpful", args=[self.review.pk]))
        self.assertEqual(response.status_code, 400)

        self.review.refresh_from_db()
        self.assertEqual(self.review.helpful_count, 0)

    def test_anonymous_cannot_vote(self):
        response = self.client.post(reverse("reviews:helpful", args=[self.review.pk]))
        self.assertEqual(response.status_code, 302)


class ReviewApiTests(TestCase):
    def setUp(self):
        self.password = "TestPass!2345"
        self.buyer = create_user(email="reviewer@example.test", password=self.password)
        self.product = create_product(price="500.00", stock=10)
        deliver_an_order(self.buyer, self.product)

        login = self.client.post(
            "/api/v1/auth/login/",
            data={"username": "reviewer@example.test", "password": self.password},
            content_type="application/json",
        )
        self.token = login.json()["access"]

    def _post(self, data, token=None):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return self.client.post(
            "/api/v1/reviews/", data=data, content_type="application/json", **headers
        )

    def test_eligible_customer_can_post_a_review(self):
        response = self._post(
            {"product": self.product.pk, "rating": 5, "title": "Api", "comment": "Great"},
            token=self.token,
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(response.json()["verified_purchase"])

    def test_api_enforces_the_same_eligibility_rule(self):
        other_password = "OtherPass!2345"
        create_user(email="notabuyer@example.test", password=other_password)
        login = self.client.post(
            "/api/v1/auth/login/",
            data={"username": "notabuyer@example.test", "password": other_password},
            content_type="application/json",
        )
        token = login.json()["access"]

        response = self._post(
            {"product": self.product.pk, "rating": 5, "comment": "Fake"}, token=token
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Review.objects.filter(product=self.product).exists())

    def test_anonymous_cannot_post_a_review(self):
        response = self._post({"product": self.product.pk, "rating": 5})
        self.assertIn(response.status_code, (401, 403))

    def test_verified_purchase_cannot_be_forged_by_the_client(self):
        response = self._post(
            {
                "product": self.product.pk,
                "rating": 5,
                "comment": "Real",
                "verified_purchase": False,
                "helpful_count": 9999,
            },
            token=self.token,
        )
        self.assertEqual(response.status_code, 201)
        review = Review.objects.get(product=self.product)
        self.assertTrue(review.verified_purchase)
        self.assertEqual(review.helpful_count, 0)

    def test_review_list_is_public(self):
        review_services.create_review(self.buyer, self.product, 4, "Public", "Visible")
        response = self.client.get("/api/v1/reviews/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)
