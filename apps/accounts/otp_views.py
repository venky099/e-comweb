"""Signing in with a one-time code (MST spec section 15).

Two steps: ask for a code, then enter it. The address is carried in the
session between them rather than in the URL, so a code page cannot be linked
to with somebody else's address filled in.
"""
import logging

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit

from apps.accounts import otp
from apps.notifications import services as notification_services

logger = logging.getLogger("ecommerce")

SESSION_EMAIL = "otp_email"


class RequestCodeForm(forms.Form):
    email = forms.EmailField(
        label=_("Email address"),
        widget=forms.EmailInput(attrs={"class": "form-control", "autocomplete": "email"}),
    )


class EnterCodeForm(forms.Form):
    code = forms.CharField(
        label=_("Six-digit code"),
        max_length=otp.CODE_LENGTH,
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-lg text-center",
                "inputmode": "numeric",
                "autocomplete": "one-time-code",
                "pattern": "[0-9]*",
            }
        ),
    )


@ratelimit(key="ip", rate="10/h", method="POST", block=True)
@require_http_methods(["GET", "POST"])
def request_code(request):
    form = RequestCodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].lower()
        try:
            _record, code = otp.issue(email, ip_address=request.META.get("REMOTE_ADDR"))
        except otp.OtpError as exc:
            messages.error(request, str(exc))
        else:
            # Only send to an address that has an account, but always say the
            # same thing: whether an address is registered is not something a
            # stranger should be able to test.
            if get_user_model().objects.filter(email__iexact=email, is_active=True).exists():
                notification_services.send(
                    "login_code",
                    to=email,
                    context={"code": code, "minutes": otp.LIFETIME_MINUTES,
                             "subject": _("Your sign-in code")},
                )
            request.session[SESSION_EMAIL] = email
            messages.info(
                request,
                _("If that address has an account, a code is on its way. It expires in %(n)d minutes.")
                % {"n": otp.LIFETIME_MINUTES},
            )
            return redirect("accounts:otp_verify")

    return render(request, "accounts/otp_request.html", {"form": form})


@ratelimit(key="ip", rate="20/h", method="POST", block=True)
@require_http_methods(["GET", "POST"])
def verify_code(request):
    email = request.session.get(SESSION_EMAIL)
    if not email:
        messages.info(request, _("Start by entering your email address."))
        return redirect("accounts:otp_request")

    form = EnterCodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            otp.verify(email, form.cleaned_data["code"])
        except otp.OtpError as exc:
            messages.error(request, str(exc))
        else:
            user = get_user_model().objects.filter(email__iexact=email, is_active=True).first()
            if user is None:
                # A valid code for an address with no account. Say nothing
                # useful; there is nothing to sign in to.
                messages.error(request, _("That code is not valid. Please request a new one."))
                return redirect("accounts:otp_request")

            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            request.session.pop(SESSION_EMAIL, None)
            messages.success(request, _("Signed in."))
            return redirect("core:home")

    return render(
        request, "accounts/otp_verify.html", {"form": form, "email": email}
    )
