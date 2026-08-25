"""Custom user, profile and address models."""
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

phone_validator = RegexValidator(
    regex=r"^\+?[0-9]{7,15}$",
    message=_("Enter a valid phone number (7-15 digits, optional leading +)."),
)


class UserManager(BaseUserManager):
    """Manager for the custom user model.

    Email is the real identifier for customers, so it is required and
    normalised here rather than left to the form layer.
    """

    use_in_migrations = True

    def _create_user(self, username, email, password, **extra_fields):
        if not email:
            raise ValueError("Users must have an email address.")
        email = self.normalize_email(email).lower()
        username = username or email
        user = self.model(username=username, email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, username=None, email=None, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(username, email, password, **extra_fields)

    def create_superuser(self, username=None, email=None, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("email_verified", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(username, email, password, **extra_fields)


class User(AbstractUser):
    """Customer / staff account.

    Extends ``AbstractUser`` rather than replacing it so Django's auth views,
    permissions and admin keep working unchanged.
    """

    class Gender(models.TextChoices):
        MALE = "male", _("Male")
        FEMALE = "female", _("Female")
        OTHER = "other", _("Other")
        UNDISCLOSED = "undisclosed", _("Prefer not to say")

    email = models.EmailField(_("email address"), unique=True, db_index=True)
    phone = models.CharField(
        max_length=20, blank=True, validators=[phone_validator], db_index=True
    )
    gender = models.CharField(
        max_length=16, choices=Gender.choices, default=Gender.UNDISCLOSED, blank=True
    )
    date_of_birth = models.DateField(null=True, blank=True)
    avatar = models.ImageField(upload_to="avatars/%Y/%m/", blank=True, null=True)
    email_verified = models.BooleanField(default=False)
    phone_verified = models.BooleanField(default=False)
    marketing_opt_in = models.BooleanField(
        default=True, help_text=_("Receive offers and product updates by email.")
    )
    last_seen_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ["email"]

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["email"], name="user_email_idx"),
            models.Index(fields=["is_active", "-created_at"], name="user_active_created_idx"),
        ]

    def __str__(self):
        return self.get_display_name()

    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.strip().lower()
        if not self.username:
            self.username = self.email
        super().save(*args, **kwargs)

    def get_display_name(self):
        full = self.get_full_name().strip()
        return full or self.email or self.username

    @property
    def initials(self):
        parts = [p for p in (self.first_name, self.last_name) if p]
        if parts:
            return "".join(p[0] for p in parts).upper()
        return (self.email or "?")[0].upper()

    def default_address(self):
        """Preferred shipping address, or the most recent one."""
        return (
            self.addresses.filter(is_default=True).first()
            or self.addresses.order_by("-created_at").first()
        )

    def touch_last_seen(self):
        User.objects.filter(pk=self.pk).update(last_seen_at=timezone.now())


class Address(TimeStampedModel):
    """A saved shipping/billing address belonging to a user."""

    class AddressType(models.TextChoices):
        HOME = "home", _("Home")
        WORK = "work", _("Work")
        OTHER = "other", _("Other")

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="addresses", db_index=True
    )
    label = models.CharField(
        max_length=16, choices=AddressType.choices, default=AddressType.HOME
    )
    full_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20, validators=[phone_validator])
    line1 = models.CharField(_("address line 1"), max_length=255)
    line2 = models.CharField(_("address line 2"), max_length=255, blank=True)
    landmark = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, db_index=True)
    state = models.CharField(max_length=100, db_index=True)
    country = models.CharField(max_length=100, default="India")
    postal_code = models.CharField(_("pincode"), max_length=16, db_index=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        verbose_name = _("address")
        verbose_name_plural = _("addresses")
        ordering = ("-is_default", "-created_at")
        indexes = [models.Index(fields=["user", "-is_default"], name="address_user_default_idx")]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(is_default=True),
                name="unique_default_address_per_user",
            )
        ]

    def __str__(self):
        return f"{self.full_name}, {self.city} {self.postal_code}"

    def save(self, *args, **kwargs):
        # First address a user saves becomes the default automatically.
        if not self.pk and not Address.objects.filter(user=self.user).exists():
            self.is_default = True
        if self.is_default:
            Address.objects.filter(user=self.user, is_default=True).exclude(
                pk=self.pk
            ).update(is_default=False)
        super().save(*args, **kwargs)

    def make_default(self):
        Address.objects.filter(user=self.user).update(is_default=False)
        Address.objects.filter(pk=self.pk).update(is_default=True)

    @property
    def single_line(self):
        parts = [self.line1, self.line2, self.landmark, self.city, self.state, self.country]
        return ", ".join(p for p in parts if p) + f" - {self.postal_code}"

    def as_snapshot(self):
        """Flat dict copied onto an Order so later edits never rewrite history."""
        return {
            "full_name": self.full_name,
            "phone": self.phone,
            "line1": self.line1,
            "line2": self.line2,
            "landmark": self.landmark,
            "city": self.city,
            "state": self.state,
            "country": self.country,
            "postal_code": self.postal_code,
        }
