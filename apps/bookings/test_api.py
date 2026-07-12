import threading
from datetime import date
from decimal import Decimal

from django.db import connections
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.packages.models import KidPricingRule, Package, PackageRoom
from apps.ships.models import Room, RoomType, Ship
from apps.testing import ThrottlelessTestMixin

from .models import Booking

BOOKING_PUBLIC_FIELDS = {
    "booking_code",
    "status",
    "package",
    "room_number",
    "customer_name",
    "phone",
    "email",
    "adult_count",
    "kid_details",
    "total_amount",
    "paid_amount",
    "due_amount",
    "min_first_payment",
    # The balance deadline, as a date the customer can actually see — it is
    # enforced at payment time, so it must be visible before it bites (QA H8).
    "balance_due_at",
    "balance_deadline_passed",
}


def build_fixtures(ship_name="Test Ship"):
    """Shared fixture builder (also used by the thread-based race test)."""
    ship = Ship.objects.create(name=ship_name)
    type_2p, _ = RoomType.objects.get_or_create(
        name="2-Person Room",
        defaults=dict(max_adults=2, max_kids=1, base_price=Decimal("2000.00")),
    )
    type_4p, _ = RoomType.objects.get_or_create(
        name="4-Person Room",
        defaults=dict(max_adults=4, max_kids=2, base_price=Decimal("3500.00")),
    )
    room_2p = Room.objects.create(ship=ship, room_type=type_2p, room_number="T1")
    room_4p = Room.objects.create(ship=ship, room_type=type_4p, room_number="T2")
    package = Package.objects.create(
        ship=ship,
        start_date=date(2099, 1, 10),
        end_date=date(2099, 1, 12),
        adult_price=Decimal("3000.00"),
        status=Package.Status.OPEN,
    )
    PackageRoom.objects.create(package=package, room=room_2p)
    PackageRoom.objects.create(package=package, room=room_4p)
    for min_age, max_age, ctype, amount in [
        (0, 3, KidPricingRule.ChargeType.FREE, None),
        (3, 8, KidPricingRule.ChargeType.FIXED, Decimal("1500.00")),
        (8, 99, KidPricingRule.ChargeType.FULL_ADULT, None),
    ]:
        KidPricingRule.objects.get_or_create(
            min_age=min_age,
            max_age=max_age,
            defaults={"charge_type": ctype, "amount": amount},
        )
    return ship, type_2p, type_4p, room_2p, room_4p, package


class BookingApiTestCase(ThrottlelessTestMixin, APITestCase):
    @classmethod
    def setUpTestData(cls):
        (
            cls.ship,
            cls.type_2p,
            cls.type_4p,
            cls.room_2p,
            cls.room_4p,
            cls.package,
        ) = build_fixtures()

    def payload(self, **overrides):
        data = {
            "package_id": self.package.id,
            "room_id": self.room_4p.id,
            "customer_name": "Rahim Uddin",
            "phone": "01700000000",
            "email": "rahim@example.com",
            "adult_count": 2,
            "kid_details": [{"age": 2}, {"age": 5}],
        }
        data.update(overrides)
        return data


class QuoteApiTests(BookingApiTestCase):
    def test_quote_returns_exact_breakdown_and_writes_nothing(self):
        response = self.client.post("/api/bookings/quote/", self.payload(), format="json")
        self.assertEqual(response.status_code, 200)
        data = response.data
        # 3500 base + 2×3000 adults + 0 (age 2) + 1500 (age 5) = 11000
        self.assertEqual(data["total"], "11000.00")
        self.assertEqual(data["room_base"], "3500.00")
        self.assertEqual(data["adults_subtotal"], "6000.00")
        self.assertEqual(data["kids_subtotal"], "1500.00")
        self.assertEqual(
            data["kids"],
            [{"age": 2, "charge": "0.00"}, {"age": 5, "charge": "1500.00"}],
        )
        self.assertEqual(Booking.objects.count(), 0)

    def test_quote_validates_like_create(self):
        response = self.client.post(
            "/api/bookings/quote/",
            self.payload(room_id=self.room_2p.id, adult_count=3),
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("adult_count", response.data)


class BookingCreateApiTests(BookingApiTestCase):
    def test_create_booking_happy_path(self):
        response = self.client.post("/api/bookings/", self.payload(), format="json")
        self.assertEqual(response.status_code, 201)
        data = response.data
        self.assertEqual(data["status"], "pending")
        self.assertEqual(data["total_amount"], "11000.00")
        self.assertEqual(data["paid_amount"], "0.00")
        self.assertEqual(data["due_amount"], "11000.00")
        self.assertEqual(data["room_number"], "T2")
        self.assertEqual(data["price_breakdown"]["total"], "11000.00")

        booking = Booking.objects.get(booking_code=data["booking_code"])
        self.assertEqual(booking.status, Booking.Status.PENDING)
        self.assertEqual(booking.total_amount, Decimal("11000.00"))
        self.assertEqual(booking.status_logs.count(), 1)

    def test_client_submitted_amounts_ignored(self):
        payload = self.payload()
        payload["total_amount"] = "1.00"
        payload["paid_amount"] = "11000.00"
        payload["due_amount"] = "0.00"
        response = self.client.post("/api/bookings/", payload, format="json")
        self.assertEqual(response.status_code, 201)
        booking = Booking.objects.get(booking_code=response.data["booking_code"])
        self.assertEqual(booking.total_amount, Decimal("11000.00"))
        self.assertEqual(booking.paid_amount, Decimal("0.00"))
        self.assertEqual(booking.due_amount, Decimal("11000.00"))

    def test_retrieve_by_code_and_unknown_404(self):
        created = self.client.post("/api/bookings/", self.payload(), format="json")
        code = created.data["booking_code"]
        response = self.client.get(f"/api/bookings/{code}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data.keys()), BOOKING_PUBLIC_FIELDS)
        self.assertEqual(self.client.get("/api/bookings/BK-NOPE9999/").status_code, 404)


class BookingValidationApiTests(BookingApiTestCase):
    def test_pax_limit_rejected(self):
        response = self.client.post(
            "/api/bookings/",
            self.payload(room_id=self.room_2p.id, adult_count=3, kid_details=[]),
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("adult_count", response.data)

    def test_too_many_kids_rejected(self):
        response = self.client.post(
            "/api/bookings/",
            self.payload(
                room_id=self.room_2p.id,
                adult_count=2,
                kid_details=[{"age": 4}, {"age": 6}],
            ),
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("kid_details", response.data)

    def test_negative_age_rejected(self):
        response = self.client.post(
            "/api/bookings/", self.payload(kid_details=[{"age": -1}]), format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("kid_details", response.data)

    def test_zero_adults_rejected(self):
        response = self.client.post(
            "/api/bookings/", self.payload(adult_count=0), format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("adult_count", response.data)

    def test_cutoff_passed_rejected(self):
        self.package.booking_cutoff_datetime = timezone.now() - timezone.timedelta(
            hours=1
        )
        self.package.save()
        response = self.client.post("/api/bookings/", self.payload(), format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("package_id", response.data)

    def test_draft_package_rejected(self):
        self.package.status = Package.Status.DRAFT
        self.package.save()
        response = self.client.post("/api/bookings/", self.payload(), format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("package_id", response.data)

    def test_room_not_in_package_rejected(self):
        stray = Room.objects.create(
            ship=self.ship, room_type=self.type_2p, room_number="T9"
        )
        response = self.client.post(
            "/api/bookings/", self.payload(room_id=stray.id), format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("room_id", response.data)

    def test_admin_withheld_room_conflict(self):
        PackageRoom.objects.filter(package=self.package, room=self.room_4p).update(
            is_available=False
        )
        response = self.client.post("/api/bookings/", self.payload(), format="json")
        self.assertEqual(response.status_code, 409)


class DoubleBookingApiTests(BookingApiTestCase):
    def test_already_booked_room_conflict(self):
        first = self.client.post("/api/bookings/", self.payload(), format="json")
        self.assertEqual(first.status_code, 201)
        second = self.client.post(
            "/api/bookings/", self.payload(customer_name="Karim"), format="json"
        )
        self.assertEqual(second.status_code, 409)
        self.assertEqual(Booking.objects.count(), 1)

    def test_cancelled_booking_frees_room_for_new_booking(self):
        first = self.client.post("/api/bookings/", self.payload(), format="json")
        booking = Booking.objects.get(booking_code=first.data["booking_code"])
        booking.status = Booking.Status.CANCELLED
        booking.save()
        second = self.client.post(
            "/api/bookings/", self.payload(customer_name="Karim"), format="json"
        )
        self.assertEqual(second.status_code, 201)


class ConcurrentBookingRaceTests(ThrottlelessTestMixin, TransactionTestCase):
    """True concurrency: two threads POST for the same room simultaneously.
    Exactly one must win (201); the loser gets 409 — never two bookings."""

    def setUp(self):
        _, _, _, _, self.room, self.package = build_fixtures(ship_name="Race Ship")

    def test_concurrent_bookings_one_winner(self):
        from rest_framework.test import APIClient

        barrier = threading.Barrier(2)
        results = []

        def attempt(name):
            try:
                barrier.wait(timeout=10)
                client = APIClient()
                response = client.post(
                    "/api/bookings/",
                    {
                        "package_id": self.package.id,
                        "room_id": self.room.id,
                        "customer_name": name,
                        "phone": "01700000000",
                        "email": "race@example.com",
                        "adult_count": 1,
                        "kid_details": [],
                    },
                    format="json",
                )
                results.append(response.status_code)
            finally:
                connections.close_all()

        threads = [
            threading.Thread(target=attempt, args=(f"Customer {i}",)) for i in range(2)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(sorted(results), [201, 409])
        self.assertEqual(
            Booking.objects.filter(package=self.package, room=self.room).count(), 1
        )
