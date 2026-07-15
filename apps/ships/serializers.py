from rest_framework import serializers

from .models import FoodMenuItem, Room, RoomImage, RoomType, Ship


class RoomTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = RoomType
        fields = ["id", "name", "max_adults", "max_kids", "base_price"]


class RoomImageSerializer(serializers.ModelSerializer):
    image = serializers.ImageField(read_only=True, use_url=True)

    class Meta:
        model = RoomImage
        fields = ["id", "image", "caption", "sort_order"]


class RoomSerializer(serializers.ModelSerializer):
    room_type = RoomTypeSerializer(read_only=True)
    images = RoomImageSerializer(many=True, read_only=True)

    class Meta:
        model = Room
        fields = ["id", "room_number", "floor_number", "room_type", "images"]


class ShipSerializer(serializers.ModelSerializer):
    layout_image = serializers.ImageField(read_only=True, use_url=True)
    total_rooms = serializers.IntegerField(read_only=True)

    class Meta:
        model = Ship
        fields = ["id", "name", "layout_image", "total_rooms"]


class ShipLayoutSerializer(ShipSerializer):
    """Ship + rooms grouped by floor. Static structure only — per-package
    availability comes from /api/packages/{id}/rooms/."""

    floors = serializers.SerializerMethodField()

    class Meta(ShipSerializer.Meta):
        fields = ShipSerializer.Meta.fields + ["floors"]

    def get_floors(self, ship):
        rooms = (
            ship.rooms.select_related("room_type")
            .prefetch_related("images")
            .order_by("floor_number", "room_number")
        )
        floors = {}
        for room in rooms:
            floors.setdefault(room.floor_number, []).append(
                RoomSerializer(room).data
            )
        return [
            {"floor_number": floor, "rooms": floor_rooms}
            for floor, floor_rooms in sorted(
                floors.items(), key=lambda item: (item[0] is None, item[0] or 0)
            )
        ]


class FoodMenuSerializer(ShipSerializer):
    """Ship's food menu grouped by day, then by meal type. Chef selects the
    day's actual dishes from these active items — this is a selection pool,
    not a fixed daily plan."""

    note = serializers.SerializerMethodField()
    days = serializers.SerializerMethodField()

    class Meta(ShipSerializer.Meta):
        fields = ShipSerializer.Meta.fields + ["note", "days"]

    def get_note(self, ship):
        return "Chef will select the day's menu from the above items."

    def get_days(self, ship):
        items = ship.food_menu_items.filter(is_active=True).order_by(
            "day", "meal_type", "order", "id"
        )
        days = {}
        for item in items:
            meals = days.setdefault(item.day, {})
            meals.setdefault(item.meal_type, []).append(item.name)

        day_labels = dict(FoodMenuItem.Day.choices)
        meal_labels = dict(FoodMenuItem.MealType.choices)
        meal_order = [choice for choice, _ in FoodMenuItem.MealType.choices]

        return [
            {
                "day": day,
                "day_label": day_labels[day],
                "meals": [
                    {
                        "meal_type": meal_type,
                        "meal_type_label": meal_labels[meal_type],
                        "items": meals[meal_type],
                    }
                    for meal_type in meal_order
                    if meal_type in meals
                ],
            }
            for day, meals in sorted(days.items())
        ]
