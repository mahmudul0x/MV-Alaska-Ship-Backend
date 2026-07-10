from datetime import datetime, time, timedelta

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.ships.models import Room, Ship


class PackageQuerySet(models.QuerySet):
    def public(self):
        """Packages visible on the public API/website.

        Only OPEN, not-yet-finished tours. Packages past their booking cutoff
        stay visible (with is_bookable() False) so the calendar doesn't
        silently lose tours; draft/cancelled/completed never appear.
        """
        return self.filter(
            status=Package.Status.OPEN, end_date__gte=timezone.localdate()
        )


class Package(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    ship = models.ForeignKey(Ship, on_delete=models.PROTECT, related_name="packages")
    start_date = models.DateField()
    end_date = models.DateField()
    booking_cutoff_datetime = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Leave blank to auto-set to 12:00 PM (noon) the day before the "
            "tour start date."
        ),
    )
    adult_price = models.DecimalField(
        max_digits=10, decimal_places=2, help_text="Charge per adult for this package."
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.DRAFT
    )
    is_booking_open = models.BooleanField(
        default=True,
        help_text="Manual override: uncheck to close booking regardless of cutoff.",
    )
    # Admin-editable marketing copy so the public website's package cards can
    # be sourced from real, bookable Package rows instead of hardcoded content.
    marketing_title = models.CharField(
        max_length=100, blank=True, help_text='e.g. "Sundarbans Explorer".'
    )
    marketing_description = models.TextField(blank=True)
    hero_image = models.ImageField(upload_to="packages/hero/", blank=True)
    highlights = models.JSONField(
        default=list,
        blank=True,
        help_text='List of short strings, e.g. ["Mangrove safari", "Sunset dinner"].',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = PackageQuerySet.as_manager()

    class Meta:
        ordering = ["-start_date"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gt=models.F("start_date")),
                name="package_end_after_start",
            ),
        ]

    def __str__(self):
        return f"{self.ship.name}: {self.start_date} – {self.end_date}"

    def clean(self):
        if self.start_date and self.end_date and self.end_date <= self.start_date:
            raise ValidationError({"end_date": "End date must be after start date."})

    def default_cutoff(self):
        """Noon (Asia/Dhaka) the day before the tour starts — PRD §5.5."""
        naive = datetime.combine(self.start_date - timedelta(days=1), time(12, 0))
        return timezone.make_aware(naive, timezone.get_default_timezone())

    def save(self, *args, **kwargs):
        if self.start_date:
            if self.booking_cutoff_datetime is None:
                # First save (or explicitly cleared) — derive from start date.
                self.booking_cutoff_datetime = self.default_cutoff()
            elif self.pk:
                # On update: if the cutoff was still the auto-derived value for
                # the *previous* start date (i.e. never hand-picked), keep it in
                # sync when the dates move. A manually customized cutoff — one
                # that differs from its date's default — is left untouched.
                prev = (
                    Package.objects.filter(pk=self.pk)
                    .values("start_date", "booking_cutoff_datetime")
                    .first()
                )
                if prev and prev["start_date"] != self.start_date:
                    prev_default = timezone.make_aware(
                        datetime.combine(
                            prev["start_date"] - timedelta(days=1), time(12, 0)
                        ),
                        timezone.get_default_timezone(),
                    )
                    if prev["booking_cutoff_datetime"] == prev_default:
                        self.booking_cutoff_datetime = self.default_cutoff()
        super().save(*args, **kwargs)

    def is_bookable(self):
        """Single source of truth for whether new bookings are allowed.

        The public booking API (Phase 3) must check this; admins may still
        create bookings past the cutoff from the admin panel.
        """
        return (
            self.status == self.Status.OPEN
            and self.is_booking_open
            and self.booking_cutoff_datetime is not None
            and timezone.now() < self.booking_cutoff_datetime
        )

    is_bookable.boolean = True


class PackageRoom(models.Model):
    package = models.ForeignKey(
        Package, on_delete=models.CASCADE, related_name="package_rooms"
    )
    room = models.ForeignKey(
        Room, on_delete=models.PROTECT, related_name="package_rooms"
    )
    is_available = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["package", "room"], name="uniq_room_per_package"
            ),
        ]

    def __str__(self):
        return f"{self.package} — Room {self.room.room_number}"

    def clean(self):
        if self.package_id and self.room_id and self.room.ship_id != self.package.ship_id:
            raise ValidationError(
                {"room": "Room belongs to a different ship than this package."}
            )


class KidPricingRule(models.Model):
    """Admin-configurable kid pricing tiers (PRD §5.2).

    Age ranges are min-inclusive, max-exclusive: [0, 3) free, [3, 8) fixed,
    [8, 99) full adult. The 8-vs-9 boundary is data, never code.
    """

    class ChargeType(models.TextChoices):
        FREE = "free", "Free"
        FIXED = "fixed", "Fixed amount"
        FULL_ADULT = "full_adult", "Full adult charge"

    min_age = models.PositiveSmallIntegerField(help_text="Inclusive lower bound.")
    max_age = models.PositiveSmallIntegerField(help_text="Exclusive upper bound.")
    charge_type = models.CharField(max_length=10, choices=ChargeType.choices)
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Required only for fixed-amount rules.",
    )

    class Meta:
        ordering = ["min_age"]

    def __str__(self):
        return f"Age {self.min_age}–{self.max_age}: {self.get_charge_type_display()}"

    def clean(self):
        errors = {}
        if self.min_age is not None and self.max_age is not None:
            if self.min_age >= self.max_age:
                errors["max_age"] = "Max age must be greater than min age."
            else:
                overlap = (
                    KidPricingRule.objects.exclude(pk=self.pk)
                    .filter(min_age__lt=self.max_age, max_age__gt=self.min_age)
                    .first()
                )
                if overlap:
                    errors["min_age"] = f"Age range overlaps with: {overlap}"
        if self.charge_type == self.ChargeType.FIXED and self.amount is None:
            errors["amount"] = "Amount is required for fixed-amount rules."
        if errors:
            raise ValidationError(errors)

    @classmethod
    def rule_for_age(cls, age):
        return cls.objects.filter(min_age__lte=age, max_age__gt=age).first()
