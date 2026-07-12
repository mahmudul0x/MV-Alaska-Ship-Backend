from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
from rest_framework import serializers

from apps.packages.models import Package, PackageRoom
from apps.ships.models import Room

from .exceptions import RoomUnavailable
from .models import Booking, Invoice, Payment
from .pricing import price_breakdown, snapshot_breakdown

#: Decimal → string ("11000.00") so DRF's encoder never floats money. Same
#: shape the booking stores in price_snapshot, so the API and the invoice can
#: never disagree about what a breakdown looks like.
breakdown_as_json = snapshot_breakdown


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


class BookingPublicSerializer(serializers.ModelSerializer):
    """Confirmation/status representation. Looked up by unguessable
    booking_code only — never exposes other customers' data."""

    package = BookingPackageSerializer(read_only=True)
    room_number = serializers.CharField(source="room.room_number", read_only=True)
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
            "room_number",
            "customer_name",
            "phone",
            "email",
            "adult_count",
            "kid_details",
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
