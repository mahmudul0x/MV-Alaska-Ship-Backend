"""Cancel unpaid PENDING bookings past the hold window, freeing their rooms.

Run periodically (cron on Railway). A booking with ANY successful payment is
partially/fully paid and is never touched here. A booking with a *live*
gateway session (a PENDING payment newer than PAYMENT_SESSION_MINUTES) is
also spared — the customer is at the checkout page, and cancelling now would
resell the room while their money is in flight.
"""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.bookings.models import Booking, Payment


def expire_booking(booking_pk, cutoff, session_cutoff):
    """Cancel one stale hold. Returns the booking if cancelled, else None.

    Every condition from the scan is re-checked under a row lock: an IPN can
    settle a payment between the scan and this write, and an unguarded
    full-field save would then cancel a paid booking and overwrite its
    paid_amount back to zero. Only status is ever written from here — a
    hold expiry must never touch money fields.
    """
    with transaction.atomic():
        booking = (
            Booking.objects.select_for_update().filter(pk=booking_pk).first()
        )
        if (
            booking is None
            or booking.status != Booking.Status.PENDING
            or booking.created_at >= cutoff
            or booking.payments.filter(status=Payment.Status.SUCCESS).exists()
            or booking.payments.filter(
                status=Payment.Status.PENDING, created_at__gte=session_cutoff
            ).exists()
        ):
            return None
        booking.status = Booking.Status.CANCELLED
        booking.save(  # logs the transition in BookingStatusLog
            update_fields=["status", "updated_at"]
        )
        return booking


class Command(BaseCommand):
    help = "Cancel PENDING bookings with no successful payment past the hold window."

    def add_arguments(self, parser):
        parser.add_argument(
            "--minutes",
            type=int,
            default=settings.BOOKING_HOLD_MINUTES,
            help="Hold window in minutes (default: BOOKING_HOLD_MINUTES).",
        )

    def handle(self, *args, **options):
        now = timezone.now()
        cutoff = now - timedelta(minutes=options["minutes"])
        session_cutoff = now - timedelta(minutes=settings.PAYMENT_SESSION_MINUTES)
        stale_ids = list(
            Booking.objects.filter(
                status=Booking.Status.PENDING, created_at__lt=cutoff
            )
            .exclude(payments__status=Payment.Status.SUCCESS)
            # Live gateway session: both conditions on the SAME payment row.
            .exclude(
                payments__status=Payment.Status.PENDING,
                payments__created_at__gte=session_cutoff,
            )
            .distinct()
            .values_list("pk", flat=True)
        )
        cancelled = 0
        for booking_pk in stale_ids:
            booking = expire_booking(booking_pk, cutoff, session_cutoff)
            if booking is not None:
                cancelled += 1
                self.stdout.write(f"cancelled {booking.booking_code}")
        self.stdout.write(self.style.SUCCESS(f"{cancelled} stale booking(s) cancelled."))
