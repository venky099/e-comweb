"""Review views."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import F
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from django.views.generic import ListView

from apps.catalog.models import Product
from apps.core.forms import MultipleFileField, MultipleFileInput

from . import services
from .models import Review, ReviewHelpfulVote


class ReviewForm(forms.ModelForm):
    """Star rating + optional title/comment/photos."""

    images = MultipleFileField(
        required=False,
        widget=MultipleFileInput(attrs={"multiple": True, "class": "form-control"}),
        label=_("Add photos (optional)"),
    )

    class Meta:
        model = Review
        fields = ("rating", "title", "comment")
        widgets = {
            "rating": forms.RadioSelect(choices=Review.RATING_CHOICES),
            "title": forms.TextInput(
                attrs={"class": "form-control", "placeholder": _("Sum it up in a line")}
            ),
            "comment": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 4,
                    "placeholder": _("What did you like or dislike?"),
                }
            ),
        }

    def clean_rating(self):
        rating = self.cleaned_data["rating"]
        if not 1 <= int(rating) <= 5:
            raise forms.ValidationError(_("Choose a rating between 1 and 5 stars."))
        return rating


@login_required
def write_review(request, slug):
    """Create a review for a product the customer has received."""
    product = get_object_or_404(Product.objects.published(), slug=slug)

    allowed, reason = services.can_review_product(request.user, product)
    if not allowed:
        messages.error(request, reason)
        return redirect(product.get_absolute_url())

    if request.method == "POST":
        form = ReviewForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                services.create_review(
                    user=request.user,
                    product=product,
                    rating=form.cleaned_data["rating"],
                    title=form.cleaned_data.get("title", ""),
                    comment=form.cleaned_data.get("comment", ""),
                    images=request.FILES.getlist("images"),
                )
            except PermissionError as exc:
                messages.error(request, str(exc))
                return redirect(product.get_absolute_url())

            messages.success(request, _("Thanks for your review!"))
            return redirect(product.get_absolute_url())
        messages.error(request, _("Please correct the errors below."))
    else:
        form = ReviewForm()

    return render(request, "reviews/write.html", {"product": product, "form": form})


class ProductReviewListView(ListView):
    """Full, paginated review list for one product."""

    template_name = "reviews/list.html"
    context_object_name = "reviews"
    paginate_by = 20

    def get_queryset(self):
        self.product = get_object_or_404(Product.objects.published(), slug=self.kwargs["slug"])
        queryset = Review.objects.for_product(self.product).with_author()

        rating = self.request.GET.get("rating")
        if rating and rating.isdigit():
            queryset = queryset.filter(rating=int(rating))
        if self.request.GET.get("verified") == "1":
            queryset = queryset.filter(verified_purchase=True)
        if self.request.GET.get("with_photos") == "1":
            queryset = queryset.filter(images__isnull=False).distinct()

        sort = self.request.GET.get("sort", "recent")
        ordering = {
            "recent": ("-created_at",),
            "helpful": ("-helpful_count", "-created_at"),
            "highest": ("-rating", "-created_at"),
            "lowest": ("rating", "-created_at"),
        }.get(sort, ("-created_at",))
        return queryset.order_by(*ordering)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["product"] = self.product
        context["active_rating"] = self.request.GET.get("rating", "")
        context["sort"] = self.request.GET.get("sort", "recent")
        return context


class MyReviewsView(LoginRequiredMixin, ListView):
    """Reviews the customer has written, plus what is still awaiting one."""

    template_name = "reviews/mine.html"
    context_object_name = "reviews"
    paginate_by = 15

    def get_queryset(self):
        return (
            Review.objects.filter(user=self.request.user)
            .select_related("product")
            .prefetch_related("product__images")
            .order_by("-created_at")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["pending"] = services.pending_reviews_for(self.request.user)
        return context


@login_required
@require_POST
def delete_review(request, pk):
    review = get_object_or_404(Review, pk=pk, user=request.user)
    product_url = review.product.get_absolute_url()
    review.delete()
    messages.success(request, _("Your review has been deleted."))
    return redirect(request.POST.get("next") or product_url)


@login_required
@require_POST
def mark_helpful(request, pk):
    """One helpful vote per user, enforced by a unique constraint."""
    review = get_object_or_404(Review.objects.approved(), pk=pk)

    if review.user_id == request.user.id:
        return JsonResponse(
            {"ok": False, "message": str(_("You cannot vote on your own review."))},
            status=400,
        )

    _vote, created = ReviewHelpfulVote.objects.get_or_create(review=review, user=request.user)
    if created:
        Review.objects.filter(pk=review.pk).update(helpful_count=F("helpful_count") + 1)
        review.refresh_from_db(fields=["helpful_count"])

    if request.headers.get("HX-Request"):
        return render(
            request,
            "reviews/partials/helpful_button.html",
            {"review": review, "voted": True},
        )
    return JsonResponse({"ok": True, "helpful_count": review.helpful_count, "voted": True})
