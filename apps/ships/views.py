from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import RoomType, Ship
from .serializers import (
    FoodMenuSerializer,
    RoomTypeSerializer,
    ShipLayoutSerializer,
    ShipSerializer,
)


class ShipViewSet(viewsets.ReadOnlyModelViewSet):
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

    queryset = RoomType.objects.all().order_by("max_adults")
    serializer_class = RoomTypeSerializer
