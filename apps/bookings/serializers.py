from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from rest_framework import serializers

from apps.packages.models import Package, PackageRoom
from apps.ships.models import Room

from .exceptions import RoomUnavailable
from .models import Booking, Payment
from .pricing import price_breakdown


def breakdown_as_json(breakdown):
    """Decimal → string ("11000.00") so DRF's encoder never floats money."""
    return {
        "room_base": str(breakdown["room_base"]),
        "adult_price": str(breakdown["adult_price"]),
        "adult_count": breakdown["adult_count"],
        "adults_subtotal": str(breakdown["adults_subtotal"]),
        "kids": [
            {"age": kid["age"], "charge": str(kid["charge"])}
            for kid in breakdown["kids"]
        ],
        "kids_subtotal": str(breakdown["kids_subtotal"]),
        "total": str(breakdown["total"]),
    }


class KidSerializer(serializers.Serializer):
    age = serializers.IntegerField(min_value=0, max_value=17)


class BookingQuoteSerializer(serializers.Serializer):
    """Validates a prospective booking and prices it — no DB writes.

    Also the base of BookingCreateSerializer, so quote and create can never
    disagree on validation or price.
    """

    # Staff subclasses flip this off: admins may book past the cutoff
    # (PRD §5.5 manual override); the public API always enforces it.
    enforce_cutoff = True

    package_id = serializers.PrimaryKeyRelatedField(
        queryset=Package.objects.public(), source="package"
    )
    room_id = serializers.PrimaryKeyRelatedField(
        queryset=Room.objects.all(), source="room"
    )
    adult_count = serializers.IntegerField(min_value=1)
    kid_details = KidSerializer(many=True, required=False)

    def validate(self, attrs):
        package = attrs["package"]
        room = attrs["room"]
        kid_details = attrs.get("kid_details") or []

        if self.enforce_cutoff and not package.is_bookable():
            raise serializers.ValidationError(
                {"package_id": "Booking is closed for this package."}
            )

        package_room = PackageRoom.objects.filter(package=package, room=room).first()
        if package_room is None:
            raise serializers.ValidationError(
                {"room_id": "This room is not part of the selected package."}
            )
        if not package_room.is_available:
            raise RoomUnavailable()
        if (
            Booking.objects.filter(package=package, room=room)
            .exclude(status=Booking.Status.CANCELLED)
            .exists()
        ):
            raise RoomUnavailable()

        room_type = room.room_type
        errors = {}
        if attrs["adult_count"] > room_type.max_adults:
            errors["adult_count"] = (
                f"{room_type.name} allows at most {room_type.max_adults} adults."
            )
        if len(kid_details) > room_type.max_kids:
            errors["kid_details"] = (
                f"{room_type.name} allows at most {room_type.max_kids} kids."
            )
        if errors:
            raise serializers.ValidationError(errors)

        return attrs

    def get_breakdown(self):
        """Priced breakdown for validated data (all Decimal)."""
        attrs = self.validated_data
        kid_ages = [kid["age"] for kid in attrs.get("kid_details") or []]
        return price_breakdown(
            attrs["room"].room_type, attrs["package"], attrs["adult_count"], kid_ages
        )


class BookingCreateSerializer(BookingQuoteSerializer):
    customer_name = serializers.CharField(max_length=100)
    phone = serializers.CharField(max_length=20)
    email = serializers.EmailField()

    def create(self, validated_data):
        kid_details = [dict(kid) for kid in validated_data.pop("kid_details", [])]
        booking = Booking(kid_details=kid_details, **validated_data)
        # Two insert attempts: a booking_code collision (two concurrent
        # requests drawing the same random code, ~2^-32) must be retried
        # with a fresh code — not misreported as a lost room race.
        for retry_left in (True, False):
            try:
                with transaction.atomic():
                    # clean() re-validates pax/availability and computes the
                    # total server-side; the partial unique constraint is the
                    # final double-booking guard for true races.
                    booking.full_clean()
                    booking.save()
                return booking
            except IntegrityError as exc:
                if "booking_code" in str(exc) and retry_left:
                    booking.booking_code = ""  # regenerated on the next save
                    continue
                raise RoomUnavailable()
            except DjangoValidationError as exc:
                if "uniq_active_booking_per_package_room" in str(exc):
                    raise RoomUnavailable()
                raise serializers.ValidationError(
                    getattr(exc, "message_dict", None) or exc.messages
                )


class PaymentInitiateSerializer(serializers.Serializer):
    """Validates a pay request against the booking's server-side due amount.

    full → amount is taken from booking.due_amount, any client amount ignored.
    partial → client amount required, 0 < amount <= due.
    """

    payment_type = serializers.ChoiceField(choices=Payment.PaymentType.choices)
    amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, min_value=Decimal("0.01")
    )

    def validate(self, attrs):
        booking = self.context["booking"]

        if booking.status in (Booking.Status.CANCELLED, Booking.Status.COMPLETED):
            raise serializers.ValidationError(
                {"payment_type": "This booking can no longer be paid."}
            )
        if booking.due_amount <= 0:
            raise serializers.ValidationError(
                {"payment_type": "Nothing is due on this booking."}
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
        else:
            attrs.pop("amount", None)  # full payment: server decides the amount
        return attrs


class BookingPackageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Package
        fields = ["id", "start_date", "end_date"]


class BookingPublicSerializer(serializers.ModelSerializer):
    """Confirmation/status representation. Looked up by unguessable
    booking_code only — never exposes other customers' data."""

    package = BookingPackageSerializer(read_only=True)
    room_number = serializers.CharField(source="room.room_number", read_only=True)

    class Meta:
        model = Booking
        fields = [
            "booking_code",
            "status",
            "package",
            "room_number",
            "customer_name",
            "phone",
            "email",
            "adult_count",
            "kid_details",
            "total_amount",
            "paid_amount",
            "due_amount",
        ]
