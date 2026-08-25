"""Authentication, profile and address forms."""
from django import forms
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordChangeForm,
    PasswordResetForm,
    SetPasswordForm,
    UserCreationForm,
)
from django.utils.translation import gettext_lazy as _

from .models import Address, User


def _style(field, placeholder=None, css="form-control"):
    """Apply Bootstrap classes without repeating widget definitions."""
    field.widget.attrs.setdefault("class", css)
    if placeholder:
        field.widget.attrs.setdefault("placeholder", placeholder)
    return field


class RegistrationForm(UserCreationForm):
    """Customer sign-up.

    Builds on ``UserCreationForm`` so Django's password validators and hashing
    apply unchanged -- passwords are never handled manually anywhere here.
    """

    first_name = forms.CharField(max_length=150, label=_("First name"))
    last_name = forms.CharField(max_length=150, required=False, label=_("Last name"))
    email = forms.EmailField(label=_("Email address"))
    phone = forms.CharField(max_length=20, required=False, label=_("Phone number"))
    marketing_opt_in = forms.BooleanField(
        required=False, initial=True, label=_("Email me offers and product updates")
    )
    accept_terms = forms.BooleanField(
        required=True, label=_("I agree to the Terms & Conditions and Privacy Policy")
    )

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "phone")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The username is derived from the email; customers never see it.
        self.fields.pop("username", None)
        placeholders = {
            "first_name": _("Jane"),
            "last_name": _("Doe"),
            "email": _("you@example.com"),
            "phone": _("9876543210"),
            "password1": _("At least 8 characters"),
            "password2": _("Repeat your password"),
        }
        for name, field in self.fields.items():
            if name in {"marketing_opt_in", "accept_terms"}:
                field.widget.attrs.setdefault("class", "form-check-input")
            else:
                _style(field, placeholders.get(name))
        self.fields["password1"].label = _("Password")
        self.fields["password2"].label = _("Confirm password")

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(_("An account with this email already exists."))
        return email

    def clean_phone(self):
        phone = (self.cleaned_data.get("phone") or "").strip()
        if phone and User.objects.filter(phone=phone).exists():
            raise forms.ValidationError(_("An account with this phone number already exists."))
        return phone

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.username = self.cleaned_data["email"]
        user.phone = self.cleaned_data.get("phone", "")
        user.marketing_opt_in = self.cleaned_data.get("marketing_opt_in", False)
        if commit:
            user.save()
        return user


class LoginForm(AuthenticationForm):
    """Email-or-username login."""

    username = forms.CharField(label=_("Email or username"))
    remember_me = forms.BooleanField(required=False, initial=True, label=_("Keep me signed in"))

    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": _("Those credentials do not match an account."),
        "inactive": _("This account has been deactivated."),
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields["username"], _("you@example.com"))
        _style(self.fields["password"], _("Your password"))
        self.fields["remember_me"].widget.attrs["class"] = "form-check-input"
        self.fields["username"].widget.attrs["autofocus"] = True


class ProfileForm(forms.ModelForm):
    """Editable profile fields. Email changes are deliberately excluded."""

    class Meta:
        model = User
        fields = (
            "first_name",
            "last_name",
            "phone",
            "gender",
            "date_of_birth",
            "avatar",
            "marketing_opt_in",
        )
        widgets = {
            "date_of_birth": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name == "marketing_opt_in":
                field.widget.attrs["class"] = "form-check-input"
            elif name == "avatar":
                field.widget.attrs["class"] = "form-control"
            elif name == "gender":
                field.widget.attrs["class"] = "form-select"
            else:
                _style(field)

    def clean_phone(self):
        phone = (self.cleaned_data.get("phone") or "").strip()
        if phone and User.objects.filter(phone=phone).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(_("Another account already uses this phone number."))
        return phone


class StyledPasswordChangeForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            _style(field)


class StyledPasswordResetForm(PasswordResetForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields["email"], _("you@example.com"))


class StyledSetPasswordForm(SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            _style(field)


class AddressForm(forms.ModelForm):
    class Meta:
        model = Address
        fields = (
            "label",
            "full_name",
            "phone",
            "line1",
            "line2",
            "landmark",
            "city",
            "state",
            "country",
            "postal_code",
            "is_default",
        )
        labels = {
            "line1": _("Address line 1"),
            "line2": _("Address line 2 (optional)"),
            "postal_code": _("Pincode"),
            "is_default": _("Use as my default address"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        placeholders = {
            "full_name": _("Recipient name"),
            "phone": _("10-digit mobile number"),
            "line1": _("Flat, house no., building"),
            "line2": _("Area, street, sector"),
            "landmark": _("Near..."),
            "city": _("City"),
            "state": _("State"),
            "country": _("Country"),
            "postal_code": _("560001"),
        }
        for name, field in self.fields.items():
            if name == "is_default":
                field.widget.attrs["class"] = "form-check-input"
            elif name == "label":
                field.widget.attrs["class"] = "form-select"
            else:
                _style(field, placeholders.get(name))

    def clean_postal_code(self):
        code = self.cleaned_data["postal_code"].strip()
        if not code.isdigit() or not (4 <= len(code) <= 10):
            raise forms.ValidationError(_("Enter a valid pincode."))
        return code
