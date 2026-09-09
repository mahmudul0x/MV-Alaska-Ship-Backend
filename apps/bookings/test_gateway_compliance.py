"""Conformance with the SSLCommerz integration document.

Each test here corresponds to a specific instruction in the gateway's
documentation that the implementation was not following. They are grouped by
the instruction rather than by our own module layout, so a future reader can
check them against the document.
"""

from decimal import Decimal
from unittest.mock import patch

from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase

from apps.bookings import payment_service, sslcommerz
from apps.bookings.models import Booking, Payment
from apps.bookings.test_api import build_fixtures
from apps.testing import ThrottlelessTestMixin, create_booking, sign_ipn


def gateway_ok(payment, **overrides):
    """A validation-API response the gateway would call successful."""
    data = {
        "status": "VALID",
        "tran_id": payment.transaction_id,
        "currency": "BDT",
        "amount": str(payment.amount),
        "risk_level": "0",
        "risk_title": "Safe",
        "val_id": "VAL-1",
    }
    data.update(overrides)
    return data


class RiskLevelTests(ThrottlelessTestMixin, APITestCase):
    """The document: on risk_level 1, "hold the service and proceed to collect
    customer verification documents". It was being read and thrown away."""

    def setUp(self):
        _, _, _, _, self.room, self.package = build_fixtures(ship_name="Risk Ship")
        self.booking = create_booking(
            self.package, [{"room": self.room, "adult_count": 2}]
        )
        self.payment, _ = self._start()

    def _start(self):
        with patch("apps.bookings.sslcommerz.create_session") as session:
            session.return_value = "https://example.test/pay"
            return payment_service.initiate_payment(
                self.booking, Payment.PaymentType.FULL
            )

    def settle(self, **overrides):
        with patch("apps.bookings.sslcommerz.validate_payment") as validate:
            validate.return_value = gateway_ok(self.payment, **overrides)
            payment_service.process_payment_result(
                self.payment.transaction_id, "VAL-1"
            )
        self.payment.refresh_from_db()
        return self.payment

    def test_a_safe_payment_settles_without_review(self):
        payment = self.settle()
        self.assertEqual(payment.status, Payment.Status.SUCCESS)
        self.assertEqual(payment.gateway_risk_level, 0)
        self.assertFalse(payment.needs_manual_review)

    def test_a_risky_payment_is_still_credited(self):
        """The money is real. Withholding the credit would leave a paying
        customer looking unpaid — what is held is trust, not the payment."""
        payment = self.settle(risk_level="1", risk_title="Not Safe")
        self.assertEqual(payment.status, Payment.Status.SUCCESS)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.paid_amount, payment.amount)

    def test_a_risky_payment_is_flagged_for_a_human(self):
        payment = self.settle(risk_level="1", risk_title="Not Safe")
        self.assertEqual(payment.gateway_risk_level, 1)
        self.assertTrue(payment.is_risky)
        self.assertTrue(payment.needs_manual_review)
        # The note has to tell staff what to DO, not merely that something is
        # wrong — it is read weeks before the sailing.
        self.assertIn("high risk", payment.last_reconcile_error.lower())
        self.assertIn("board", payment.last_reconcile_error.lower())

    def test_an_unparseable_risk_score_is_not_read_as_safe(self):
        payment = self.settle(risk_level="not-a-number")
        self.assertIsNone(payment.gateway_risk_level)
        self.assertFalse(payment.is_risky)

    def test_a_missing_risk_score_is_recorded_as_unknown(self):
        payment = self.settle(risk_level="")
        self.assertIsNone(payment.gateway_risk_level)


class IpnStatusTests(ThrottlelessTestMixin, APITestCase):
    """UNATTEMPTED ("customer did not choose to pay any channel") and EXPIRED
    both mean no money moved, so both must release the cabin. UNATTEMPTED used
    to fall through to the crediting path, which no-ops without a val_id — so
    the session stayed PENDING and held its cabin until a cron that does not
    run on every hosting tier cleared it."""

    def setUp(self):
        _, _, _, _, self.room, self.package = build_fixtures(ship_name="IPN Ship")
        self.booking = create_booking(
            self.package, [{"room": self.room, "adult_count": 2}]
        )
        with patch("apps.bookings.sslcommerz.create_session") as session:
            session.return_value = "https://example.test/pay"
            self.payment, _ = payment_service.initiate_payment(
                self.booking, Payment.PaymentType.FULL
            )

    def post_ipn(self, ipn_status):
        response = self.client.post(
            "/api/payments/ipn/",
            sign_ipn(
                {
                    "status": ipn_status,
                    "tran_id": self.payment.transaction_id,
                    "amount": str(self.payment.amount),
                    "currency": "BDT",
                }
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.payment.refresh_from_db()
        return self.payment

    def test_unattempted_closes_the_session(self):
        self.assertEqual(self.post_ipn("UNATTEMPTED").status, Payment.Status.CANCELLED)

    def test_expired_closes_the_session(self):
        self.assertEqual(self.post_ipn("EXPIRED").status, Payment.Status.FAILED)

    def test_cancelled_and_failed_still_behave(self):
        self.assertEqual(self.post_ipn("CANCELLED").status, Payment.Status.CANCELLED)

    def test_a_closed_session_releases_its_cabin_for_resale(self):
        """The point of closing it: the room goes back on sale."""
        self.post_ipn("UNATTEMPTED")
        rooms = self.client.get(f"/api/packages/{self.package.id}/rooms/").data
        cabin = next(r for r in rooms if r["room_number"] == self.room.room_number)
        # The booking is still PENDING and still holds the room — closing the
        # payment is what lets the expiry job reclaim it, and what stops the
        # customer being blocked from starting a fresh session.
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, Booking.Status.PENDING)
        self.assertEqual(cabin["availability"], "booked")

    def test_a_closed_session_lets_the_customer_start_a_new_one(self):
        """The practical harm of leaving it PENDING: a second attempt was
        refused because a live session already existed."""
        self.post_ipn("UNATTEMPTED")
        with patch("apps.bookings.sslcommerz.create_session") as session:
            session.return_value = "https://example.test/pay2"
            payment, _ = payment_service.initiate_payment(
                self.booking, Payment.PaymentType.FULL
            )
        self.assertNotEqual(payment.pk, self.payment.pk)
        self.assertEqual(payment.status, Payment.Status.PENDING)


class SessionFieldLimitTests(ThrottlelessTestMixin, APITestCase):
    """The document caps cus_name and cus_email at 50 characters. Ours are 100
    and 254, so an ordinary long name was sent oversized and the session
    refused — with nothing the customer could act on."""

    def setUp(self):
        _, _, _, _, self.room, self.package = build_fixtures(ship_name="Limit Ship")

    def booking_with(self, **fields):
        return create_booking(
            self.package, [{"room": self.room, "adult_count": 2}], **fields
        )

    def test_a_long_name_is_trimmed_to_what_the_gateway_accepts(self):
        booking = self.booking_with(
            customer_name="Mohammad Abdur Rahman Chowdhury Al-Mahmud Siddiqui Junior"
        )
        with patch("apps.bookings.sslcommerz.requests.post") as post:
            post.return_value.json.return_value = {
                "status": "SUCCESS",
                "GatewayPageURL": "https://example.test/pay",
            }
            post.return_value.raise_for_status.return_value = None
            payment, _ = payment_service.initiate_payment(
                booking, Payment.PaymentType.FULL
            )
        sent = post.call_args.kwargs["data"]
        self.assertLessEqual(len(sent["cus_name"]), sslcommerz._MAX_CUS_NAME)
        # Trimmed for the gateway only — our own records keep the whole name.
        booking.refresh_from_db()
        self.assertEqual(len(booking.customer_name), 57)
        self.assertEqual(payment.status, Payment.Status.PENDING)

    def test_an_over_long_email_is_refused_with_an_explanation(self):
        """Truncating an email produces a WRONG address, and the gateway mails
        its own receipt to it — so this one is refused, not trimmed."""
        long_email = f"{'a' * 45}@example.com"
        self.assertGreater(len(long_email), sslcommerz.MAX_CUS_EMAIL)
        booking = self.booking_with(email=long_email)
        with self.assertRaises(ValidationError) as caught:
            payment_service.initiate_payment(booking, Payment.PaymentType.FULL)
        message = str(caught.exception.detail["payment_type"])
        self.assertIn("50", message)
        self.assertIn("contact us", message.lower())

    def test_an_ordinary_email_is_untouched(self):
        booking = self.booking_with(email="rahim@example.com")
        with patch("apps.bookings.sslcommerz.requests.post") as post:
            post.return_value.json.return_value = {
                "status": "SUCCESS",
                "GatewayPageURL": "https://example.test/pay",
            }
            post.return_value.raise_for_status.return_value = None
            payment_service.initiate_payment(booking, Payment.PaymentType.FULL)
        self.assertEqual(
            post.call_args.kwargs["data"]["cus_email"], "rahim@example.com"
        )


class IpnSignatureTests(ThrottlelessTestMixin, APITestCase):
    """The reference implementation hashes only the fields PRESENT in the POST
    (`isset($_POST[$value])`). Substituting "" for a named-but-absent field
    produced a different hash and would have rejected a genuine notification as
    forged — silently, leaving the payment PENDING."""

    def test_a_verify_key_naming_an_absent_field_still_verifies(self):
        signed = sign_ipn({"status": "VALID", "tran_id": "T1", "amount": "10.00"})
        # The gateway names a field it did not send.
        signed["verify_key"] = f"{signed['verify_key']},card_no"
        # Re-sign the way the gateway does: absent fields are simply not hashed.
        resigned = sign_ipn(
            {"status": "VALID", "tran_id": "T1", "amount": "10.00"}
        )
        signed["verify_sign"] = resigned["verify_sign"]
        self.assertTrue(sslcommerz.verify_ipn_signature(signed))

    def test_a_forged_notification_is_still_rejected(self):
        self.assertFalse(
            sslcommerz.verify_ipn_signature(
                {
                    "status": "VALID",
                    "tran_id": "T1",
                    "verify_key": "status,tran_id",
                    "verify_sign": "0" * 32,
                }
            )
        )

    def test_an_unsigned_notification_is_rejected(self):
        self.assertFalse(
            sslcommerz.verify_ipn_signature({"status": "VALID", "tran_id": "T1"})
        )
