"""
Who can reach what.

These are the tests where a false pass is expensive: every one of them is a
thing somebody should not be able to do, and the whole point of the feature is
that they cannot. They call the real endpoints rather than the permission
classes directly, because what matters is whether the URL is reachable, not
whether a class returns False in isolation.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.packages.models import Package
from apps.ships.models import Ship
from apps.testing import ThrottlelessTestMixin

User = get_user_model()


class RoleAccessTests(ThrottlelessTestMixin, APITestCase):
    def setUp(self):
        self.ship, _ = Ship.objects.get_or_create(name="MV Alaska")
        self.package = Package.objects.create(
            ship=self.ship,
            start_date=date.today() + timedelta(days=20),
            end_date=date.today() + timedelta(days=22),
            adult_price=Decimal("20000.00"),
            status=Package.Status.OPEN,
        )
        self.admin = User.objects.create_user(
            username="owner", password="pw", is_staff=True, role=User.Role.ADMIN
        )
        self.booking_staff = User.objects.create_user(
            username="desk", password="pw", is_staff=True, role=User.Role.BOOKING
        )

    def as_booking_staff(self):
        self.client.force_authenticate(self.booking_staff)

    def as_admin(self):
        self.client.force_authenticate(self.admin)

    # ── What booking staff must be able to do ────────────────────────────────

    def test_booking_staff_can_open_the_bookings_list(self):
        self.as_booking_staff()
        response = self.client.get(reverse("staff-booking-list"))
        self.assertEqual(response.status_code, 200)

    def test_booking_staff_can_see_the_sailings_they_book_against(self):
        """A booking is made against a package — without read access here the
        role could not do the one job it exists for."""
        self.as_booking_staff()
        response = self.client.get(reverse("staff-package-list"))
        self.assertEqual(response.status_code, 200)

    def test_booking_staff_can_see_cabins_and_room_types(self):
        self.as_booking_staff()
        for route in ("staff-cabin-list", "staff-room-type-list", "staff-room-list"):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(reverse(route)).status_code, 200)

    def test_booking_staff_can_read_payments_and_invoices(self):
        self.as_booking_staff()
        for route in ("staff-payment-list", "staff-invoice-list"):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(reverse(route)).status_code, 200)

    def test_booking_staff_can_handle_customer_messages(self):
        self.as_booking_staff()
        response = self.client.get(reverse("staff-contact-message-list"))
        self.assertEqual(response.status_code, 200)

    # ── What booking staff must NOT be able to do ────────────────────────────

    def test_booking_staff_cannot_change_a_sailing(self):
        """Reading the catalogue is the job; editing it is the price list."""
        self.as_booking_staff()
        response = self.client.patch(
            reverse("staff-package-detail", args=[self.package.id]),
            {"adult_price": "1.00"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.package.refresh_from_db()
        self.assertEqual(self.package.adult_price, Decimal("20000.00"))

    def test_booking_staff_cannot_create_a_sailing(self):
        self.as_booking_staff()
        response = self.client.post(
            reverse("staff-package-list"),
            {
                "ship": self.ship.id,
                "start_date": str(date.today() + timedelta(days=40)),
                "end_date": str(date.today() + timedelta(days=42)),
                "adult_price": "20000.00",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_booking_staff_cannot_touch_refunds(self):
        """Money leaving the company is the sharpest line here."""
        self.as_booking_staff()
        response = self.client.get(reverse("staff-refund-list"))
        self.assertEqual(response.status_code, 403)

    def test_booking_staff_cannot_touch_cancellation_rules(self):
        self.as_booking_staff()
        response = self.client.get(reverse("staff-cancellation-rule-list"))
        self.assertEqual(response.status_code, 403)

    def test_booking_staff_cannot_change_pricing_rules(self):
        self.as_booking_staff()
        response = self.client.get(reverse("staff-kid-rule-list"))
        self.assertEqual(response.status_code, 403)

    def test_booking_staff_cannot_publish_offers_to_the_public(self):
        self.as_booking_staff()
        response = self.client.get(reverse("staff-promotion-list"))
        self.assertEqual(response.status_code, 403)

    def test_booking_staff_cannot_manage_other_accounts(self):
        """The one that would undo all the others: create an admin for
        yourself and every line above stops mattering."""
        self.as_booking_staff()

        self.assertEqual(self.client.get(reverse("staff-user-list")).status_code, 403)

        response = self.client.post(
            reverse("staff-user-list"),
            {"username": "mine", "password": "Sundarban!2026", "role": "admin"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.filter(username="mine").exists())

    # ── The administrator keeps everything ───────────────────────────────────

    def test_an_administrator_still_reaches_all_of_it(self):
        self.as_admin()
        for route in (
            "staff-package-list",
            "staff-refund-list",
            "staff-promotion-list",
            "staff-kid-rule-list",
            "staff-user-list",
        ):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(reverse(route)).status_code, 200)

    def test_a_superuser_is_an_administrator_whatever_the_field_says(self):
        """createsuperuser does not ask for a role, so the field can disagree
        with reality. The superuser must not be locked out by it."""
        root = User.objects.create_superuser(username="root", password="pw")
        root.role = User.Role.BOOKING
        root.save()

        self.client.force_authenticate(root)
        self.assertEqual(self.client.get(reverse("staff-user-list")).status_code, 200)

    def test_existing_accounts_default_to_administrator(self):
        """The migration must not quietly demote the people already using the
        dashboard — that would lock the owner out of their own system."""
        legacy = User.objects.create_user(username="legacy", password="pw", is_staff=True)
        self.assertEqual(legacy.role, User.Role.ADMIN)
        self.assertTrue(legacy.is_admin_role)

    def test_a_deactivated_account_reaches_nothing(self):
        self.booking_staff.is_active = False
        self.booking_staff.save()

        self.as_booking_staff()
        response = self.client.get(reverse("staff-booking-list"))
        self.assertIn(response.status_code, (401, 403))


class StaffAccountManagementTests(ThrottlelessTestMixin, APITestCase):
    """Creating and changing dashboard accounts."""

    def setUp(self):
        self.admin = User.objects.create_user(
            username="owner", password="pw", is_staff=True, role=User.Role.ADMIN
        )
        self.client.force_authenticate(self.admin)
        self.url = reverse("staff-user-list")

    def test_an_admin_creates_a_booking_account_that_can_log_in(self):
        response = self.client.post(
            self.url,
            {
                "username": "desk",
                "first_name": "Rahim",
                "password": "Sundarban!2026",
                "role": "booking",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        created = User.objects.get(username="desk")
        self.assertTrue(created.is_staff)  # or it could not open the dashboard
        self.assertTrue(created.check_password("Sundarban!2026"))
        self.assertEqual(created.role, User.Role.BOOKING)

    def test_the_password_is_never_echoed_back(self):
        response = self.client.post(
            self.url,
            {"username": "desk", "password": "Sundarban!2026", "role": "booking"},
            format="json",
        )
        self.assertNotIn("password", response.data)

    def test_a_weak_password_is_refused(self):
        response = self.client.post(
            self.url,
            {"username": "desk", "password": "1234", "role": "booking"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("password", response.data)

    def test_editing_without_a_password_keeps_the_existing_one(self):
        other = User.objects.create_user(
            username="desk", password="Sundarban!2026", is_staff=True,
            role=User.Role.BOOKING,
        )

        response = self.client.patch(
            reverse("staff-user-detail", args=[other.id]),
            {"first_name": "Rahim"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        other.refresh_from_db()
        self.assertTrue(other.check_password("Sundarban!2026"))

    def test_an_admin_cannot_demote_themselves(self):
        """Otherwise the only way back is a shell on the server."""
        response = self.client.patch(
            reverse("staff-user-detail", args=[self.admin.id]),
            {"role": "booking"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.role, User.Role.ADMIN)

    def test_an_admin_cannot_deactivate_themselves(self):
        response = self.client.patch(
            reverse("staff-user-detail", args=[self.admin.id]),
            {"is_active": False},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_the_last_administrator_cannot_be_stood_down(self):
        second = User.objects.create_user(
            username="second", password="pw", is_staff=True, role=User.Role.ADMIN
        )
        self.client.force_authenticate(second)

        # While two exist, demoting one is fine.
        response = self.client.patch(
            reverse("staff-user-detail", args=[self.admin.id]),
            {"role": "booking"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)

        # Now `second` is the only one left, and cannot be demoted by anyone.
        self.client.force_authenticate(self.admin)  # now booking staff
        self.client.force_authenticate(second)
        response = self.client.patch(
            reverse("staff-user-detail", args=[second.id]),
            {"role": "booking"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_removing_an_account_deactivates_it_rather_than_deleting_it(self):
        """The account is referenced by the bookings it created and the status
        changes it signed. Deleting it would make that history unattributable."""
        other = User.objects.create_user(
            username="desk", password="pw", is_staff=True, role=User.Role.BOOKING
        )

        response = self.client.delete(reverse("staff-user-detail", args=[other.id]))

        self.assertIn(response.status_code, (204, 200))
        other.refresh_from_db()
        self.assertFalse(other.is_active)
        self.assertTrue(User.objects.filter(pk=other.pk).exists())

    def test_the_list_is_only_dashboard_accounts(self):
        User.objects.create_user(username="nobody", password="pw", is_staff=False)

        usernames = [row["username"] for row in self.client.get(self.url).data]

        self.assertIn("owner", usernames)
        self.assertNotIn("nobody", usernames)
