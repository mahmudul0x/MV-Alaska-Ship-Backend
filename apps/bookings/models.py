import secrets
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q, Sum

from apps.packages.models import Package, PackageRoom
from apps.ships.models import Room

from .pricing import calculate_total


def generate_booking_code():
    """Unique human-readable code; also the SSLCommerz tran_id base (Phase 4)."""
    while True:
        code = "BK-" + secrets.token_hex(4).upper()
        if not Booking.objects.filter(booking_code=code).exists():
            return code


class Booking(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"  # created, no successful payment yet
        PARTIALLY_PAID = "partially_paid", "Partially paid"
        FULLY_PAID = "fully_paid", "Fully paid"
        CANCELLED = "cancelled", "Cancelled"
        COMPLETED = "completed", "Completed"

    booking_code = models.CharField(max_length=20, unique=True, blank=True)
    customer_name = models.CharField(max_length=100)
    phone = models.CharField(max_length=20)
    email = models.EmailField()
    package = models.ForeignKey(
        Package, on_delete=models.PROTECT, related_name="bookings"
    )
    room = models.ForeignKey(Room, on_delete=models.PROTECT, related_name="bookings")
    adult_count = models.PositiveSmallIntegerField()
    kid_details = models.JSONField(
        default=list,
        blank=True,
        help_text='List of kids as [{"age": 5}, ...]. Ages drive kid pricing.',
    )
    total_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    paid_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    due_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # One active booking per room per package (cancelled frees the room).
            models.UniqueConstraint(
                fields=["package", "room"],
                condition=~Q(status="cancelled"),
                name="uniq_active_booking_per_package_room",
            ),
        ]

    def __str__(self):
        return f"{self.booking_code} — {self.customer_name}"

    @property
    def kid_ages(self):
        return [kid["age"] for kid in self.kid_details]

    @property
    def total_pax(self):
        return self.adult_count + len(self.kid_details)

    def clean(self):
        if not self.package_id or not self.room_id:
            return  # field-level "required" errors already cover these

        errors = {}
        room_type = self.room.room_type

        if self.adult_count is not None:
            if self.adult_count < 1:
                errors["adult_count"] = "At least one adult is required."
            elif self.adult_count > room_type.max_adults:
                errors["adult_count"] = (
                    f"{room_type.name} allows at most {room_type.max_adults} adults."
                )

        if not isinstance(self.kid_details, list) or not all(
            isinstance(kid, dict) and isinstance(kid.get("age"), int) and kid["age"] >= 0
            for kid in self.kid_details
        ):
            errors["kid_details"] = (
                'kid_details must be a list like [{"age": 5}] with non-negative '
                "integer ages."
            )
        elif len(self.kid_details) > room_type.max_kids:
            errors["kid_details"] = (
                f"{room_type.name} allows at most {room_type.max_kids} kids."
            )

        if not PackageRoom.objects.filter(
            package_id=self.package_id, room_id=self.room_id, is_available=True
        ).exists():
            errors["room"] = "This room is not available in the selected package."

        if errors:
            raise ValidationError(errors)

        # Server-side pricing — client-submitted amounts are never trusted.
        self.total_amount = calculate_total(
            room_type, self.package, self.adult_count, self.kid_ages
        )

    def save(self, *args, changed_by=None, **kwargs):
        if not self.booking_code:
            self.booking_code = generate_booking_code()
        self.due_amount = self.total_amount - self.paid_amount

        old_status = None
        if self.pk:
            old_status = (
                Booking.objects.filter(pk=self.pk)
                .values_list("status", flat=True)
                .first()
            )
        super().save(*args, **kwargs)

        if old_status != self.status:
            BookingStatusLog.objects.create(
                booking=self,
                old_status=old_status or "",
                new_status=self.status,
                changed_by=changed_by,
            )

    def refresh_paid_amount(self):
        """Recompute paid/due from successful payments (never incremented) and
        move the booking through the payment statuses.

        Runs under a lock on the booking row: two payments settling in
        overlapping transactions would otherwise each SUM before the other
        commits (READ COMMITTED), and the last writer would record only its
        own amount — settled money vanishing from paid_amount."""
        with transaction.atomic():
            booking = Booking.objects.select_for_update().get(pk=self.pk)
            paid = booking.payments.filter(status=Payment.Status.SUCCESS).aggregate(
                total=Sum("amount")
            )["total"] or Decimal("0.00")
            booking.paid_amount = paid
            if booking.status not in (self.Status.CANCELLED, self.Status.COMPLETED):
                if paid >= booking.total_amount and paid > 0:
                    booking.status = self.Status.FULLY_PAID
                elif paid > 0:
                    booking.status = self.Status.PARTIALLY_PAID
            booking.save(
                update_fields=["paid_amount", "due_amount", "status", "updated_at"]
            )
            # Keep the caller's instance in sync with what was persisted.
            self.paid_amount = booking.paid_amount
            self.due_amount = booking.due_amount
            self.status = booking.status


class BookingStatusLog(models.Model):
    """Audit trail of booking status changes (PRD §6)."""

    booking = models.ForeignKey(
        Booking, on_delete=models.CASCADE, related_name="status_logs"
    )
    old_status = models.CharField(max_length=20, blank=True)
    new_status = models.CharField(max_length=20)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.booking.booking_code}: {self.old_status or '—'} → {self.new_status}"


class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    class PaymentType(models.TextChoices):
        FULL = "full", "Full"
        PARTIAL = "partial", "Partial"

    booking = models.ForeignKey(
        Booking, on_delete=models.PROTECT, related_name="payments"
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_type = models.CharField(max_length=10, choices=PaymentType.choices)
    gateway = models.CharField(max_length=30, default="sslcommerz")
    transaction_id = models.CharField(max_length=100, blank=True)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    gateway_payload = models.JSONField(
        default=dict, blank=True, help_text="Raw gateway (SSLCommerz) response."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # One payment row per gateway tran_id — duplicate IPNs can never
            # create a second credit.
            models.UniqueConstraint(
                fields=["transaction_id"],
                condition=~Q(transaction_id=""),
                name="uniq_payment_transaction_id",
            ),
        ]

    def __str__(self):
        return f"{self.booking.booking_code}: {self.amount} ({self.status})"

    def clean(self):
        if self.amount is not None and self.amount <= 0:
            raise ValidationError({"amount": "Amount must be positive."})

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.booking.refresh_paid_amount()


class Invoice(models.Model):
    class SentVia(models.TextChoices):
        EMAIL = "email", "Email"
        WHATSAPP = "whatsapp", "WhatsApp"  # Phase 2

    booking = models.ForeignKey(
        Booking, on_delete=models.CASCADE, related_name="invoices"
    )
    sent_via = models.CharField(
        max_length=10, choices=SentVia.choices, default=SentVia.EMAIL
    )
    sent_at = models.DateTimeField(null=True, blank=True)
    pdf_file = models.FileField(upload_to="invoices/", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Invoice for {self.booking.booking_code} via {self.sent_via}"
