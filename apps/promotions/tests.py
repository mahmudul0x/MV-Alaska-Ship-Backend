"""
Tests for promotions.

The weight is on `is_live()` and on what the public endpoint refuses to
return. A promotion is the one piece of staff-authored content that appears
unprompted in front of every visitor, so the failure that matters is not a
crash — it is last Eid's banner still greeting customers in August.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.packages.models import Package
from apps.promotions.models import ModalFrequency, Promotion
from apps.ships.models import Ship
from apps.testing import ThrottlelessTestMixin


def _make_ship(name="MV Alaska"):
    # get_or_create, not create: Ship.name is unique and a data migration
    # already seeds the live ship, so create() collides on a fresh test DB.
    ship, _ = Ship.objects.get_or_create(name=name)
    return ship


def _make_promotion(ship, **overrides):
    defaults = {
        "ship": ship,
        "title": "Eid Offer",
        "subtitle": "Sail the Sundarbans for less",
    }
    return Promotion.objects.create(**{**defaults, **overrides})


class PromotionLivenessTests(ThrottlelessTestMixin, APITestCase):
    """`is_live()` is the single definition of "showing right now" — the public
    endpoint, the dashboard's badge and these tests all call it."""

    def setUp(self):
        self.ship = _make_ship()
        self.now = timezone.now()

    def test_a_plain_active_promotion_is_live(self):
        promo = _make_promotion(self.ship)
        self.assertTrue(promo.is_live())

    def test_the_manual_switch_beats_everything(self):
        """The dates can be perfect and the switch still wins — this is what
        staff reach for when a promotion has to come down immediately."""
        promo = _make_promotion(
            self.ship,
            is_active=False,
            starts_at=self.now - timedelta(days=1),
            ends_at=self.now + timedelta(days=1),
        )
        self.assertFalse(promo.is_live())

    def test_a_promotion_scheduled_for_the_future_is_not_live_yet(self):
        promo = _make_promotion(self.ship, starts_at=self.now + timedelta(hours=1))
        self.assertFalse(promo.is_live())

    def test_a_promotion_whose_window_has_closed_is_not_live(self):
        promo = _make_promotion(self.ship, ends_at=self.now - timedelta(seconds=1))
        self.assertFalse(promo.is_live())

    def test_the_end_is_exclusive(self):
        """Exactly at ends_at it is over, not still running. The boundary is
        worth pinning: "ends at midnight" must not show at midnight."""
        promo = _make_promotion(self.ship, ends_at=self.now)
        self.assertFalse(promo.is_live(now=self.now))

    def test_blank_dates_mean_it_runs_until_switched_off(self):
        promo = _make_promotion(self.ship, starts_at=None, ends_at=None)
        self.assertTrue(promo.is_live())

    def test_a_promotion_for_a_departed_sailing_takes_itself_down(self):
        """The most common promotion is for one sailing, and nobody remembers
        to remove those. Once the ship has sailed the banner goes on its own,
        even with no end date set."""
        departed = Package.objects.create(
            ship=self.ship,
            start_date=date.today() - timedelta(days=2),
            end_date=date.today() - timedelta(days=1),
            adult_price=Decimal("20000.00"),
        )
        promo = _make_promotion(
            self.ship,
            linked_package=departed,
            cta_label="See the sailing",
        )
        self.assertFalse(promo.is_live())

    def test_a_promotion_for_an_upcoming_sailing_stays_live(self):
        upcoming = Package.objects.create(
            ship=self.ship,
            start_date=date.today() + timedelta(days=10),
            end_date=date.today() + timedelta(days=12),
            adult_price=Decimal("20000.00"),
        )
        promo = _make_promotion(
            self.ship, linked_package=upcoming, cta_label="See the sailing"
        )
        self.assertTrue(promo.is_live())


class PromotionValidationTests(ThrottlelessTestMixin, APITestCase):
    """Rules that stop staff publishing something broken to the public site."""

    def setUp(self):
        self.ship = _make_ship()

    def test_a_button_with_nowhere_to_go_is_refused(self):
        promo = Promotion(ship=self.ship, title="Eid", cta_label="Book now")
        with self.assertRaises(ValidationError) as caught:
            promo.clean()
        self.assertIn("cta_url", caught.exception.message_dict)

    def test_a_link_with_no_button_text_is_refused(self):
        promo = Promotion(ship=self.ship, title="Eid", cta_url="/packages")
        with self.assertRaises(ValidationError) as caught:
            promo.clean()
        self.assertIn("cta_label", caught.exception.message_dict)

    def test_an_end_before_its_start_is_refused(self):
        now = timezone.now()
        promo = Promotion(
            ship=self.ship,
            title="Eid",
            starts_at=now,
            ends_at=now - timedelta(hours=1),
        )
        with self.assertRaises(ValidationError) as caught:
            promo.clean()
        self.assertIn("ends_at", caught.exception.message_dict)

    def test_active_but_shown_nowhere_is_refused(self):
        """Otherwise it saves happily, reports itself live, and appears in no
        place at all — which staff read as the site being broken."""
        promo = Promotion(
            ship=self.ship,
            title="Eid",
            show_in_modal=False,
            show_in_hero=False,
            show_in_home_section=False,
        )
        with self.assertRaises(ValidationError):
            promo.clean()

    def test_a_linked_package_wins_over_a_typed_url(self):
        package = Package.objects.create(
            ship=self.ship,
            start_date=date.today() + timedelta(days=5),
            end_date=date.today() + timedelta(days=7),
            adult_price=Decimal("20000.00"),
        )
        promo = _make_promotion(
            self.ship,
            cta_label="Book",
            cta_url="/somewhere-else",
            linked_package=package,
        )
        self.assertEqual(promo.resolved_cta_url, f"/packages?package={package.id}")


class PublicPromotionEndpointTests(ThrottlelessTestMixin, APITestCase):
    """What the website is allowed to see."""

    def setUp(self):
        self.ship = _make_ship()
        self.url = reverse("promotion-list")

    def test_it_returns_a_live_promotion(self):
        _make_promotion(self.ship, badge_label="EID OFFER")
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["badge_label"], "EID OFFER")

    def test_it_hides_switched_off_expired_and_not_yet_started_promotions(self):
        now = timezone.now()
        _make_promotion(self.ship, title="Off", is_active=False)
        _make_promotion(self.ship, title="Expired", ends_at=now - timedelta(days=1))
        _make_promotion(self.ship, title="Future", starts_at=now + timedelta(days=1))
        _make_promotion(self.ship, title="Live")

        response = self.client.get(self.url)

        titles = [row["title"] for row in response.data]
        self.assertEqual(titles, ["Live"])

    def test_it_never_leaks_scheduling_or_the_manual_switch(self):
        """The public payload carries what to draw, not how it is managed."""
        _make_promotion(self.ship, starts_at=timezone.now() - timedelta(days=1))

        row = self.client.get(self.url).data[0]

        for private in ("is_active", "starts_at", "ends_at", "ship", "sort_order"):
            self.assertNotIn(private, row)

    def test_it_carries_what_the_modal_needs_to_throttle_itself(self):
        _make_promotion(
            self.ship,
            modal_frequency=ModalFrequency.ONCE,
            modal_delay_seconds=5,
        )

        row = self.client.get(self.url).data[0]

        self.assertEqual(row["modal_frequency"], "once")
        self.assertEqual(row["modal_delay_seconds"], 5)
        # Dismissal is keyed on id + updated_at, so an edited promotion is
        # shown again to someone who dismissed the old wording.
        self.assertIn("updated_at", row)

    def test_sort_order_decides_which_one_the_modal_shows(self):
        _make_promotion(self.ship, title="Second", sort_order=2)
        _make_promotion(self.ship, title="First", sort_order=1)

        titles = [row["title"] for row in self.client.get(self.url).data]

        self.assertEqual(titles, ["First", "Second"])

    def test_it_is_readable_without_logging_in(self):
        _make_promotion(self.ship)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_it_refuses_writes_from_the_public(self):
        response = self.client.post(self.url, {"title": "Injected"})
        self.assertIn(response.status_code, (401, 403, 405))
        self.assertFalse(Promotion.objects.filter(title="Injected").exists())
