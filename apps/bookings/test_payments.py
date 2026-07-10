from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.testing import ThrottlelessTestMixin

from .models import Booking, Payment
from .sslcommerz import GatewayError
from .test_api import build_fixtures

GATEWAY_URL = "https://sandbox.sslcommerz.com/gwprocess/testsession"


class PaymentTestCase(ThrottlelessTestMixin, APITestCase):
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

    def make_booking(self, room=None, adults=2, kids=None):
        booking = Booking(
            customer_name="Rahim Uddin",
            phone="01700000000",
            email="rahim@example.com",
            package=self.package,
            room=room or self.room_4p,
            adult_count=adults,
            kid_details=kids or [],
        )
        booking.full_clean()
        booking.save()
        return booking  # 4P, 2 adults: total = 3500 + 6000 = 9500

    def initiate(self, booking, payload):
        with patch(
            "apps.bookings.sslcommerz.create_session", return_value=GATEWAY_URL
        ):
            return self.client.post(
                f"/api/bookings/{booking.booking_code}/pay/", payload, format="json"
            )

    def verdict(self, payment, **overrides):
        data = {
            "status": "VALID",
            "tran_id": payment.transaction_id,
            "val_id": "VAL123",
            "amount": str(payment.amount),
            "currency": "BDT",
            "card_type": "VISA",
        }
        data.update(overrides)
        return data

    def send_ipn(self, payment, verdict=None, ipn_status="VALID"):
        with patch(
            "apps.bookings.sslcommerz.validate_payment",
            return_value=verdict or self.verdict(payment),
        ) as mock_validate:
            response = self.client.post(
                "/api/payments/ipn/",
                {
                    "tran_id": payment.transaction_id,
                    "val_id": "VAL123",
                    "status": ipn_status,
                },
            )
        return response, mock_validate


class InitiatePaymentTests(PaymentTestCase):
    def test_full_payment_amount_is_server_side_due(self):
        booking = self.make_booking()
        response = self.initiate(booking, {"payment_type": "full", "amount": "1.00"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["gateway_url"], GATEWAY_URL)
        self.assertEqual(response.data["amount"], "9500.00")  # client "1.00" ignored
        payment = Payment.objects.get(transaction_id=response.data["tran_id"])
        self.assertEqual(payment.amount, Decimal("9500.00"))
        self.assertEqual(payment.status, Payment.Status.PENDING)
        self.assertEqual(
            payment.transaction_id, f"{booking.booking_code}-P{payment.pk}"
        )

    def test_partial_payment_validations(self):
        booking = self.make_booking()
        cases = [
            ({"payment_type": "partial"}, "amount"),  # missing
            ({"payment_type": "partial", "amount": "0"}, "amount"),  # zero
            ({"payment_type": "partial", "amount": "-5"}, "amount"),  # negative
            ({"payment_type": "partial", "amount": "9500.01"}, "amount"),  # > due
        ]
        for payload, field in cases:
            response = self.initiate(booking, payload)
            self.assertEqual(response.status_code, 400, payload)
            self.assertIn(field, response.data)
        ok = self.initiate(booking, {"payment_type": "partial", "amount": "3000"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.data["amount"], "3000.00")

    def test_cancelled_booking_cannot_pay(self):
        booking = self.make_booking()
        booking.status = Booking.Status.CANCELLED
        booking.save()
        response = self.initiate(booking, {"payment_type": "full"})
        self.assertEqual(response.status_code, 400)

    def test_gateway_failure_marks_payment_failed_and_502(self):
        booking = self.make_booking()
        with patch(
            "apps.bookings.sslcommerz.create_session",
            side_effect=GatewayError("store auth failed"),
        ):
            response = self.client.post(
                f"/api/bookings/{booking.booking_code}/pay/",
                {"payment_type": "full"},
                format="json",
            )
        self.assertEqual(response.status_code, 502)
        payment = Payment.objects.get(booking=booking)
        self.assertEqual(payment.status, Payment.Status.FAILED)

    def test_nothing_due_rejected(self):
        booking = self.make_booking()
        Payment.objects.create(
            booking=booking,
            amount=booking.total_amount,
            payment_type=Payment.PaymentType.FULL,
            status=Payment.Status.SUCCESS,
        )
        response = self.initiate(booking, {"payment_type": "full"})
        self.assertEqual(response.status_code, 400)


class ProcessResultTests(PaymentTestCase):
    def start_payment(self, booking, amount):
        response = self.initiate(
            booking, {"payment_type": "partial", "amount": str(amount)}
        )
        return Payment.objects.get(transaction_id=response.data["tran_id"])

    def test_valid_ipn_credits_payment_and_updates_booking(self):
        booking = self.make_booking()
        payment = self.start_payment(booking, "3000")
        response, _ = self.send_ipn(payment)
        self.assertEqual(response.status_code, 200)

        payment.refresh_from_db()
        booking.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.SUCCESS)
        self.assertIsNotNone(payment.paid_at)
        self.assertEqual(booking.paid_amount, Decimal("3000.00"))
        self.assertEqual(booking.due_amount, Decimal("6500.00"))
        self.assertEqual(booking.status, Booking.Status.PARTIALLY_PAID)

    def test_second_partial_payment_reaches_fully_paid(self):
        booking = self.make_booking()
        first = self.start_payment(booking, "3000")
        self.send_ipn(first)
        second = self.start_payment(booking, "6500")
        self.send_ipn(second)

        booking.refresh_from_db()
        self.assertEqual(booking.paid_amount, Decimal("9500.00"))
        self.assertEqual(booking.due_amount, Decimal("0.00"))
        self.assertEqual(booking.status, Booking.Status.FULLY_PAID)

    def test_duplicate_ipn_credits_only_once(self):
        booking = self.make_booking()
        payment = self.start_payment(booking, "3000")
        verdict = self.verdict(payment)
        total_validations = 0
        for _ in range(5):
            _, mock_validate = self.send_ipn(payment, verdict=verdict)
            total_validations += mock_validate.call_count

        booking.refresh_from_db()
        self.assertEqual(booking.paid_amount, Decimal("3000.00"))
        self.assertEqual(Payment.objects.filter(booking=booking).count(), 1)
        self.assertEqual(total_validations, 1)  # 4 duplicates gated before validation

    def test_amount_tampering_rejected(self):
        booking = self.make_booking()
        payment = self.start_payment(booking, "3000")
        response, _ = self.send_ipn(
            payment, verdict=self.verdict(payment, amount="1.00")
        )
        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        booking.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.FAILED)
        self.assertEqual(booking.paid_amount, Decimal("0.00"))
        self.assertEqual(booking.status, Booking.Status.PENDING)

    def test_invalid_verdict_rejected(self):
        booking = self.make_booking()
        payment = self.start_payment(booking, "3000")
        self.send_ipn(payment, verdict=self.verdict(payment, status="INVALID"))
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.FAILED)
        booking.refresh_from_db()
        self.assertEqual(booking.paid_amount, Decimal("0.00"))

    def test_unknown_tran_id_credits_nothing(self):
        with patch("apps.bookings.sslcommerz.validate_payment") as mock_validate:
            response = self.client.post(
                "/api/payments/ipn/",
                {"tran_id": "BK-FAKE-P999", "val_id": "V1", "status": "VALID"},
            )
        self.assertEqual(response.status_code, 200)
        mock_validate.assert_not_called()
        self.assertEqual(Payment.objects.count(), 0)

    def test_failed_ipn_status_closes_pending_payment(self):
        booking = self.make_booking()
        payment = self.start_payment(booking, "3000")
        self.client.post(
            "/api/payments/ipn/",
            {"tran_id": payment.transaction_id, "val_id": "V1", "status": "FAILED"},
        )
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.FAILED)

    def test_stray_fail_cannot_undo_verified_success(self):
        booking = self.make_booking()
        payment = self.start_payment(booking, "3000")
        self.send_ipn(payment)
        self.client.post(
            "/api/payments/fail/", {"tran_id": payment.transaction_id}
        )
        payment.refresh_from_db()
        booking.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.SUCCESS)
        self.assertEqual(booking.paid_amount, Decimal("3000.00"))


class RedirectViewTests(PaymentTestCase):
    def test_success_redirect_processes_and_points_to_frontend(self):
        booking = self.make_booking()
        payment = Payment.objects.get(
            transaction_id=self.initiate(booking, {"payment_type": "full"}).data[
                "tran_id"
            ]
        )
        with patch(
            "apps.bookings.sslcommerz.validate_payment",
            return_value=self.verdict(payment),
        ):
            response = self.client.post(
                "/api/payments/success/",
                {"tran_id": payment.transaction_id, "val_id": "VAL123"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/payment/success?booking=", response.url)
        self.assertIn(booking.booking_code, response.url)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.FULLY_PAID)

    def test_cancel_redirect_marks_payment_cancelled(self):
        booking = self.make_booking()
        payment = Payment.objects.get(
            transaction_id=self.initiate(booking, {"payment_type": "full"}).data[
                "tran_id"
            ]
        )
        response = self.client.post(
            "/api/payments/cancel/", {"tran_id": payment.transaction_id}
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/payment/cancel?booking=", response.url)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.CANCELLED)


class ExpireStaleBookingsTests(PaymentTestCase):
    def age_booking(self, booking, minutes):
        Booking.objects.filter(pk=booking.pk).update(
            created_at=timezone.now() - timedelta(minutes=minutes)
        )

    def test_old_unpaid_pending_cancelled_and_room_freed(self):
        booking = self.make_booking()
        self.age_booking(booking, 60)
        call_command("expire_stale_bookings")
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CANCELLED)
        self.assertEqual(booking.status_logs.count(), 2)  # create + cancel

        rebook = Booking(
            customer_name="Karim",
            phone="01800000000",
            email="karim@example.com",
            package=self.package,
            room=booking.room,
            adult_count=1,
        )
        rebook.full_clean()
        rebook.save()  # room is free again — must not raise

    def test_partially_paid_and_fresh_bookings_untouched(self):
        paid = self.make_booking()
        payment = self.start_payment_for(paid, "3000")
        self.send_ipn(payment)
        self.age_booking(paid, 60)

        fresh = self.make_booking(room=self.room_2p, adults=2)

        call_command("expire_stale_bookings")
        paid.refresh_from_db()
        fresh.refresh_from_db()
        self.assertEqual(paid.status, Booking.Status.PARTIALLY_PAID)
        self.assertEqual(fresh.status, Booking.Status.PENDING)

    def start_payment_for(self, booking, amount):
        response = self.initiate(
            booking, {"payment_type": "partial", "amount": amount}
        )
        return Payment.objects.get(transaction_id=response.data["tran_id"])
