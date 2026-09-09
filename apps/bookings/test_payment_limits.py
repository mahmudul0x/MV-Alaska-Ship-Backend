"""The gateway's own transaction band, enforced on our side.

SSLCommerz accepts 10.00–500000.00 BDT in a single transaction. It rejects
anything outside that AFTER the redirect, which strands the customer on a
gateway error page with nothing they can act on — so the refusal has to happen
here, in words, with the way forward in it.

A large group booking genuinely reaches the ceiling: at the current adult fare a
few cabins of guests is most of the way there.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import override_settings
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase

from apps.bookings import payment_service
from apps.bookings.models import Payment
from apps.bookings.test_api import build_fixtures
from apps.packages.models import Package, PackageRoom
from apps.ships.models import Room, RoomType
from apps.testing import ThrottlelessTestMixin, create_booking


class GatewayAmountBandTests(ThrottlelessTestMixin, APITestCase):
    @classmethod
    def setUpTestData(cls):
        (
            cls.ship,
            cls.type_2p,
            cls.type_4p,
            cls.room_2p,
            cls.room_4p,
            cls.package,
        ) = build_fixtures(ship_name="Limits Ship")

    def booking_worth(self, total):
        """A booking whose due is exactly `total`.

        The amounts here are the point of the test, so they are set directly
        rather than assembled out of fares — pricing has its own tests.
        """
        booking = create_booking(
            self.package, [{"room": self.room_4p, "adult_count": 2}]
        )
        booking.total_amount = Decimal(total)
        booking.save(update_fields=["total_amount", "due_amount", "updated_at"])
        booking.refresh_from_db()
        return booking

    def initiate(self, booking, payment_type=Payment.PaymentType.FULL, amount=None):
        # The gateway is never called: every case here is refused before that.
        with patch("apps.bookings.sslcommerz.create_session") as session:
            session.return_value = "https://example.test/pay"
            return payment_service.initiate_payment(booking, payment_type, amount)

    def test_a_booking_over_the_ceiling_is_refused_before_the_redirect(self):
        booking = self.booking_worth("600000.00")
        with self.assertRaises(ValidationError) as caught:
            self.initiate(booking)
        message = str(caught.exception.detail["amount"])
        # The customer needs the cap, their own figure, and a way forward.
        self.assertIn("500000.00", message)
        self.assertIn("600000.00", message)
        self.assertIn("partial", message.lower())

    def test_the_ceiling_is_inclusive(self):
        booking = self.booking_worth("500000.00")
        payment, _ = self.initiate(booking)
        self.assertEqual(payment.amount, Decimal("500000.00"))

    def test_an_oversized_booking_can_still_be_paid_in_instalments(self):
        """The way forward the error offers has to actually work — otherwise it
        is an apology, not a solution."""
        booking = self.booking_worth("600000.00")
        payment, _ = self.initiate(
            booking, Payment.PaymentType.PARTIAL, Decimal("300000.00")
        )
        self.assertEqual(payment.amount, Decimal("300000.00"))

    def test_a_payment_under_the_floor_is_refused(self):
        booking = self.booking_worth("20000.00")
        booking.paid_amount = Decimal("19995.00")
        booking.save(update_fields=["paid_amount", "due_amount", "updated_at"])
        # 5.00 left — below what the gateway will take.
        with self.assertRaises(ValidationError) as caught:
            self.initiate(booking)
        message = str(caught.exception.detail["amount"])
        self.assertIn("10.00", message)
        # Say what to do instead, rather than leaving them stuck on 5 taka.
        self.assertIn("guide", message.lower())

    def test_the_floor_is_inclusive(self):
        booking = self.booking_worth("20000.00")
        booking.paid_amount = Decimal("19990.00")
        booking.save(update_fields=["paid_amount", "due_amount", "updated_at"])
        payment, _ = self.initiate(booking)
        self.assertEqual(payment.amount, Decimal("10.00"))

    @override_settings(SSLCOMMERZ_MAX_AMOUNT=Decimal("1000.00"))
    def test_the_band_is_configuration_not_a_constant(self):
        """These are the gateway's numbers, not ours; they must be changeable
        without a code change when the gateway changes them."""
        booking = self.booking_worth("1500.00")
        with self.assertRaises(ValidationError):
            self.initiate(booking)


class LargeGroupBookingTests(ThrottlelessTestMixin, APITestCase):
    """The ceiling is not hypothetical at this operator's fares."""

    def test_a_realistic_group_booking_exceeds_the_gateway_ceiling(self):
        ship, type_2p, _, _, _, _ = build_fixtures(ship_name="Group Ship")
        package = Package.objects.create(
            ship=ship,
            start_date=date(2099, 5, 10),
            end_date=date(2099, 5, 12),
            adult_price=Decimal("20000.00"),
            status=Package.Status.OPEN,
        )
        big_type, _ = RoomType.objects.get_or_create(
            name="Group Cabin",
            defaults=dict(max_adults=4, max_kids=2, base_price=Decimal("3500.00")),
        )
        rooms = []
        for i in range(7):
            room = Room.objects.create(
                ship=ship, room_type=big_type, room_number=f"G{i}"
            )
            PackageRoom.objects.create(package=package, room=room)
            rooms.append(room)

        booking = create_booking(
            package, [{"room": room, "adult_count": 4} for room in rooms]
        )
        # 7 cabins x (3,500 base + 4 x 20,000) = 584,500 — over the cap, from
        # ordinary inputs and no unusual pricing.
        self.assertGreater(booking.total_amount, Decimal("500000.00"))
