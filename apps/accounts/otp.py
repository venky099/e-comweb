"""One-time codes for signing in (MST spec section 15).

Codes are stored hashed, never in plain text. A leaked database should not
hand somebody a working sign-in code any more than it hands them a password,
and there is no reason to be able to read a code back -- it is only ever
compared.

Every rule here exists to stop the same attack:

    short lifetime      a code stops working in minutes, not days
    single use          a code that has signed someone in is dead
    attempt limit       six digits is 1,000,000 guesses, so guessing is
                        capped rather than left open
    rate limited issue  requesting codes in a loop is not a way to spam
                        somebody's inbox, or to farm codes
"""
import hashlib
import hmac
import logging
import secrets

from django.conf import settings
from django.db import models, transaction
from django.db.models import F
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

logger = logging.getLogger("ecommerce")

CODE_LENGTH = 6
LIFETIME_MINUTES = 10
MAX_ATTEMPTS = 5
RESEND_COOLDOWN_SECONDS = 60
MAX_PER_HOUR = 5


def generate_code():
    """A numeric code, from a source suitable for secrets."""
    return f"{secrets.randbelow(10 ** CODE_LENGTH):0{CODE_LENGTH}d}"


def hash_code(code, email):
    """Hash a code, salted with the address it was issued to.

    Salting with the address means two people holding the same digits at the
    same moment still have different stored hashes, so a stolen hash cannot
    be replayed against another account.
    """
    material = f"{email.strip().lower()}:{code}".encode()
    return hashlib.sha256(settings.SECRET_KEY.encode() + material).hexdigest()


class OneTimeCode(TimeStampedModel):
    """A short-lived sign-in code issued to an email address."""

    class Purpose(models.TextChoices):
        LOGIN = "login", _("Sign in")
        VERIFY = "verify", _("Verify an address")

    email = models.EmailField(db_index=True)
    code_hash = models.CharField(max_length=64)
    purpose = models.CharField(
        max_length=10, choices=Purpose.choices, default=Purpose.LOGIN
    )
    expires_at = models.DateTimeField(db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    used_at = models.DateTimeField(blank=True, null=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["email", "purpose", "-created_at"])]
        verbose_name = _("one-time code")

    def __str__(self):
        return f"{self.email} ({self.get_purpose_display()})"

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @property
    def is_spent(self):
        return self.used_at is not None or self.attempts >= MAX_ATTEMPTS

    @property
    def is_usable(self):
        return not self.is_expired and not self.is_spent


class OtpError(Exception):
    """Raised when a code cannot be issued or accepted."""


def recent_codes(email, minutes=60):
    since = timezone.now() - timezone.timedelta(minutes=minutes)
    return OneTimeCode.objects.filter(email__iexact=email.strip(), created_at__gte=since)


@transaction.atomic
def issue(email, purpose=OneTimeCode.Purpose.LOGIN, ip_address=None):
    """Create a code and return ``(record, plain_code)``.

    The plain code is returned once, for sending. It is never stored and
    cannot be recovered afterwards.
    """
    email = (email or "").strip().lower()
    if not email:
        raise OtpError(_("Enter your email address."))

    latest = recent_codes(email).first()
    if latest is not None:
        age = (timezone.now() - latest.created_at).total_seconds()
        if age < RESEND_COOLDOWN_SECONDS:
            raise OtpError(
                _("A code was just sent. Please wait a moment before asking for another.")
            )

    if recent_codes(email).count() >= MAX_PER_HOUR:
        raise OtpError(
            _("Too many codes have been requested for this address. Try again later.")
        )

    # Any earlier code stops working the moment a new one is issued, so a
    # code read from an old email cannot be used after a resend.
    OneTimeCode.objects.filter(
        email__iexact=email, purpose=purpose, used_at__isnull=True
    ).update(used_at=timezone.now())

    code = generate_code()
    record = OneTimeCode.objects.create(
        email=email,
        code_hash=hash_code(code, email),
        purpose=purpose,
        expires_at=timezone.now() + timezone.timedelta(minutes=LIFETIME_MINUTES),
        ip_address=ip_address,
    )
    return record, code


def verify(email, code, purpose=OneTimeCode.Purpose.LOGIN):
    """Check a code. Returns the record on success, raises otherwise.

    Deliberately *not* wrapped in a transaction. Every failure path raises,
    and a raise inside an atomic block rolls back the write that recorded the
    attempt -- which would leave the guess counter permanently at zero and
    the attempt limit doing nothing at all. Counting is therefore committed
    on its own, before the comparison that may reject.
    """
    email = (email or "").strip().lower()
    code = (code or "").strip()
    if not code:
        raise OtpError(_("Enter the code we sent you."))

    record = (
        OneTimeCode.objects
        .filter(email__iexact=email, purpose=purpose, used_at__isnull=True)
        .order_by("-created_at")
        .first()
    )
    if record is None:
        # Deliberately the same message as a wrong code: saying "no code was
        # requested for this address" tells a stranger whether it is in use.
        raise OtpError(_("That code is not valid. Please request a new one."))

    if record.is_expired:
        raise OtpError(_("That code has expired. Please request a new one."))

    # Count the attempt with an UPDATE rather than an instance save, so two
    # simultaneous guesses cannot both read the same count and write it back.
    OneTimeCode.objects.filter(pk=record.pk).update(attempts=F("attempts") + 1)
    record.refresh_from_db(fields=["attempts"])

    # Constant-time comparison: a timing difference leaks how much of a code
    # was right, which turns guessing into something far cheaper.
    if not hmac.compare_digest(record.code_hash, hash_code(code, email)):
        # Burn the code on the last allowed attempt, so exactly MAX_ATTEMPTS
        # guesses are possible and the next request finds nothing usable.
        if record.attempts >= MAX_ATTEMPTS:
            OneTimeCode.objects.filter(pk=record.pk).update(used_at=timezone.now())
        raise OtpError(_("That code is not valid. Please check and try again."))

    OneTimeCode.objects.filter(pk=record.pk).update(used_at=timezone.now())
    record.refresh_from_db(fields=["used_at"])
    return record


def purge_expired(older_than_hours=24):
    """Delete spent and expired codes. Safe to run from a scheduled job."""
    cutoff = timezone.now() - timezone.timedelta(hours=older_than_hours)
    deleted, _details = OneTimeCode.objects.filter(created_at__lt=cutoff).delete()
    return deleted
