"""Cancel unpaid PENDING bookings past the hold window, freeing their rooms.

Run periodically (cron on Railway). A booking with ANY successful payment is
partially/fully paid and is never touched here.
"""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.bookings.models import Booking, Payment


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
        cutoff = timezone.now() - timedelta(minutes=options["minutes"])
        stale = (
            Booking.objects.filter(
                status=Booking.Status.PENDING, created_at__lt=cutoff
            )
            .exclude(payments__status=Payment.Status.SUCCESS)
            .distinct()
        )
        cancelled = 0
        for booking in stale:
            booking.status = Booking.Status.CANCELLED
            booking.save()  # logs the transition in BookingStatusLog
            cancelled += 1
            self.stdout.write(f"cancelled {booking.booking_code}")
        self.stdout.write(self.style.SUCCESS(f"{cancelled} stale booking(s) cancelled."))
