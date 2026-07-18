from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
from rest_framework import serializers

from apps.packages.models import Package, PackageRoom
from apps.ships.models import Room

from .exceptions import RoomUnavailable
from .models import Booking, BookingRoom, Invoice, Payment
from .pricing import booking_price_breakdown, snapshot_booking_breakdown

#: Decimal → string so DRF's encoder never floats money. The API `price_breakdown`
#: field carries the whole booking's per-room breakdown plus its grand total.
breakdown_as_json = snapshot_booking_breakdown


class KidSerializer(serializers.Serializer):
    age = serializers.IntegerField(min_value=0, max_value=17)


class BookingRoomInputSerializer(serializers.Serializer):
    """One room within a booking: which cabin, and that cabin's own party."""

    room_id = serializers.PrimaryKeyRelatedField(
        queryset=Room.objects.all(), source="room"
    )
    adult_count = serializers.IntegerField(min_value=1)
    kid_details = KidSerializer(many=True, required=False)


class BookingQuoteSerializer(serializers.Serializer):
    """Validates a prospective (multi-room) booking and prices it — no DB writes.

    Also the base of BookingCreateSerializer, so quote and create can never
    disagree on validation or price.
    """

    # Staff subclasses flip this off: admins may book past the cutoff
    # (PRD §5.5 manual override); the public API always enforces it.
    enforce_cutoff = True

    package_id = serializers.PrimaryKeyRelatedField(
        queryset=Package.objects.public(), source="package"
    )
    rooms = BookingRoomInputSerializer(many=True)

    def validate_rooms(self, rooms):
        if not rooms:
            raise serializers.ValidationError("At least one room is required.")
        # The same physical cabin cannot be listed twice in one booking — it
        # would double-count pricing and then fail the (package, room) unique
        # constraint at save with a confusing "unavailable" instead.
        room_ids = [entry["room"].pk for entry in rooms]
        if len(room_ids) != len(set(room_ids)):
            raise serializers.ValidationError(
                "A room may only be selected once per booking."
            )
        return rooms

    def validate(self, attrs):
        package = attrs["package"]
        rooms = attrs["rooms"]

        if self.enforce_cutoff and not package.is_bookable():
            raise serializers.ValidationError(
                {"package_id": "Booking is closed for this package."}
            )

        for index, entry in enumerate(rooms):
            self._validate_room(package, index, entry)

        return attrs

    def _validate_room(self, package, index, entry):
        """Per-room availability + pax limits. Errors are keyed by room index so
        the frontend can point at the offending cabin."""
        room = entry["room"]
        kid_details = entry.get("kid_details") or []

        package_room = PackageRoom.objects.filter(package=package, room=room).first()
        if package_room is None:
            raise serializers.ValidationError(
                {"rooms": {index: {"room_id": "This room is not part of the "
                                              "selected package."}}}
            )
        if not package_room.is_available:
            raise RoomUnavailable()
        if (
            BookingRoom.objects.filter(package=package, room=room, is_active=True)
            .exists()
        ):
            raise RoomUnavailable()

        room_type = room.room_type
        errors = {}
        if entry["adult_count"] > room_type.max_adults:
            errors["adult_count"] = (
                f"{room_type.name} allows at most {room_type.max_adults} adults."
            )
        if len(kid_details) > room_type.max_kids:
            errors["kid_details"] = (
                f"{room_type.name} allows at most {room_type.max_kids} kids."
            )
        if errors:
            raise serializers.ValidationError({"rooms": {index: errors}})

    def get_breakdown(self):
        """Priced breakdown for the whole booking (all Decimal), grand total
        included."""
        attrs = self.validated_data
        rooms = [
            {
                "room": entry["room"],
                "adult_count": entry["adult_count"],
                "kid_ages": [kid["age"] for kid in entry.get("kid_details") or []],
            }
            for entry in attrs["rooms"]
        ]
        return booking_price_breakdown(attrs["package"], rooms)


class BookingCreateSerializer(BookingQuoteSerializer):
    customer_name = serializers.CharField(max_length=100)
    phone = serializers.CharField(max_length=20)
    email = serializers.EmailField()
    # Optional free-text note. Bounded here (not just on the model's TextField)
    # because this endpoint is anonymous — an uncapped field would let anyone
    # push arbitrarily large rows into the DB. allow_blank so an empty box is
    # simply "no request", not a validation error.
    special_requests = serializers.CharField(
        max_length=1000, required=False, allow_blank=True, default=""
    )

    def create(self, validated_data):
        rooms_data = validated_data.pop("rooms")
        package = validated_data["package"]
        booking = Booking(**validated_data)
        # Two insert attempts: a booking_code collision (two concurrent
        # requests drawing the same random code, ~2^-32) must be retried
        # with a fresh code — not misreported as a lost room race.
        for retry_left in (True, False):
            try:
                with transaction.atomic():
                    booking.full_clean()
                    booking.save()
                    # Each room validates its own pax/availability and prices
                    # itself in clean(); the partial unique constraint on
                    # BookingRoom is the final double-booking guard for true
                    # races. The whole set is created inside one transaction, so
                    # if any room is lost to a race the entire booking rolls
                    # back — a family never ends up half-booked.
                    for entry in rooms_data:
                        booking_room = BookingRoom(
                            booking=booking,
                            package=package,
                            room=entry["room"],
                            adult_count=entry["adult_count"],
                            kid_details=[dict(k) for k in entry.get("kid_details", [])],
                        )
                        booking_room.full_clean()
                        booking_room.save()
                    # Now that every room is priced, sum them onto the booking.
                    booking.reprice()
                    booking.save(update_fields=[
                        "total_amount", "price_snapshot", "due_amount", "updated_at"
                    ])
                return booking
            except IntegrityError as exc:
                if "booking_code" in str(exc) and retry_left:
                    booking.booking_code = ""  # regenerated on the next save
                    continue
                raise RoomUnavailable()
            except DjangoValidationError as exc:
                if "uniq_active_bookingroom_per_package_room" in str(exc):
                    raise RoomUnavailable()
                raise serializers.ValidationError(
                    getattr(exc, "message_dict", None) or exc.messages
                )


class PaymentInitiateSerializer(serializers.Serializer):
    """Validates a pay request against the booking's server-side due amount.

    full → amount is taken from booking.due_amount, any client amount ignored.
    partial → client amount required, min_first_payment <= amount <= due
    (the floor comes from Package.min_deposit_percent and applies only to the
    booking's FIRST payment; top-ups have no floor).

    These checks are check-then-act UX; initiate_payment() re-verifies all of
    them under a row lock on the booking.
    """

    payment_type = serializers.ChoiceField(choices=Payment.PaymentType.choices)
    amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, min_value=Decimal("0.01")
    )

    def validate(self, attrs):
        from django.utils import timezone

        from .payment_service import minimum_first_payment

        booking = self.context["booking"]

        if booking.status in (Booking.Status.CANCELLED, Booking.Status.COMPLETED):
            raise serializers.ValidationError(
                {"payment_type": "This booking can no longer be paid."}
            )
        if booking.due_amount <= 0:
            raise serializers.ValidationError(
                {"payment_type": "Nothing is due on this booking."}
            )
        # Balance may be paid any time before departure (client policy, QA H6);
        # online payment only stops once the ship has sailed, after which the
        # guide collects any balance on board.
        if timezone.localdate() > booking.package.start_date:
            raise serializers.ValidationError(
                {
                    "payment_type": (
                        "This package has already departed — please settle any "
                        "balance with the guide on board."
                    )
                }
            )

        if attrs["payment_type"] == Payment.PaymentType.PARTIAL:
            amount = attrs.get("amount")
            if amount is None:
                raise serializers.ValidationError(
                    {"amount": "Amount is required for a partial payment."}
                )
            if amount > booking.due_amount:
                raise serializers.ValidationError(
                    {"amount": f"Amount exceeds the due amount ({booking.due_amount})."}
                )
            floor = minimum_first_payment(booking)
            if amount < floor:
                raise serializers.ValidationError(
                    {
                        "amount": (
                            f"Minimum first payment is {floor} BDT "
                            f"({booking.package.min_deposit_percent}% of the total)."
                        )
                    }
                )
        else:
            attrs.pop("amount", None)  # full payment: server decides the amount
        return attrs


class BookingInvoiceSerializer(serializers.ModelSerializer):
    """A customer-facing invoice listing. Exposes the download link (bearing
    the invoice's own capability token) and the figures the invoice states —
    never another booking's data."""

    download_url = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = [
            "number", "total_amount", "paid_amount", "due_amount",
            "sent_at", "created_at", "download_url",
        ]

    def get_download_url(self, invoice):
        url = reverse("invoice-download", kwargs={"token": invoice.access_token})
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url


class BookingPackageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Package
        fields = ["id", "start_date", "end_date"]


class BookingRoomPublicSerializer(serializers.ModelSerializer):
    """One room of a booking in the confirmation/status view."""

    room_number = serializers.CharField(source="room.room_number", read_only=True)
    room_type = serializers.CharField(source="room.room_type.name", read_only=True)

    class Meta:
        model = BookingRoom
        fields = [
            "room_number", "room_type", "adult_count", "kid_details",
            "room_subtotal",
        ]


class BookingPublicSerializer(serializers.ModelSerializer):
    """Confirmation/status representation. Looked up by unguessable
    booking_code only — never exposes other customers' data."""

    package = BookingPackageSerializer(read_only=True)
    rooms = BookingRoomPublicSerializer(many=True, read_only=True)
    total_pax = serializers.IntegerField(read_only=True)
    # The frontend renders the deposit floor from this — it never computes
    # money client-side.
    min_first_payment = serializers.SerializerMethodField()
    # The balance deadline, as a DATE the customer can see — not a policy
    # phrase. It is enforced server-side at payment time (QA H8); showing it
    # is what stops them discovering it by being refused.
    balance_due_at = serializers.SerializerMethodField()
    balance_deadline_passed = serializers.SerializerMethodField()

    class Meta:
        model = Booking
        fields = [
            "booking_code",
            "status",
            "package",
            "rooms",
            "total_pax",
            "customer_name",
            "phone",
            "email",
            "special_requests",
            "total_amount",
            "paid_amount",
            "due_amount",
            "min_first_payment",
            "balance_due_at",
            "balance_deadline_passed",
        ]

    def get_min_first_payment(self, booking):
        from .payment_service import minimum_first_payment

        return str(minimum_first_payment(booking))

    def get_balance_due_at(self, booking):
        return booking.package.balance_due_at()

    def get_balance_deadline_passed(self, booking):
        from .payment_service import balance_deadline_passed

        return balance_deadline_passed(booking)
