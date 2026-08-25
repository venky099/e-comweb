"""Authentication and customer account views."""
from django.contrib import messages
from django.contrib.auth import login, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import (
    LoginView,
    LogoutView,
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, ListView, TemplateView, UpdateView
from django_ratelimit.decorators import ratelimit

from apps.orders.models import Order

from .forms import (
    AddressForm,
    LoginForm,
    ProfileForm,
    RegistrationForm,
    StyledPasswordChangeForm,
    StyledPasswordResetForm,
    StyledSetPasswordForm,
)
from .models import Address


def _safe_next(request, fallback):
    """Only follow ``?next=`` when it points back at this site."""
    from django.utils.http import url_has_allowed_host_and_scheme

    target = request.POST.get("next") or request.GET.get("next")
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return fallback


@method_decorator(
    ratelimit(key="ip", rate="10/m", method="POST", block=True), name="dispatch"
)
class RegisterView(CreateView):
    """Customer sign-up, rate limited against automated account creation."""

    form_class = RegistrationForm
    template_name = "accounts/register.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("core:home")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.save()
        # Explicit backend: two are configured, so Django cannot pick for us.
        login(self.request, user, backend="apps.accounts.backends.EmailOrUsernameBackend")
        messages.success(
            self.request, _("Welcome, %(name)s! Your account is ready.") % {"name": user.first_name}
        )
        return redirect(_safe_next(self.request, reverse("core:home")))


@method_decorator(
    ratelimit(key="ip", rate="10/m", method="POST", block=True), name="dispatch"
)
class CustomerLoginView(LoginView):
    """Session login. Rate limited to blunt credential stuffing."""

    form_class = LoginForm
    template_name = "accounts/login.html"
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)
        if not form.cleaned_data.get("remember_me"):
            self.request.session.set_expiry(0)  # expire when the browser closes
        messages.success(self.request, _("Signed in successfully."))
        return response

    def form_invalid(self, form):
        messages.error(self.request, _("Please check your credentials and try again."))
        return super().form_invalid(form)


class CustomerLogoutView(LogoutView):
    next_page = reverse_lazy("core:home")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            messages.info(request, _("You have been signed out."))
        return super().dispatch(request, *args, **kwargs)


# ---------------------------------------------------------------------------
# Password reset (Django's views, wrapped with styled forms + templates)
# ---------------------------------------------------------------------------
class CustomerPasswordResetView(PasswordResetView):
    form_class = StyledPasswordResetForm
    template_name = "accounts/password_reset.html"
    email_template_name = "accounts/emails/password_reset_email.txt"
    subject_template_name = "accounts/emails/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")


class CustomerPasswordResetDoneView(PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


class CustomerPasswordResetConfirmView(PasswordResetConfirmView):
    form_class = StyledSetPasswordForm
    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("accounts:password_reset_complete")


class CustomerPasswordResetCompleteView(PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"


# ---------------------------------------------------------------------------
# Customer dashboard
# ---------------------------------------------------------------------------
class AccountDashboardView(LoginRequiredMixin, TemplateView):
    """Account landing page with order stats and recent activity."""

    template_name = "accounts/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        stats = Order.objects.filter(user=user).aggregate(
            total_orders=Count("id"),
            delivered=Count("id", filter=Q(status=Order.Status.DELIVERED)),
            in_progress=Count(
                "id",
                filter=Q(
                    status__in=[
                        Order.Status.PENDING,
                        Order.Status.CONFIRMED,
                        Order.Status.PROCESSING,
                        Order.Status.SHIPPED,
                        Order.Status.OUT_FOR_DELIVERY,
                    ]
                ),
            ),
            lifetime_value=Sum(
                "total_amount",
                filter=~Q(
                    status__in=[
                        Order.Status.CANCELLED,
                        Order.Status.RETURNED,
                        Order.Status.REFUNDED,
                    ]
                ),
            ),
        )
        context["stats"] = stats
        context["recent_orders"] = (
            Order.objects.filter(user=user)
            .prefetch_related("items")
            .order_by("-placed_at")[:5]
        )
        context["address_count"] = user.addresses.count()
        context["default_address"] = user.default_address()
        return context


class ProfileUpdateView(LoginRequiredMixin, UpdateView):
    form_class = ProfileForm
    template_name = "accounts/profile.html"
    success_url = reverse_lazy("accounts:profile")

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form):
        messages.success(self.request, _("Your profile has been updated."))
        return super().form_valid(form)


@login_required
def change_password(request):
    """Password change that keeps the current session signed in."""
    if request.method == "POST":
        form = StyledPasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            messages.success(request, _("Your password has been changed."))
            return redirect("accounts:profile")
        messages.error(request, _("Please correct the errors below."))
    else:
        form = StyledPasswordChangeForm(request.user)
    return render(request, "accounts/change_password.html", {"form": form})


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------
class AddressListView(LoginRequiredMixin, ListView):
    template_name = "accounts/address_list.html"
    context_object_name = "addresses"

    def get_queryset(self):
        return self.request.user.addresses.all()


class AddressCreateView(LoginRequiredMixin, CreateView):
    form_class = AddressForm
    template_name = "accounts/address_form.html"
    success_url = reverse_lazy("accounts:address_list")

    def form_valid(self, form):
        form.instance.user = self.request.user
        messages.success(self.request, _("Address saved."))
        response = super().form_valid(form)
        return redirect(_safe_next(self.request, str(self.success_url)))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Add a new address")
        return context


class AddressUpdateView(LoginRequiredMixin, UpdateView):
    form_class = AddressForm
    template_name = "accounts/address_form.html"
    success_url = reverse_lazy("accounts:address_list")

    def get_queryset(self):
        # Scoped to the owner: another user's id in the URL 404s.
        return self.request.user.addresses.all()

    def form_valid(self, form):
        messages.success(self.request, _("Address updated."))
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Edit address")
        return context


class AddressDeleteView(LoginRequiredMixin, DeleteView):
    template_name = "accounts/address_confirm_delete.html"
    success_url = reverse_lazy("accounts:address_list")

    def get_queryset(self):
        return self.request.user.addresses.all()

    def form_valid(self, form):
        messages.success(self.request, _("Address removed."))
        return super().form_valid(form)


@login_required
@require_POST
def set_default_address(request, pk):
    address = get_object_or_404(Address, pk=pk, user=request.user)
    address.make_default()
    messages.success(request, _("Default address updated."))
    return redirect("accounts:address_list")
