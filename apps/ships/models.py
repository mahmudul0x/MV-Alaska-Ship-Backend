from django.db import models


class Ship(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"

    name = models.CharField(max_length=100, unique=True)
    layout_image = models.ImageField(upload_to="ships/layouts/", blank=True)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.ACTIVE
    )
    #: Helpline numbers printed on this ship's guide report & customer invoices,
    #: comma-separated. Editable from the staff dashboard; when blank the PDFs
    #: fall back to settings.AUTHORITY_PHONES. Per-ship so each ship can carry
    #: its own contact numbers.
    authority_phones = models.CharField(
        max_length=255,
        blank=True,
        help_text="Helpline numbers for the report/invoice header, comma-separated.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    @property
    def total_rooms(self):
        return self.rooms.count()

    @property
    def authority_phone_list(self):
        """The ship's helpline numbers as a clean list, falling back to the
        system default (settings.AUTHORITY_PHONES) when none are set — so a
        report/invoice never prints an empty helpline line."""
        from django.conf import settings

        raw = self.authority_phones or getattr(
            settings, "AUTHORITY_PHONES", ""
        )
        return [n.strip() for n in raw.split(",") if n.strip()]


class RoomType(models.Model):
    name = models.CharField(max_length=50, unique=True)
    max_adults = models.PositiveSmallIntegerField()
    max_kids = models.PositiveSmallIntegerField()
    base_price = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.name} (max {self.max_adults} adults, {self.max_kids} kids)"


class Room(models.Model):
    ship = models.ForeignKey(Ship, on_delete=models.PROTECT, related_name="rooms")
    room_type = models.ForeignKey(
        RoomType, on_delete=models.PROTECT, related_name="rooms"
    )
    room_number = models.CharField(max_length=20)
    floor_number = models.IntegerField(
        null=True, blank=True, help_text="1 = 1st floor (200-series), 2 = 2nd floor (300-series)."
    )

    class Meta:
        ordering = ["room_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["ship", "room_number"], name="uniq_room_number_per_ship"
            ),
        ]

    def __str__(self):
        return f"{self.ship.name} — Room {self.room_number}"


class FoodMenuItem(models.Model):
    """A dish the chef may serve on a given day/meal. Rows are a selection
    pool, not a fixed daily assignment — the chef picks from the active
    items for that (ship, day, meal_type) on the day."""

    class Day(models.TextChoices):
        DAY_1 = "day_1", "Day 1"
        DAY_2 = "day_2", "Day 2"
        DAY_3 = "day_3", "Day 3"

    class MealType(models.TextChoices):
        BREAKFAST = "breakfast", "Breakfast"
        SNACKS = "snacks", "Snacks"
        LUNCH = "lunch", "Lunch"
        DINNER = "dinner", "Dinner"

    ship = models.ForeignKey(
        Ship, on_delete=models.CASCADE, related_name="food_menu_items"
    )
    day = models.CharField(max_length=10, choices=Day.choices)
    meal_type = models.CharField(max_length=10, choices=MealType.choices)
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(
        default=True, help_text="Uncheck to hide without deleting."
    )
    order = models.PositiveSmallIntegerField(
        default=0, help_text="Display order within the same day/meal."
    )

    class Meta:
        ordering = ["ship", "day", "meal_type", "order", "id"]

    def __str__(self):
        return f"{self.ship.name} — {self.get_day_display()} {self.get_meal_type_display()}: {self.name}"
