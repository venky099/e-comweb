"""Support tickets (MST spec section 51).

Three properties do most of the work: whose turn it is moves correctly,
internal notes never reach the customer, and nobody can read a ticket that is
not theirs.
"""
from django.test import TestCase
from django.urls import reverse

from apps.core.tests.factories import create_staff, create_user
from apps.support import services
from apps.support.models import Ticket, TicketMessage


class TicketTestCase(TestCase):
    def setUp(self):
        self.customer = create_user(email="buyer@example.test")
        self.agent = create_staff(email="agent@example.test")

    def open(self, **kwargs):
        return services.open_ticket(
            kwargs.pop("user", self.customer),
            subject=kwargs.pop("subject", "Where is my saree?"),
            body=kwargs.pop("body", "It has not arrived."),
            **kwargs,
        )


class OpeningTests(TicketTestCase):
    def test_a_ticket_starts_with_the_customers_message(self):
        ticket = self.open()
        self.assertEqual(ticket.messages.count(), 1)
        self.assertFalse(ticket.messages.first().is_staff_reply)

    def test_a_new_ticket_is_waiting_on_us(self):
        self.assertEqual(self.open().status, Ticket.Status.AWAITING_STAFF)

    def test_the_reference_uses_the_document_series(self):
        prefix, kind, _year, _seq = self.open().reference.split("-")
        self.assertEqual((prefix, kind), ("MST", "SUP"))

    def test_references_do_not_repeat(self):
        first, second = self.open(), self.open()
        self.assertNotEqual(first.reference, second.reference)

    def test_an_empty_subject_is_refused(self):
        with self.assertRaises(services.SupportError):
            self.open(subject="   ")

    def test_an_empty_message_is_refused(self):
        with self.assertRaises(services.SupportError):
            self.open(body="")


class ReplyTests(TicketTestCase):
    def setUp(self):
        super().setUp()
        self.ticket = self.open()

    def test_a_staff_reply_puts_the_ball_with_the_customer(self):
        services.add_message(self.ticket, self.agent, "It ships tomorrow.")
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, Ticket.Status.AWAITING_CUSTOMER)

    def test_a_customer_reply_puts_it_back_with_us(self):
        services.add_message(self.ticket, self.agent, "It ships tomorrow.")
        services.add_message(self.ticket, self.customer, "Thank you.")
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, Ticket.Status.AWAITING_STAFF)

    def test_an_empty_reply_is_refused(self):
        with self.assertRaises(services.SupportError):
            services.add_message(self.ticket, self.customer, "  ")

    def test_a_closed_ticket_cannot_be_replied_to(self):
        services.set_status(self.ticket, Ticket.Status.CLOSED)
        with self.assertRaises(services.SupportError):
            services.add_message(self.ticket, self.customer, "One more thing")

    def test_resolving_stamps_the_time(self):
        services.set_status(self.ticket, Ticket.Status.RESOLVED)
        self.ticket.refresh_from_db()
        self.assertIsNotNone(self.ticket.resolved_at)

    def test_a_staff_reply_notifies_the_customer(self):
        from apps.notifications.models import Notification

        services.add_message(self.ticket, self.agent, "On its way.")
        self.assertTrue(
            Notification.objects.filter(user=self.customer).exists()
        )


class InternalNoteTests(TicketTestCase):
    def setUp(self):
        super().setUp()
        self.ticket = self.open()
        self.note = services.add_message(
            self.ticket, self.agent, "Refund approved by finance.", is_internal_note=True
        )

    def test_the_customer_never_sees_an_internal_note(self):
        visible = services.visible_messages(self.ticket, self.customer)
        self.assertNotIn(self.note, visible)

    def test_staff_do_see_it(self):
        self.assertIn(self.note, services.visible_messages(self.ticket, self.agent))

    def test_a_note_does_not_change_whose_turn_it_is(self):
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, Ticket.Status.AWAITING_STAFF)

    def test_a_customer_cannot_write_an_internal_note(self):
        message = services.add_message(
            self.ticket, self.customer, "sneaky", is_internal_note=True
        )
        self.assertFalse(message.is_internal_note)


class AccessTests(TicketTestCase):
    def setUp(self):
        super().setUp()
        self.ticket = self.open()
        self.url = self.ticket.get_absolute_url()

    def test_the_owner_can_read_it(self):
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_another_customer_cannot(self):
        self.client.force_login(create_user(email="nosy@example.test"))
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_staff_can(self):
        self.client.force_login(self.agent)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_signing_in_is_required(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_the_list_shows_only_your_own_tickets(self):
        other = create_user(email="other@example.test")
        services.open_ticket(other, subject="Theirs", body="Hello")

        self.client.force_login(self.customer)
        response = self.client.get(reverse("support:list"))
        self.assertContains(response, self.ticket.reference)
        self.assertNotContains(response, "Theirs")

    def test_a_ticket_can_be_opened_through_the_form(self):
        self.client.force_login(self.customer)
        response = self.client.post(
            reverse("support:create"),
            {"topic": Ticket.Topic.DELIVERY, "subject": "Late parcel", "body": "Still waiting."},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Ticket.objects.filter(subject="Late parcel").exists())

    def test_an_internal_note_is_absent_from_the_customers_page(self):
        services.add_message(self.ticket, self.agent, "Do not refund yet.", is_internal_note=True)
        self.client.force_login(self.customer)
        response = self.client.get(self.url)
        self.assertNotContains(response, "Do not refund yet")
