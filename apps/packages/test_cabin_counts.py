"""Cabin availability counts on the public package list.

The homepage card prints "N cabins free". That number sits next to a deck plan
the customer can open, so it has to agree with what that plan lets them pick —
an inflated count is a promise the booking step then refuses.
"""

from datetime import date
from decimal import Decimal

from rest_framework.test import APITestCase

from apps.bookings.models import Booking
from apps.packages.models import Package, PackageRoom
from apps.packages.inventory import count_free_cabins
from apps.ships.models import Room, RoomType, Ship
from apps.testing import ThrottlelessTestMixin, create_booking


class CabinCountTests(ThrottlelessTestMixin, APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ship = Ship.objects.create(name="Count Ship")
        cls.room_type, _ = RoomType.objects.get_or_create(
            name="2-Person Room",
            defaults=dict(max_adults=2, max_kids=1, base_price=Decimal("2000.00")),
        )
        cls.package = Package.objects.create(
            ship=cls.ship,
            start_date=date(2099, 1, 10),
            end_date=date(2099, 1, 12),
            adult_price=Decimal("3000.00"),
            status=Package.Status.OPEN,
        )
        cls.rooms = [
            Room.objects.create(
                ship=cls.ship, room_type=cls.room_type, room_number=f"C{i}"
            )
            for i in range(5)
        ]
        for room in cls.rooms:
            PackageRoom.objects.create(package=cls.package, room=room)

    def listed(self):
        response = self.client.get("/api/packages/")
        self.assertEqual(response.status_code, 200)
        return next(p for p in response.data if p["id"] == self.package.id)

    def test_all_cabins_free_on_a_fresh_sailing(self):
        row = self.listed()
        self.assertEqual(row["cabins_total"], 5)
        self.assertEqual(row["cabins_free"], 5)

    def test_a_booking_takes_its_cabin_out_of_the_count(self):
        create_booking(
            self.package, [{"room": self.rooms[0], "adult_count": 2}]
        )
        self.assertEqual(self.listed()["cabins_free"], 4)

    def test_cancelling_gives_the_cabin_back(self):
        booking = create_booking(
            self.package, [{"room": self.rooms[0], "adult_count": 2}]
        )
        booking.status = Booking.Status.CANCELLED
        booking.save()
        self.assertEqual(self.listed()["cabins_free"], 5)

    def test_an_admin_hold_is_not_free_but_is_still_inventory(self):
        """Blocked reads as 'booked' on the deck plan, so it must not be
        counted free — but the ship still has the cabin."""
        PackageRoom.objects.filter(
            package=self.package, room=self.rooms[1]
        ).update(is_blocked=True)
        row = self.listed()
        self.assertEqual(row["cabins_free"], 4)
        self.assertEqual(row["cabins_total"], 5)

    def test_a_cabin_withdrawn_from_inventory_counts_in_neither(self):
        PackageRoom.objects.filter(
            package=self.package, room=self.rooms[2]
        ).update(is_available=False)
        row = self.listed()
        self.assertEqual(row["cabins_free"], 4)
        self.assertEqual(row["cabins_total"], 4)

    def test_zero_free_is_zero_and_not_null(self):
        """No free cabin means the subquery returns no row; NULL would render
        as 'null cabins free' on the card."""
        for room in self.rooms:
            create_booking(self.package, [{"room": room, "adult_count": 2}])
        self.assertEqual(self.listed()["cabins_free"], 0)

    def test_the_count_matches_what_the_deck_plan_offers(self):
        """The card's number and the plan's selectable tiles are the same claim
        made twice; they must not be able to disagree."""
        create_booking(self.package, [{"room": self.rooms[0], "adult_count": 2}])
        PackageRoom.objects.filter(
            package=self.package, room=self.rooms[1]
        ).update(is_blocked=True)
        PackageRoom.objects.filter(
            package=self.package, room=self.rooms[2]
        ).update(is_available=False)

        rooms = self.client.get(f"/api/packages/{self.package.id}/rooms/").data
        selectable = [r for r in rooms if r["availability"] == "available"]
        self.assertEqual(self.listed()["cabins_free"], len(selectable))

    def test_listing_stays_one_pass_however_many_sailings(self):
        """Counting per package would be a query per sailing on a page that
        renders every open one."""
        for i in range(3):
            Package.objects.create(
                ship=Ship.objects.create(name=f"Extra {i}"),
                start_date=date(2099, 6, 10),
                end_date=date(2099, 6, 12),
                adult_price=Decimal("3000.00"),
                status=Package.Status.OPEN,
            )
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as ctx:
            self.client.get("/api/packages/")
        baseline = len(ctx.captured_queries)

        for i in range(3, 8):
            Package.objects.create(
                ship=Ship.objects.create(name=f"Extra {i}"),
                start_date=date(2099, 8, 10),
                end_date=date(2099, 8, 12),
                adult_price=Decimal("3000.00"),
                status=Package.Status.OPEN,
            )
        with self.assertNumQueries(baseline):
            self.client.get("/api/packages/")

    def test_the_helper_agrees_with_the_annotation(self):
        """count_free_cabins serves un-annotated Packages; the two definitions
        of 'free' must not drift."""
        create_booking(self.package, [{"room": self.rooms[0], "adult_count": 2}])
        self.assertEqual(count_free_cabins(self.package), self.listed()["cabins_free"])
