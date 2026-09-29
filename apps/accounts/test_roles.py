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
            username="desk",
            password="pw",
            is_staff=True,
            role=User.Role.BOOKING,
            capabilities=["bookings", "payments", "invoices", "messages"],
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


class CapabilityTests(ThrottlelessTestMixin, APITestCase):
    """An administrator ticks what each account may do, and only that.

    The role decides whether somebody can manage OTHER accounts; everything
    else is a capability. These tests exist because the whole feature is the
    difference between "was given this" and "was not stopped from this".
    """

    def setUp(self):
        self.ship, _ = Ship.objects.get_or_create(name="MV Alaska")
        self.admin = User.objects.create_user(
            username="owner", password="pw", is_staff=True, role=User.Role.ADMIN
        )

    def staff_with(self, *capabilities):
        user = User.objects.create_user(
            username=f"s{len(capabilities)}{capabilities}"[:30],
            password="pw",
            is_staff=True,
            role=User.Role.BOOKING,
            capabilities=list(capabilities),
        )
        self.client.force_authenticate(user)
        return user

    def test_a_granted_area_is_reachable(self):
        self.staff_with("bookings")
        self.assertEqual(
            self.client.get(reverse("staff-booking-list")).status_code, 200
        )

    def test_an_ungranted_area_is_not(self):
        self.staff_with("bookings")
        self.assertEqual(
            self.client.get(reverse("staff-refund-list")).status_code, 403
        )

    def test_capabilities_are_independent_of_each_other(self):
        """Granting refunds must not quietly bring bookings with it."""
        self.staff_with("refunds")

        self.assertEqual(self.client.get(reverse("staff-refund-list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("staff-booking-list")).status_code, 403)

    def test_an_account_with_nothing_ticked_reaches_no_work(self):
        """Fails closed: an empty list grants nothing, rather than everything."""
        self.staff_with()

        for route in (
            "staff-booking-list",
            "staff-refund-list",
            "staff-promotion-list",
            "staff-contact-message-list",
        ):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(reverse(route)).status_code, 403)

    def test_the_catalogue_can_be_read_but_grants_nothing(self):
        self.staff_with("bookings")
        response = self.client.get(reverse("staff-capabilities"))

        self.assertEqual(response.status_code, 200)
        keys = {c["key"] for c in response.data["capabilities"]}
        self.assertIn("refunds", keys)
        # Reading about refunds is not being able to do them.
        self.assertEqual(self.client.get(reverse("staff-refund-list")).status_code, 403)

    def test_managing_accounts_is_not_a_capability_anyone_can_be_given(self):
        """The one that would undo the rest: ticking your way to admin."""
        self.staff_with("bookings", "refunds", "pricing", "promotions", "packages")
        self.assertEqual(self.client.get(reverse("staff-user-list")).status_code, 403)

    def test_the_catalogue_is_refused_unknown_keys(self):
        """A typo would sit in the list looking granted and grant nothing."""
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            reverse("staff-user-list"),
            {
                "username": "desk",
                "password": "Sundarban!2026",
                "role": "booking",
                "capabilities": ["bookings", "refndz"],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("capabilities", response.data)

    def test_a_new_account_with_no_choice_made_gets_the_desk_job(self):
        """An account that signs in to a dashboard refusing everything reads
        as broken, not as restricted."""
        self.client.force_authenticate(self.admin)

        self.client.post(
            reverse("staff-user-list"),
            {"username": "desk", "password": "Sundarban!2026", "role": "booking"},
            format="json",
        )

        created = User.objects.get(username="desk")
        self.assertEqual(
            set(created.capabilities), {"bookings", "payments", "invoices", "messages"}
        )

    def test_an_administrator_holds_every_capability_without_a_list(self):
        self.assertEqual(self.admin.capabilities, [])
        for key in ("bookings", "refunds", "pricing", "promotions"):
            with self.subTest(key=key):
                self.assertTrue(self.admin.has_capability(key))

    def test_the_catalogue_a_sailing_is_booked_against_stays_readable(self):
        """Someone taking bookings must SEE sailings and cabins even without
        permission to edit them, or the booking form cannot name what it is
        booking."""
        self.staff_with("bookings")

        for route in ("staff-package-list", "staff-cabin-list", "staff-room-type-list"):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(reverse(route)).status_code, 200)

    def test_but_editing_that_catalogue_needs_the_capability(self):
        package = Package.objects.create(
            ship=self.ship,
            start_date=date.today() + timedelta(days=20),
            end_date=date.today() + timedelta(days=22),
            adult_price=Decimal("20000.00"),
            status=Package.Status.OPEN,
        )
        self.staff_with("bookings")

        response = self.client.patch(
            reverse("staff-package-detail", args=[package.id]),
            {"marketing_title": "Rewritten"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_an_admin_can_change_what_an_account_may_do(self):
        desk = User.objects.create_user(
            username="desk", password="pw", is_staff=True,
            role=User.Role.BOOKING, capabilities=["bookings"],
        )
        self.client.force_authenticate(self.admin)

        response = self.client.patch(
            reverse("staff-user-detail", args=[desk.id]),
            {"capabilities": ["bookings", "refunds"]},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        desk.refresh_from_db()
        self.assertIn("refunds", desk.capabilities)

        self.client.force_authenticate(desk)
        self.assertEqual(self.client.get(reverse("staff-refund-list")).status_code, 200)


class FineGrainedCapabilityTests(ThrottlelessTestMixin, APITestCase):
    """The places where one screen serves two jobs, and the split is by field
    or by what is shown rather than by URL."""

    def setUp(self):
        self.ship, _ = Ship.objects.get_or_create(name="MV Alaska")
        self.package = Package.objects.create(
            ship=self.ship,
            start_date=date.today() + timedelta(days=20),
            end_date=date.today() + timedelta(days=22),
            adult_price=Decimal("20000.00"),
            status=Package.Status.OPEN,
        )
        self.detail = reverse("staff-package-detail", args=[self.package.id])

    def staff_with(self, *capabilities):
        user = User.objects.create_user(
            username=("u_" + "_".join(capabilities))[:30] or "u_none",
            password="pw",
            is_staff=True,
            role=User.Role.BOOKING,
            capabilities=list(capabilities),
        )
        self.client.force_authenticate(user)
        return user

    # ── The guide report is a passenger list ─────────────────────────────────

    def test_the_guide_report_is_not_readable_just_by_being_on_the_dashboard(self):
        """It used to ride on the sailings' read-for-everyone rule — names,
        phone numbers and balances for anyone given, say, gallery photos."""
        self.staff_with("media")
        url = reverse("staff-package-guide-report", args=[self.package.id])
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_the_guide_report_needs_invoices(self):
        self.staff_with("invoices")
        url = reverse("staff-package-guide-report", args=[self.package.id])
        self.assertNotEqual(self.client.get(url).status_code, 403)

    # ── The room map: who is in the cabin is booking data ────────────────────

    def test_the_room_map_is_refused_to_accounts_with_neither_job(self):
        self.staff_with("media")
        url = reverse("staff-package-rooms", args=[self.package.id])
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_the_room_map_is_open_to_either_job(self):
        for caps in (("bookings",), ("packages",)):
            with self.subTest(caps=caps):
                self.staff_with(*caps)
                url = reverse("staff-package-rooms", args=[self.package.id])
                self.assertEqual(self.client.get(url).status_code, 200)

    # ── A sailing: schedule is one job, price is another ─────────────────────

    def test_packages_alone_can_move_a_sailing_but_not_price_it(self):
        self.staff_with("packages")

        ok = self.client.patch(self.detail, {"marketing_title": "Winter run"}, format="json")
        self.assertEqual(ok.status_code, 200, ok.data)

        refused = self.client.patch(self.detail, {"adult_price": "1.00"}, format="json")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("adult_price", refused.data)
        self.package.refresh_from_db()
        self.assertEqual(self.package.adult_price, Decimal("20000.00"))

    def test_pricing_alone_can_discount_a_sailing_but_not_move_it(self):
        self.staff_with("pricing")

        ok = self.client.patch(
            self.detail,
            {"discount_type": "percent", "discount_value": "10.00", "offer_label": "Last cabins"},
            format="json",
        )
        self.assertEqual(ok.status_code, 200, ok.data)

        refused = self.client.patch(self.detail, {"marketing_title": "Moved"}, format="json")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("marketing_title", refused.data)

    def test_an_unchanged_price_riding_along_with_a_schedule_edit_is_not_a_price_edit(self):
        """The dashboard PATCHes the whole form."""
        self.staff_with("packages")
        response = self.client.patch(
            self.detail,
            {"adult_price": "20000.00", "marketing_title": "Winter run"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)

    def test_neither_job_cannot_write_to_a_sailing_at_all(self):
        self.staff_with("bookings")
        response = self.client.patch(self.detail, {"marketing_title": "x"}, format="json")
        self.assertEqual(response.status_code, 403)
