"""Product reviews and ratings.

A review is only accepted when the reviewer has a delivered order line for the
product; that check lives in ``apps.reviews.services`` and is enforced by both
the storefront form and the API serializer.
"""
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class ReviewQuerySet(models.QuerySet):
    def approved(self):
        return self.filter(is_approved=True)

    def for_product(self, product):
        return self.filter(product=product).approved()

    def with_author(self):
        return self.select_related("user").prefetch_related("images")


class Review(TimeStampedModel):
    RATING_CHOICES = [(i, f"{i} star{'s' if i > 1 else ''}") for i in range(1, 6)]

    product = models.ForeignKey(
        "catalog.Product", on_delete=models.CASCADE, related_name="reviews", db_index=True
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reviews", db_index=True
    )
    order_item = models.ForeignKey(
        "orders.OrderItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviews",
        help_text=_("The delivered line that entitles this review."),
    )

    rating = models.PositiveSmallIntegerField(
        choices=RATING_CHOICES, validators=[MinValueValidator(1), MaxValueValidator(5)], db_index=True
    )
    title = models.CharField(max_length=150, blank=True)
    comment = models.TextField(blank=True)

    verified_purchase = models.BooleanField(default=False, db_index=True)
    is_approved = models.BooleanField(
        default=True, db_index=True, help_text=_("Uncheck to hide this review from the storefront.")
    )
    helpful_count = models.PositiveIntegerField(default=0)
    staff_reply = models.TextField(blank=True)
    staff_replied_at = models.DateTimeField(null=True, blank=True)

    objects = ReviewQuerySet.as_manager()

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(fields=["product", "user"], name="one_review_per_user_product")
        ]
        indexes = [
            models.Index(fields=["product", "is_approved", "-created_at"], name="review_live_idx"),
            models.Index(fields=["rating"], name="review_rating_idx"),
        ]

    def __str__(self):
        return f"{self.rating}* on {self.product_id} by {self.user_id}"

    @property
    def rating_percent(self):
        return int((self.rating / 5) * 100)

    @property
    def author_name(self):
        name = self.user.get_full_name().strip()
        if name:
            return name
        local = (self.user.email or "customer").split("@")[0]
        return local[:2] + "***"


class ReviewImage(TimeStampedModel):
    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to="reviews/%Y/%m/")
    caption = models.CharField(max_length=150, blank=True)

    class Meta:
        ordering = ("id",)

    def __str__(self):
        return f"Image for review {self.review_id}"


class ReviewHelpfulVote(TimeStampedModel):
    """Prevents a user inflating ``helpful_count`` by clicking repeatedly."""

    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="helpful_votes")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="helpful_votes"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["review", "user"], name="one_helpful_vote_per_user")
        ]

    def __str__(self):
        return f"{self.user_id} found {self.review_id} helpful"
