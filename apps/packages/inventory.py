"""How many cabins a sailing still has for sale.

"Free" here is the customer's meaning, and it has to match exactly what the
deck plan shows as selectable — a card advertising "29 cabins free" beside a
plan with 12 of them greyed out is worse than showing no number at all. So the
rule below is the same one PackageRoomSerializer.get_availability applies, in
one place both can use:

    free = in inventory (is_available)
           AND not withheld by an admin (is_blocked)
           AND not held by an active booking

The annotation is what the list endpoint uses — counting per package would be a
query per sailing on a page that renders every open one.
"""

from django.db.models import Count, Exists, IntegerField, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce

from apps.bookings.models import BookingRoom

from .models import PackageRoom


def _bookable_rooms(package_filter):
    """PackageRooms on sale and not held, for whichever package(s) the caller
    points at."""
    held = BookingRoom.objects.filter(
        package_id=OuterRef("package_id"),
        room_id=OuterRef("room_id"),
        is_active=True,
    )
    return (
        PackageRoom.objects.filter(package_filter, is_available=True, is_blocked=False)
        .annotate(is_held=Exists(held))
        .filter(is_held=False)
    )


def with_cabin_counts(queryset):
    """Annotate `cabins_total` and `cabins_free` onto a Package queryset.

    One extra subquery for the whole page, not one per sailing.
    """
    free = (
        _bookable_rooms(Q(package_id=OuterRef("pk")))
        .order_by()
        .values("package_id")
        .annotate(n=Count("pk"))
        .values("n")
    )
    return queryset.annotate(
        cabins_total=Count(
            "package_rooms",
            filter=Q(package_rooms__is_available=True),
            distinct=True,
        ),
        # Coalesce: a sailing with no free cabin produces no subquery row at
        # all, and NULL would render as "null free" on the card.
        cabins_free=Coalesce(Subquery(free, output_field=IntegerField()), 0),
    )


def count_free_cabins(package):
    """The same count for a single Package that was not annotated — the staff
    dashboard and the tests build those directly."""
    return _bookable_rooms(Q(package=package)).count()
