from rest_framework import serializers

from apps.ships.serializers import RoomImageSerializer, RoomTypeSerializer

from .models import KidPricingRule, Package, PackageRoom


class ShipMiniSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(read_only=True)


class KidPricingRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = KidPricingRule
        fields = ["min_age", "max_age", "charge_type", "amount"]


class PackageListSerializer(serializers.ModelSerializer):
    """Public package representation.

    Internal state (raw status, is_booking_open) is deliberately not exposed;
    the outside world only sees the combined is_bookable / booking_status.
    """

    ship = ShipMiniSerializer(read_only=True)
    nights = serializers.SerializerMethodField()
    days = serializers.SerializerMethodField()
    is_bookable = serializers.SerializerMethodField()
    booking_status = serializers.SerializerMethodField()

    hero_image = serializers.ImageField(read_only=True, use_url=True)

    class Meta:
        model = Package
        fields = [
            "id",
            "ship",
            "start_date",
            "end_date",
            "nights",
            "days",
            "adult_price",
            "booking_cutoff_datetime",
            "is_bookable",
            "booking_status",
            "marketing_title",
            "marketing_description",
            "hero_image",
            "highlights",
        ]

    def get_nights(self, package):
        return package.effective_nights()

    def get_days(self, package):
        return package.effective_days()

    def get_is_bookable(self, package):
        return package.is_bookable()

    def get_booking_status(self, package):
        return "open" if package.is_bookable() else "closed"


class PackageDetailSerializer(PackageListSerializer):
    kid_pricing_rules = serializers.SerializerMethodField()

    class Meta(PackageListSerializer.Meta):
        fields = PackageListSerializer.Meta.fields + ["kid_pricing_rules"]

    def get_kid_pricing_rules(self, package):
        return KidPricingRuleSerializer(KidPricingRule.objects.all(), many=True).data


class PackageRoomSerializer(serializers.ModelSerializer):
    """One room within a package, flattened, with its availability status.

    Expects a queryset annotated with `is_booked` (Exists subquery) — see
    PackageViewSet.rooms. Never exposes any booking/customer data.
    """

    id = serializers.IntegerField(source="room.id", read_only=True)
    room_number = serializers.CharField(source="room.room_number", read_only=True)
    floor_number = serializers.IntegerField(source="room.floor_number", read_only=True)
    room_type = RoomTypeSerializer(source="room.room_type", read_only=True)
    images = RoomImageSerializer(source="room.images", many=True, read_only=True)
    availability = serializers.SerializerMethodField()

    class Meta:
        model = PackageRoom
        fields = [
            "id",
            "room_number",
            "floor_number",
            "room_type",
            "images",
            "availability",
        ]

    def get_availability(self, package_room):
        # An admin hold is surfaced to the public as "booked" — the room is
        # simply not on sale, and "booked" reads more naturally to a customer
        # than "unavailable" (the internal block state/reason still never leaks;
        # only the label is shared). A room genuinely dropped from inventory
        # (is_available=False) stays "unavailable".
        if package_room.is_booked or package_room.is_blocked:
            return "booked"
        if not package_room.is_available:
            return "unavailable"
        return "available"
