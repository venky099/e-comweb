"""Authentication backends."""
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q

User = get_user_model()


class EmailOrUsernameBackend(ModelBackend):
    """Let customers sign in with either their email address or username.

    Customers think in email addresses; staff accounts created via
    ``createsuperuser`` think in usernames. Supporting both avoids a second
    login form.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        identifier = username or kwargs.get("email")
        if identifier is None or password is None:
            return None

        identifier = identifier.strip()
        try:
            user = User.objects.get(
                Q(username__iexact=identifier) | Q(email__iexact=identifier)
            )
        except User.DoesNotExist:
            # Run the hasher anyway so a missing account and a wrong password
            # take the same amount of time.
            User().set_password(password)
            return None
        except User.MultipleObjectsReturned:
            # Extremely unlikely (email is unique); prefer the exact email hit.
            user = User.objects.filter(email__iexact=identifier).order_by("id").first()
            if user is None:
                return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
