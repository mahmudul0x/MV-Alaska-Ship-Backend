"""Staff dashboard serializers — full-field, staff-only views of every model.

Unlike the public serializers these expose internal state (raw status,
is_booking_open, customer data across bookings) because every staff endpoint
sits behind IsAdminUser.
"""

from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from apps.bookings.models import Booking, BookingStatusLog, Invoice, Payment
from apps.bookings.serializers import BookingCreateSerializer
from apps.packages.models import KidPricingRule, Package, PackageRoom
from apps.ships.models import FoodMenuItem, Room, RoomType


class StaffTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Login is for dashboard staff only — valid credentials without
    is_staff still get rejected."""

    def validate(self, attrs):
        data = super().validate(attrs)
        if not self.user.is_staff:
            raise serializers.ValidationError(
                {"detail": "This account does not have staff access."}
            )
        data["user"] = {
            "username": self.user.username,
            "first_name": self.user.first_name,
            "is_staff": self.user.is_staff,
        }
        return data


class StaffRoomTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = RoomType
        fields = ["id", "name", "max_adults", "max_kids", "base_price"]


class StaffRoomSerializer(serializers.ModelSerializer):
    room_type_name = serializers.CharField(source="room_type.name", read_only=True)

    class Meta:
        model = Room
        fields = ["id", "ship", "room_type", "room_type_name", "room_number", "floor_number"]


class StaffFoodMenuItemSerializer(serializers.ModelSerializer):
    ship_name = serializers.CharField(source="ship.name", read_only=True)

    class Meta:
        model = FoodMenuItem
        fields = [
            "id", "ship", "ship_name", "day", "meal_type", "name", "order", "is_active",
        ]


class StaffPackageRoomBookingSerializer(serializers.ModelSerializer):
    """Booking summary embedded in the per-package room map — enough for the
    dashboard to show who holds a room and what they owe."""

    total_pax = serializers.IntegerField(read_only=True)

    class Meta:
        model = Booking
        fields = [
            "id", "booking_code", "customer_name", "phone",
            "adult_count", "kid_details", "total_pax",
            "total_amount", "paid_amount", "due_amount", "status",
        ]


class StaffPackageRoomSerializer(serializers.ModelSerializer):
    """One room within a package with its live status and (if booked) the
    active booking. Expects `bookings_by_room` {room_id: Booking} in context."""

    room_id = serializers.IntegerField(source="room.id", read_only=True)
    room_number = serializers.CharField(source="room.room_number", read_only=True)
    floor_number = serializers.IntegerField(source="room.floor_number", read_only=True)
    room_type = StaffRoomTypeSerializer(source="room.room_type", read_only=True)
    availability = serializers.SerializerMethodField()
    booking = serializers.SerializerMethodField()

    class Meta:
        model = PackageRoom
        fields = [
            "id", "room_id", "room_number", "floor_number", "room_type",
            "is_available", "availability", "booking",
        ]

    def _active_booking(self, package_room):
        return self.context.get("bookings_by_room", {}).get(package_room.room_id)

    def get_availability(self, package_room):
        if not package_room.is_available:
            return "unavailable"
        if self._active_booking(package_room):
            return "booked"
        return "available"

    def get_booking(self, package_room):
        booking = self._active_booking(package_room)
        if booking is None:
            return None
        return StaffPackageRoomBookingSerializer(booking).data


class StaffKidPricingRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = KidPricingRule
        fields = ["id", "min_age", "max_age", "charge_type", "amount"]

    def validate(self, attrs):
        # Surface the model's overlap/amount validation as field errors,
        # merging with the existing instance so partial updates validate too.
        rule = KidPricingRule(
            min_age=attrs.get("min_age", getattr(self.instance, "min_age", None)),
            max_age=attrs.get("max_age", getattr(self.instance, "max_age", None)),
            charge_type=attrs.get("charge_type", getattr(self.instance, "charge_type", None)),
            amount=attrs.get("amount", getattr(self.instance, "amount", None)),
        )
        if self.instance:
            rule.pk = self.instance.pk
        rule.clean()
        return attrs


class StaffPackageSerializer(serializers.ModelSerializer):
    ship_name = serializers.CharField(source="ship.name", read_only=True)
    # Annotated by the viewset queryset:
    bookings_count = serializers.IntegerField(read_only=True)
    paid_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    due_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    rooms_total = serializers.IntegerField(read_only=True)
    is_bookable = serializers.SerializerMethodField()

    class Meta:
        model = Package
        fields = [
            "id", "ship", "ship_name", "start_date", "end_date",
            "booking_cutoff_datetime", "adult_price", "status", "is_booking_open",
            "marketing_title", "marketing_description", "hero_image", "highlights",
            "bookings_count", "paid_total", "due_total", "rooms_total", "is_bookable",
        ]

    def get_is_bookable(self, package):
        return package.is_bookable()

    def validate(self, attrs):
        # DRF never calls model clean(), so without this an inverted date
        # range or a ship-date overlap would hit the DB constraints and 500.
        # Merge with the existing instance so partial updates validate too
        # (same pattern as StaffKidPricingRuleSerializer).
        def value(field):
            return attrs.get(field, getattr(self.instance, field, None))

        package = Package(
            ship=value("ship"),
            start_date=value("start_date"),
            end_date=value("end_date"),
            status=value("status") or Package.Status.DRAFT,
        )
        if self.instance:
            package.pk = self.instance.pk
        package.clean()
        return attrs


class StaffPaymentSerializer(serializers.ModelSerializer):
    booking_code = serializers.CharField(source="booking.booking_code", read_only=True)

    class Meta:
        model = Payment
        fields = [
            "id", "booking", "booking_code", "amount", "payment_type", "gateway",
            "transaction_id", "status", "paid_at", "created_at",
        ]
        read_only_fields = ["transaction_id", "created_at"]


class StaffStatusLogSerializer(serializers.ModelSerializer):
    changed_by_username = serializers.CharField(
        source="changed_by.username", read_only=True, default=None
    )

    class Meta:
        model = BookingStatusLog
        fields = ["old_status", "new_status", "changed_by_username", "created_at"]


class StaffBookingListSerializer(serializers.ModelSerializer):
    package_title = serializers.SerializerMethodField()
    room_number = serializers.CharField(source="room.room_number", read_only=True)
    total_pax = serializers.IntegerField(read_only=True)

    class Meta:
        model = Booking
        fields = [
            "id", "booking_code", "customer_name", "phone", "email",
            "package", "package_title", "room", "room_number",
            "adult_count", "kid_details", "total_pax",
            "total_amount", "paid_amount", "due_amount", "status", "created_at",
        ]

    def get_package_title(self, booking):
        return booking.package.marketing_title or str(booking.package)


class StaffBookingDetailSerializer(StaffBookingListSerializer):
    payments = StaffPaymentSerializer(many=True, read_only=True)
    status_logs = StaffStatusLogSerializer(many=True, read_only=True)

    class Meta(StaffBookingListSerializer.Meta):
        fields = StaffBookingListSerializer.Meta.fields + ["payments", "status_logs"]


class StaffBookingUpdateSerializer(serializers.ModelSerializer):
    """Staff may only touch status and contact info; pax/room changes go
    through cancel-and-rebook so pricing and availability stay consistent."""

    class Meta:
        model = Booking
        fields = ["status", "customer_name", "phone", "email"]

    def update(self, instance, validated_data):
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save(changed_by=self.context["request"].user)
        return instance


class StaffBookingCreateSerializer(BookingCreateSerializer):
    """Manual booking from the dashboard — same validation as the public
    API except the cutoff check (staff may book past cutoff, PRD §5.5),
    and any package is selectable (not just publicly visible ones)."""

    enforce_cutoff = False
    package_id = serializers.PrimaryKeyRelatedField(
        queryset=Package.objects.all(), source="package"
    )


class StaffInvoiceSerializer(serializers.ModelSerializer):
    booking_code = serializers.CharField(source="booking.booking_code", read_only=True)
    pdf_url = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = ["id", "booking", "booking_code", "sent_via", "sent_at", "pdf_url", "created_at"]

    def get_pdf_url(self, invoice):
        if invoice.pdf_file:
            request = self.context.get("request")
            url = invoice.pdf_file.url
            return request.build_absolute_uri(url) if request else url
        return None
