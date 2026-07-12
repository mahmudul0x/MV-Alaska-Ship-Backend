from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from .models import RoomType, Ship
from .serializers import (
    FoodMenuSerializer,
    RoomTypeSerializer,
    ShipLayoutSerializer,
    ShipSerializer,
)


class ShipViewSet(viewsets.ReadOnlyModelViewSet):
    # Tiny bounded catalog the frontend reads as a bare array — opt out of the
    # project-wide default paginator (QA phase8b F3). Read-only browsing, so the
    # generous `read` throttle bucket, not the shared anon one (QA phase8b F1).
    pagination_class = None
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "read"
    queryset = Ship.objects.filter(status=Ship.Status.ACTIVE)
    serializer_class = ShipSerializer

    @action(detail=True)
    def layout(self, request, pk=None):
        ship = self.get_object()
        serializer = ShipLayoutSerializer(ship, context={"request": request})
        return Response(serializer.data)

    @action(detail=True, url_path="food-menu")
    def food_menu(self, request, pk=None):
        ship = self.get_object()
        serializer = FoodMenuSerializer(ship, context={"request": request})
        return Response(serializer.data)


class RoomTypeViewSet(viewsets.ReadOnlyModelViewSet):
    """Public catalog of room types (2/3/4-Person Room) for marketing pages
    like /cabins — availability is always package-specific, so this is
    static catalog data only (base_price, pax limits), never bookings."""

    # Static catalog (2/3/4-person), read as a bare array by the frontend —
    # opt out of the project-wide default paginator (QA phase8b F3), and use the
    # generous read-only throttle bucket (QA phase8b F1).
    pagination_class = None
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "read"
    queryset = RoomType.objects.all().order_by("max_adults")
    serializer_class = RoomTypeSerializer
