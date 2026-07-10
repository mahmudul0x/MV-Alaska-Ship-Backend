"""All money-moving logic in one place: initiate, confirm, fail.

Invariants:
- A Payment row is only ever created by initiate_payment(); IPN/redirects can
  never create one (so forged notifications can't mint credits).
- process_payment_result() is idempotent — duplicate IPNs are no-ops thanks to
  the row lock + status gate, and paid_amount is always recomputed as a SUM.
- Every gateway verdict is persisted to Payment.gateway_payload for audit.
"""

import logging
from decimal import Decimal, InvalidOperation

import requests
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException

from . import invoices, sslcommerz
from .models import Payment
from .sslcommerz import GatewayError

logger = logging.getLogger(__name__)


class PaymentGatewayUnavailable(APIException):
    status_code = 502
    default_detail = "Payment gateway is unavailable. Please try again."
    default_code = "gateway_unavailable"


def initiate_payment(booking, payment_type, amount=None):
    """Create a PENDING Payment and a gateway session; returns (payment, url).

    The amount is decided server-side: full → the current due; partial → the
    serializer-validated amount (0 < amount <= due).
    """
    if payment_type == Payment.PaymentType.FULL:
        amount = booking.due_amount

    payment = Payment.objects.create(
        booking=booking,
        amount=amount,
        payment_type=payment_type,
        gateway="sslcommerz",
        status=Payment.Status.PENDING,
    )
    # pk exists only after create — tran_id is unique and booking-traceable.
    payment.transaction_id = f"{booking.booking_code}-P{payment.pk}"
    payment.save(update_fields=["transaction_id"])

    try:
        gateway_url = sslcommerz.create_session(payment)
    except (requests.RequestException, GatewayError, ValueError) as exc:
        payment.status = Payment.Status.FAILED
        payment.gateway_payload = {"error": f"session: {exc}"}
        payment.save()
        logger.error("SSLCommerz session failed for %s: %s", payment.transaction_id, exc)
        raise PaymentGatewayUnavailable()
    return payment, gateway_url


def process_payment_result(tran_id, val_id):
    """Verify a gateway result and credit the payment. Idempotent.

    Called from the IPN listener and the success redirect (both may fire for
    the same payment, possibly concurrently).
    """
    if not tran_id or not val_id:
        return None
    with transaction.atomic():
        try:
            payment = Payment.objects.select_for_update().get(transaction_id=tran_id)
        except Payment.DoesNotExist:
            logger.warning("Gateway result for unknown tran_id %s", tran_id)
            return None

        if payment.status == Payment.Status.SUCCESS:
            return payment  # duplicate notification — already credited

        try:
            data = sslcommerz.validate_payment(val_id)
        except (requests.RequestException, ValueError) as exc:
            # Leave PENDING: a later IPN retry / re-verify can still settle it.
            logger.error("Validation API error for %s: %s", tran_id, exc)
            return payment

        if _verdict_is_valid(payment, tran_id, data):
            payment.status = Payment.Status.SUCCESS
            payment.paid_at = timezone.now()
        else:
            logger.warning("Rejected gateway verdict for %s: %s", tran_id, data)
            payment.status = Payment.Status.FAILED
        payment.gateway_payload = data
        payment.save()  # SUCCESS → booking paid/due/status refresh (SUM-based)

        if payment.status == Payment.Status.SUCCESS:
            # After commit so email trouble can never roll back the payment.
            # Duplicate IPNs never reach here (SUCCESS gate above), so exactly
            # one invoice per settled payment.
            booking = payment.booking
            transaction.on_commit(lambda: invoices.create_and_send_invoice(booking))
    return payment


def _verdict_is_valid(payment, tran_id, data):
    """Every check must pass explicitly — anything missing fails closed."""
    try:
        amount_matches = Decimal(str(data.get("amount"))) == payment.amount
    except (InvalidOperation, TypeError):
        amount_matches = False
    return (
        data.get("status") in ("VALID", "VALIDATED")
        and data.get("tran_id") == tran_id
        and data.get("currency") == "BDT"
        and amount_matches
    )


def mark_payment_closed(tran_id, new_status):
    """Fail/cancel redirect: close out a PENDING payment. Never touches
    SUCCESS rows (a stray 'fail' redirect can't undo verified money)."""
    if not tran_id:
        return None
    updated = Payment.objects.filter(
        transaction_id=tran_id, status=Payment.Status.PENDING
    ).update(status=new_status)
    if updated:
        logger.info("Payment %s marked %s", tran_id, new_status)
        return Payment.objects.get(transaction_id=tran_id)
    return None
