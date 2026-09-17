"""The archive: sailings that have returned, and sailings that were called off.

The point of a separate endpoint is what it keeps OUT of the live list. The
list feeds the booking wizard's package picker and the home page as well as the
packages page, so a cancelled departure appearing there would be offered to
someone choosing what to book.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APITestCase

from apps.bookings.test_api import build_fixtures
from apps.packages.models import Package
from apps.testing import ThrottlelessTestMixin


class ArchiveEndpointTests(ThrottlelessTestMixin, APITestCase):
    def setUp(self):
        self.ship, *_rest, self.live = build_fixtures(ship_name="Archive Ship")
        today = timezone.localdate()

        def sailing(start, end, status):
            return Package.objects.create(
                ship=self.ship,
                start_date=start,
                end_date=end,
                adult_price=Decimal("3000.00"),
                status=status,
            )

        self.finished = sailing(
            today - timedelta(days=20), today - timedelta(days=17), Package.Status.OPEN
        )
        self.cancelled = sailing(
            today + timedelta(days=40),
            today + timedelta(days=43),
            Package.Status.CANCELLED,
        )
        self.ancient = sailing(
            date(2020, 1, 10), date(2020, 1, 13), Package.Status.COMPLETED
        )

    def archived(self):
        response = self.client.get("/api/packages/archive/")
        self.assertEqual(response.status_code, 200)
        return {p["id"]: p for p in response.data}

    def listed(self):
        response = self.client.get("/api/packages/")
        return {p["id"] for p in response.data}

    def test_a_finished_sailing_is_archived(self):
        self.assertIn(self.finished.id, self.archived())

    def test_a_cancelled_sailing_is_archived(self):
        self.assertIn(self.cancelled.id, self.archived())

    def test_a_cancelled_sailing_is_never_in_the_live_list(self):
        """The one that matters: the live list is what the booking wizard
        offers, and a called-off departure must not be offered."""
        self.assertNotIn(self.cancelled.id, self.listed())

    def test_a_finished_sailing_is_not_in_the_live_list_either(self):
        self.assertNotIn(self.finished.id, self.listed())

    def test_an_upcoming_sailing_stays_out_of_the_archive(self):
        self.assertNotIn(self.live.id, self.archived())

    def test_the_archive_does_not_reach_back_forever(self):
        """An unauthenticated read that grows with every passing year is one
        that eventually times out. A year is context; six is a catalogue."""
        self.assertNotIn(self.ancient.id, self.archived())

    def test_each_entry_says_why_it_is_archived(self):
        rows = self.archived()
        self.assertEqual(rows[self.finished.id]["archive_reason"], "finished")
        self.assertEqual(rows[self.cancelled.id]["archive_reason"], "cancelled")

    def test_a_live_sailing_has_no_archive_reason(self):
        response = self.client.get("/api/packages/")
        row = next(p for p in response.data if p["id"] == self.live.id)
        self.assertIsNone(row["archive_reason"])
