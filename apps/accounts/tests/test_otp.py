"""One-time sign-in codes (MST spec section 15).

Almost every test here is a security property. A sign-in code is a
credential, and the ways it goes wrong are all quiet: codes readable in the
database, codes that outlive their use, codes that can be guessed, and pages
that tell a stranger which addresses have accounts.
"""
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts import otp
from apps.accounts.otp import OneTimeCode
from apps.core.tests.factories import create_user

EMAIL = "buyer@example.test"


class IssueTests(TestCase):
    def test_a_code_is_six_digits(self):
        _record, code = otp.issue(EMAIL)
        self.assertEqual(len(code), otp.CODE_LENGTH)
        self.assertTrue(code.isdigit())

    def test_the_code_is_never_stored_in_plain_text(self):
        record, code = otp.issue(EMAIL)
        self.assertNotIn(code, record.code_hash)
        self.assertNotEqual(record.code_hash, code)
        self.assertEqual(len(record.code_hash), 64)

    def test_the_hash_is_salted_with_the_address(self):
        """The same digits for two people must not share a stored hash."""
        self.assertNotEqual(
            otp.hash_code("123456", "a@example.test"),
            otp.hash_code("123456", "b@example.test"),
        )

    def test_issuing_a_new_code_kills_the_previous_one(self):
        _first, first_code = otp.issue(EMAIL)
        OneTimeCode.objects.update(created_at=timezone.now() - timezone.timedelta(minutes=5))
        otp.issue(EMAIL)
        with self.assertRaises(otp.OtpError):
            otp.verify(EMAIL, first_code)

    def test_asking_twice_in_a_row_is_refused(self):
        otp.issue(EMAIL)
        with self.assertRaises(otp.OtpError):
            otp.issue(EMAIL)

    def test_an_hourly_cap_applies(self):
        for _ in range(otp.MAX_PER_HOUR):
            otp.issue(EMAIL)
            OneTimeCode.objects.update(
                created_at=timezone.now() - timezone.timedelta(minutes=2)
            )
        with self.assertRaises(otp.OtpError):
            otp.issue(EMAIL)

    def test_an_empty_address_is_refused(self):
        with self.assertRaises(otp.OtpError):
            otp.issue("  ")


class VerifyTests(TestCase):
    def setUp(self):
        self.record, self.code = otp.issue(EMAIL)

    def test_the_right_code_is_accepted(self):
        record = otp.verify(EMAIL, self.code)
        self.assertIsNotNone(record.used_at)

    def test_a_code_works_only_once(self):
        otp.verify(EMAIL, self.code)
        with self.assertRaises(otp.OtpError):
            otp.verify(EMAIL, self.code)

    def test_a_wrong_code_is_refused(self):
        with self.assertRaises(otp.OtpError):
            otp.verify(EMAIL, "000000")

    def test_an_expired_code_is_refused(self):
        OneTimeCode.objects.update(
            expires_at=timezone.now() - timezone.timedelta(minutes=1)
        )
        with self.assertRaises(otp.OtpError):
            otp.verify(EMAIL, self.code)

    def test_guessing_is_capped(self):
        for _ in range(otp.MAX_ATTEMPTS):
            with self.assertRaises(otp.OtpError):
                otp.verify(EMAIL, "000000")
        # The code is now spent, so even the right one no longer works.
        with self.assertRaises(otp.OtpError):
            otp.verify(EMAIL, self.code)

    def test_a_code_issued_to_someone_else_does_not_work(self):
        with self.assertRaises(otp.OtpError):
            otp.verify("other@example.test", self.code)

    def test_an_unknown_address_gives_the_same_message_as_a_wrong_code(self):
        """Otherwise the error tells a stranger which addresses exist."""
        with self.assertRaises(otp.OtpError) as unknown:
            otp.verify("nobody@example.test", "123456")
        with self.assertRaises(otp.OtpError) as wrong:
            otp.verify(EMAIL, "000000")
        self.assertIn("not valid", str(unknown.exception))
        self.assertIn("not valid", str(wrong.exception))

    def test_the_address_is_matched_case_insensitively(self):
        record = otp.verify(EMAIL.upper(), self.code)
        self.assertIsNotNone(record.used_at)


class HousekeepingTests(TestCase):
    def test_old_codes_are_purged(self):
        otp.issue(EMAIL)
        OneTimeCode.objects.update(
            created_at=timezone.now() - timezone.timedelta(days=2)
        )
        self.assertEqual(otp.purge_expired(), 1)
        self.assertEqual(OneTimeCode.objects.count(), 0)

    def test_recent_codes_are_kept(self):
        otp.issue(EMAIL)
        self.assertEqual(otp.purge_expired(), 0)


@override_settings(RATELIMIT_ENABLE=False)
class SignInFlowTests(TestCase):
    def setUp(self):
        self.user = create_user(email=EMAIL)
        mail.outbox.clear()

    def request_code(self, email=EMAIL):
        return self.client.post(reverse("accounts:otp_request"), {"email": email})

    def test_requesting_a_code_emails_it(self):
        response = self.request_code()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("code", mail.outbox[0].subject.lower())

    def test_an_unknown_address_is_answered_identically_but_sends_nothing(self):
        response = self.request_code("stranger@example.test")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 0)

    def test_the_code_page_needs_an_address_in_the_session(self):
        response = self.client.get(reverse("accounts:otp_verify"))
        self.assertRedirects(response, reverse("accounts:otp_request"))

    def test_a_valid_code_signs_the_customer_in(self):
        self.request_code()
        code = mail.outbox[0].body
        digits = "".join(c for c in code if c.isdigit())[-otp.CODE_LENGTH:]

        record = OneTimeCode.objects.filter(email=EMAIL).first()
        self.assertIsNotNone(record)

        # Read the code from the email the customer actually received.
        import re

        match = re.search(r"\b(\d{6})\b", mail.outbox[0].body)
        self.assertIsNotNone(match, "the email should contain the code")

        response = self.client.post(
            reverse("accounts:otp_verify"), {"code": match.group(1)}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    def test_a_wrong_code_does_not_sign_anyone_in(self):
        self.request_code()
        self.client.post(reverse("accounts:otp_verify"), {"code": "000000"})
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_an_inactive_account_cannot_sign_in(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.request_code()
        self.assertEqual(len(mail.outbox), 0)
