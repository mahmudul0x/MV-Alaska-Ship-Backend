from datetime import datetime, time, timedelta

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields.ranges import RangeOperators
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
            # DB-level backstop for clean()'s overlap check: no two active
            # packages on one ship may cover the same nights, even via raw ORM
            # writes or two staff sessions racing. daterange() defaults to
            # half-open [), so same-day turnaround stays allowed.
            ExclusionConstraint(
                name="excl_ship_package_date_overlap",
                expressions=[
                    ("ship", RangeOperators.EQUAL),
                    (
                        models.Func(
                            models.F("start_date"),
                            models.F("end_date"),
                            function="daterange",
                        ),
                        RangeOperators.OVERLAPS,
                    ),
                ],
                condition=~models.Q(status__in=("draft", "cancelled")),
            ),
        ]

    def __str__(self):
        return f"{self.ship.name}: {self.start_date} – {self.end_date}"

    # Statuses that occupy the ship's calendar. DRAFT and CANCELLED packages
    # never conflict — only real (sellable or sailed) voyages do.
    ACTIVE_STATUSES = (Status.OPEN, Status.CLOSED, Status.COMPLETED)

    def clean(self):
        if self.start_date and self.end_date and self.end_date <= self.start_date:
            raise ValidationError({"end_date": "End date must be after start date."})
        # One ship cannot run two voyages over the same nights. Same-day
        # turnaround (this end_date == next start_date) is allowed, so the
        # comparison is half-open: [start_date, end_date).
        if (
            self.ship_id
            and self.start_date
            and self.end_date
            and self.status in self.ACTIVE_STATUSES
        ):
            overlap = (
                Package.objects.exclude(pk=self.pk)
                .filter(
                    ship_id=self.ship_id,
                    status__in=self.ACTIVE_STATUSES,
                    start_date__lt=self.end_date,
                    end_date__gt=self.start_date,
                )
                .first()
            )
            if overlap:
                raise ValidationError(
                    {
                        "start_date": (
                            "Dates overlap with another package on this ship: "
                            f"{overlap} — the same room would be sold twice."
                        )
                    }
                )
        # A cutoff after departure day would keep the public booking API
        # selling cabins while the ship is at sea (the cutoff is the only
        # time gate). Validate the value save() will actually store, so a
        # date move that resyncs an auto-derived cutoff isn't rejected.
        if self.start_date and self.booking_cutoff_datetime:
            effective = self._resolved_cutoff()
            if timezone.localdate(effective) > self.start_date:
                raise ValidationError(
                    {
                        "booking_cutoff_datetime": (
                            "Cutoff is after the departure date "
                            f"({self.start_date}) — bookings would stay open "
                            "during the voyage."
                        )
                    }
                )

    @staticmethod
    def cutoff_default_for(start_date):
        """Noon (Asia/Dhaka) the day before the given start date — PRD §5.5."""
        naive = datetime.combine(start_date - timedelta(days=1), time(12, 0))
        return timezone.make_aware(naive, timezone.get_default_timezone())

    def default_cutoff(self):
        return self.cutoff_default_for(self.start_date)

    def _resolved_cutoff(self):
        """The cutoff save() will store for the current field values.

        Blank derives from the start date. On update, a cutoff that was still
        the auto-derived default for the *previous* start date (i.e. never
        hand-picked) follows the dates when they move; a manually customized
        cutoff is kept as-is.
        """
        if self.booking_cutoff_datetime is None:
            return self.default_cutoff()
        if self.pk:
            prev = (
                Package.objects.filter(pk=self.pk)
                .values("start_date", "booking_cutoff_datetime")
                .first()
            )
            if (
                prev
                and prev["start_date"] != self.start_date
                and prev["booking_cutoff_datetime"]
                == self.cutoff_default_for(prev["start_date"])
            ):
                return self.default_cutoff()
        return self.booking_cutoff_datetime

    def save(self, *args, **kwargs):
        if self.start_date:
            self.booking_cutoff_datetime = self._resolved_cutoff()
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
            # Backstop for a mis-set cutoff: never sell a departed voyage,
            # whatever the cutoff says. Day-of sales (an admin extending the
            # cutoff to departure morning) stay possible.
            and self.start_date is not None
            and timezone.localdate() <= self.start_date
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
