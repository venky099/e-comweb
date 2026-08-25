"""Authentication and account-area tests."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.core.tests.factories import create_address, create_staff, create_user

User = get_user_model()


class RegistrationTests(TestCase):
    url = None

    def setUp(self):
        self.url = reverse("accounts:register")

    def test_registration_page_renders(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Create your account")

    def test_valid_registration_creates_user_and_signs_in(self):
        response = self.client.post(
            self.url,
            {
                "first_name": "Nadia",
                "last_name": "Rao",
                "email": "Nadia.Rao@Example.com",
                "phone": "9876500001",
                "password1": "SunnyRiver!2345",
                "password2": "SunnyRiver!2345",
                "accept_terms": "on",
                "marketing_opt_in": "on",
            },
        )
        self.assertRedirects(response, reverse("core:home"))

        user = User.objects.get(email="nadia.rao@example.com")  # normalised
        self.assertEqual(user.username, "nadia.rao@example.com")
        self.assertTrue(user.check_password("SunnyRiver!2345"))
        # Password must never be stored in the clear. (The stored value is
        # "<algorithm>$<iterations>$<salt>$<hash>"; the algorithm itself
        # differs between the test and production hasher settings.)
        self.assertNotEqual(user.password, "SunnyRiver!2345")
        self.assertNotIn("SunnyRiver", user.password)
        self.assertIn("$", user.password)

    def test_registration_rejects_duplicate_email(self):
        create_user(email="taken@example.test")
        response = self.client.post(
            self.url,
            {
                "first_name": "Copy",
                "email": "taken@example.test",
                "password1": "SunnyRiver!2345",
                "password2": "SunnyRiver!2345",
                "accept_terms": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already exists")
        self.assertEqual(User.objects.filter(email="taken@example.test").count(), 1)

    def test_registration_rejects_mismatched_passwords(self):
        response = self.client.post(
            self.url,
            {
                "first_name": "Mismatch",
                "email": "mismatch@example.test",
                "password1": "SunnyRiver!2345",
                "password2": "DifferentPass!99",
                "accept_terms": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="mismatch@example.test").exists())

    def test_registration_enforces_password_validators(self):
        response = self.client.post(
            self.url,
            {
                "first_name": "Weak",
                "email": "weak@example.test",
                "password1": "12345678",
                "password2": "12345678",
                "accept_terms": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="weak@example.test").exists())

    def test_registration_requires_terms(self):
        response = self.client.post(
            self.url,
            {
                "first_name": "NoTerms",
                "email": "noterms@example.test",
                "password1": "SunnyRiver!2345",
                "password2": "SunnyRiver!2345",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="noterms@example.test").exists())


class LoginTests(TestCase):
    def setUp(self):
        self.password = "TestPass!2345"
        self.user = create_user(email="signin@example.test", password=self.password)
        self.url = reverse("accounts:login")

    def test_login_with_email(self):
        response = self.client.post(
            self.url, {"username": "signin@example.test", "password": self.password}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    def test_login_with_username(self):
        response = self.client.post(
            self.url, {"username": self.user.username, "password": self.password}
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)

    def test_login_is_case_insensitive_on_email(self):
        response = self.client.post(
            self.url, {"username": "SIGNIN@EXAMPLE.TEST", "password": self.password}
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)

    def test_invalid_password_is_rejected(self):
        response = self.client.post(
            self.url, {"username": "signin@example.test", "password": "wrong-password"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_unknown_email_is_rejected(self):
        response = self.client.post(
            self.url, {"username": "nobody@example.test", "password": self.password}
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_inactive_account_cannot_sign_in(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.client.post(
            self.url, {"username": "signin@example.test", "password": self.password}
        )
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_logout(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("accounts:logout"))
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)


class ProtectedViewTests(TestCase):
    """Every customer-area view must require a signed-in user."""

    PROTECTED = [
        "accounts:dashboard",
        "accounts:profile",
        "accounts:change_password",
        "accounts:address_list",
        "accounts:address_create",
        "wishlist:detail",
        "orders:list",
        "orders:returns",
        "reviews:mine",
    ]

    def test_anonymous_is_redirected_to_login(self):
        for name in self.PROTECTED:
            with self.subTest(view=name):
                url = reverse(name)
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302, name)
                self.assertIn(reverse("accounts:login"), response["Location"])

    def test_signed_in_user_can_reach_account_pages(self):
        user = create_user()
        create_address(user)
        self.client.force_login(user)
        for name in self.PROTECTED:
            with self.subTest(view=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200, name)


class AddressOwnershipTests(TestCase):
    """A customer must never reach another customer's address."""

    def setUp(self):
        self.owner = create_user()
        self.other = create_user()
        self.address = create_address(self.owner)

    def test_owner_can_edit(self):
        self.client.force_login(self.owner)
        response = self.client.get(
            reverse("accounts:address_edit", args=[self.address.pk])
        )
        self.assertEqual(response.status_code, 200)

    def test_other_user_gets_404(self):
        self.client.force_login(self.other)
        for name in ("accounts:address_edit", "accounts:address_delete"):
            with self.subTest(view=name):
                response = self.client.get(reverse(name, args=[self.address.pk]))
                self.assertEqual(response.status_code, 404)

    def test_setting_default_moves_the_flag(self):
        second = create_address(self.owner, full_name="Second Address")
        self.client.force_login(self.owner)
        self.client.post(reverse("accounts:address_set_default", args=[second.pk]))

        self.address.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(self.address.is_default)
        self.assertTrue(second.is_default)

    def test_first_address_becomes_default_automatically(self):
        fresh_user = create_user()
        address = create_address(fresh_user)
        self.assertTrue(address.is_default)


class StaffAccessTests(TestCase):
    """Customers must be kept out of staff surfaces."""

    def setUp(self):
        self.customer = create_user()
        self.staff = create_staff(superuser=True)

    def test_customer_cannot_open_staff_dashboard(self):
        self.client.force_login(self.customer)
        response = self.client.get(reverse("dashboard:index"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("admin", response["Location"])

    def test_customer_cannot_open_django_admin(self):
        self.client.force_login(self.customer)
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 302)

    def test_staff_can_open_dashboard_and_admin(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("dashboard:index")).status_code, 200)
        self.assertEqual(self.client.get(reverse("dashboard:reports")).status_code, 200)
        self.assertEqual(self.client.get("/admin/").status_code, 200)
