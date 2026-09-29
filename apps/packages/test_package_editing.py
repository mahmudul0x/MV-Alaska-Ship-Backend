"""
What staff may still change about a sailing, and when that stops.

Two opposite failures are being pinned here. One is a sailing filling badly
and nobody being able to do anything about it — the client's actual case: 30
cabins targeted, 20 sold, and an offer wanted on the rest. The other is a
finished sailing being quietly rewritten months later, under invoices and a
guide report that were printed from the old figures.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.bookings.models import Booking
from apps.packages.models import Package
from apps.ships.models import Ship
from apps.testing import ThrottlelessTestMixin

User = get_user_model()


class PackageEditingTests(ThrottlelessTestMixin, APITestCase):
    def setUp(self):
        self.ship, _ = Ship.objects.get_or_create(name="MV Alaska")
        self.staff = User.objects.create_user(
            username="staff", password="pw", is_staff=True
        )
        self.client.force_authenticate(self.staff)

    def _package(self, **overrides):
        defaults = {
            "ship": self.ship,
            "start_date": date.today() + timedelta(days=20),
            "end_date": date.today() + timedelta(days=22),
            "adult_price": Decimal("20000.00"),
            "status": Package.Status.OPEN,
        }
        return Package.objects.create(**{**defaults, **overrides})

    def _finished(self, **overrides):
        """A sailing that has already departed AND returned.

        Both dates move together: a DB check constraint enforces
        end_date >= start_date, so overriding only the end date builds a row
        the database refuses outright.
        """
        return self._package(
            start_date=date.today() - timedelta(days=3),
            end_date=date.today() - timedelta(days=1),
            **overrides,
        )

    def _url(self, package):
        return reverse("staff-package-detail", args=[package.id])

    # ── Still editable: the client's actual scenario ─────────────────────────

    def test_an_offer_can_be_added_to_a_sailing_that_is_selling_badly(self):
        """The case this exists for: cabins are not moving, so staff discount
        the remainder. This must keep working right up to departure."""
        package = self._package()

        response = self.client.patch(
            self._url(package),
            {
                "discount_type": "percent",
                "discount_value": "15.00",
                "offer_label": "Last cabins",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        package.refresh_from_db()
        self.assertEqual(package.discount_value, Decimal("15.00"))
        self.assertEqual(package.offer_label, "Last cabins")

    def test_an_offer_can_be_removed_again(self):
        package = self._package(
            discount_type="percent",
            discount_value=Decimal("15.00"),
            offer_label="Last cabins",
        )

        response = self.client.patch(
            self._url(package),
            {"discount_type": "none", "discount_value": "0.00", "offer_label": ""},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        package.refresh_from_db()
        self.assertEqual(package.discount_type, "none")

    def test_marketing_copy_and_the_cutoff_stay_editable_before_departure(self):
        package = self._package()

        response = self.client.patch(
            self._url(package),
            {"marketing_title": "Winter special", "is_booking_open": False},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)

    def test_a_sailing_that_starts_today_is_still_editable(self):
        """It has not finished — the boundary is departure, not the start.
        Staff are most likely to be adjusting things on the day itself."""
        package = self._package(
            start_date=date.today(), end_date=date.today() + timedelta(days=2)
        )

        response = self.client.patch(
            self._url(package), {"offer_label": "Today only"}, format="json"
        )

        self.assertEqual(response.status_code, 200, response.data)

    # ── No longer editable ───────────────────────────────────────────────────

    def test_a_finished_sailing_refuses_an_offer_change(self):
        package = self._finished()

        response = self.client.patch(
            self._url(package),
            {"discount_type": "percent", "discount_value": "15.00"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        package.refresh_from_db()
        self.assertEqual(package.discount_type, "none")

    def test_a_finished_sailing_refuses_a_price_change(self):
        package = self._finished()

        response = self.client.patch(
            self._url(package), {"adult_price": "1.00"}, format="json"
        )

        self.assertEqual(response.status_code, 400)
        package.refresh_from_db()
        self.assertEqual(package.adult_price, Decimal("20000.00"))

    def test_a_completed_sailing_is_locked_even_if_its_dates_are_ahead(self):
        """Status is the other way a sailing is finished with — a short cruise
        marked completed early must not reopen for edits."""
        package = self._package(status=Package.Status.COMPLETED)

        response = self.client.patch(
            self._url(package), {"marketing_title": "Rewritten"}, format="json"
        )

        self.assertEqual(response.status_code, 400)

    def test_the_error_names_the_field_and_says_why(self):
        """Staff need to know it is the sailing being over, not their input."""
        package = self._finished()

        response = self.client.patch(
            self._url(package), {"offer_label": "Too late"}, format="json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("offer_label", response.data)
        self.assertIn("finished", str(response.data["offer_label"]).lower())

    def test_a_finished_sailing_cannot_be_deleted(self):
        """Deleting is the largest edit there is — it would take the bookings,
        payments and invoices that are the record of a tour that ran."""
        package = self._finished()

        response = self.client.delete(self._url(package))

        self.assertEqual(response.status_code, 400)
        self.assertTrue(Package.objects.filter(pk=package.pk).exists())

    # ── The two deliberate exceptions ────────────────────────────────────────

    def test_a_finished_sailing_can_still_be_marked_completed(self):
        """Bookkeeping about something that already happened, not a change to
        it — and nothing sets this status automatically, so locking it would
        leave staff unable to close a sailing off at all."""
        package = self._finished()

        response = self.client.patch(
            self._url(package), {"status": Package.Status.COMPLETED}, format="json"
        )

        self.assertEqual(response.status_code, 200, response.data)
        package.refresh_from_db()
        self.assertEqual(package.status, Package.Status.COMPLETED)

    def test_resending_unchanged_values_on_a_finished_sailing_is_not_an_error(self):
        """The dashboard PATCHes the whole form. Sending back what is already
        stored changes nothing, and must not be rejected as an edit."""
        package = self._finished()

        response = self.client.patch(
            self._url(package),
            {"adult_price": "20000.00", "marketing_title": package.marketing_title},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)

    def test_a_cancelled_future_sailing_is_still_editable(self):
        """Cancelled is deliberately not "finished": an accidental
        cancellation of a sailing that has not departed must be recoverable."""
        package = self._package(status=Package.Status.CANCELLED)

        response = self.client.patch(
            self._url(package), {"status": Package.Status.OPEN}, format="json"
        )

        self.assertEqual(response.status_code, 200, response.data)


class RepricingWithLiveBookingsTests(ThrottlelessTestMixin, APITestCase):
    """Changing a sailing's price while people are already booked on it.

    This used to be refused outright, with the advice to "cancel and rebook".
    That was bad advice: cancelling real bookings runs the cancellation-charge
    and refund machinery and emails those customers, all to change a number
    that does not affect any of them. It now asks instead, and the tests pin
    both halves — that it asks, and that saying yes leaves the existing
    bookings exactly as they were.
    """

    def setUp(self):
        self.ship, _ = Ship.objects.get_or_create(name="MV Alaska")
        self.staff = User.objects.create_user(
            username="repricer", password="pw", is_staff=True
        )
        self.client.force_authenticate(self.staff)

        self.package = Package.objects.create(
            ship=self.ship,
            start_date=date.today() + timedelta(days=20),
            end_date=date.today() + timedelta(days=22),
            adult_price=Decimal("20000.00"),
            status=Package.Status.OPEN,
        )
        self.url = reverse("staff-package-detail", args=[self.package.id])

    def _add_booking(self):
        """A booking priced against the package as it stands today."""
        return Booking.objects.create(
            package=self.package,
            customer_name="Rahim",
            phone="01700000000",
            email="rahim@example.com",
            total_amount=Decimal("44000.00"),
            paid_amount=Decimal("44000.00"),
            due_amount=Decimal("0.00"),
            status=Booking.Status.FULLY_PAID,
        )

    def test_with_no_bookings_the_price_changes_without_ceremony(self):
        response = self.client.patch(
            self.url, {"adult_price": "18000.00"}, format="json"
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.package.refresh_from_db()
        self.assertEqual(self.package.adult_price, Decimal("18000.00"))

    def test_with_bookings_it_asks_first(self):
        self._add_booking()

        response = self.client.patch(
            self.url, {"adult_price": "18000.00"}, format="json"
        )

        self.assertEqual(response.status_code, 400)
        self.package.refresh_from_db()
        self.assertEqual(self.package.adult_price, Decimal("20000.00"))

    def test_the_message_points_at_offers_and_not_at_cancelling(self):
        """The old advice would have had staff cancel live bookings. Whatever
        this message says, it must never say that again."""
        self._add_booking()

        response = self.client.patch(
            self.url, {"adult_price": "18000.00"}, format="json"
        )
        message = str(response.data["adult_price"]).lower()

        self.assertIn("offer", message)
        self.assertNotIn("cancel and rebook", message)

    def test_confirming_goes_through(self):
        self._add_booking()

        response = self.client.patch(
            self.url,
            {"adult_price": "18000.00", "confirm_reprice": True},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.package.refresh_from_db()
        self.assertEqual(self.package.adult_price, Decimal("18000.00"))

    def test_the_people_already_booked_keep_what_they_were_quoted(self):
        """The whole reason this is safe. If this ever fails, put the hard
        block back."""
        booking = self._add_booking()

        self.client.patch(
            self.url,
            {"adult_price": "1.00", "confirm_reprice": True},
            format="json",
        )

        booking.refresh_from_db()
        self.assertEqual(booking.total_amount, Decimal("44000.00"))
        self.assertEqual(booking.paid_amount, Decimal("44000.00"))
        self.assertEqual(booking.due_amount, Decimal("0.00"))

    def test_a_cancelled_booking_does_not_trigger_the_question(self):
        booking = self._add_booking()
        booking.status = Booking.Status.CANCELLED
        booking.save()

        response = self.client.patch(
            self.url, {"adult_price": "18000.00"}, format="json"
        )

        self.assertEqual(response.status_code, 200, response.data)

    def test_the_acknowledgement_never_reaches_the_model_or_a_read(self):
        """It is a flag about the request, not a field of the package."""
        self._add_booking()

        response = self.client.patch(
            self.url,
            {"adult_price": "18000.00", "confirm_reprice": True},
            format="json",
        )

        self.assertNotIn("confirm_reprice", response.data)
        self.assertFalse(hasattr(self.package, "confirm_reprice"))

    def test_an_offer_can_still_be_set_without_any_confirmation(self):
        """Discounting the remaining cabins is the recommended path, so it must
        stay the frictionless one."""
        self._add_booking()

        response = self.client.patch(
            self.url,
            {
                "discount_type": "percent",
                "discount_value": "15.00",
                "offer_label": "Last cabins",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
