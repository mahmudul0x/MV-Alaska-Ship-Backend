from django.contrib import admin

from .models import FoodMenuItem, Room, RoomType, Ship


class RoomInline(admin.TabularInline):
    model = Room
    extra = 0


@admin.register(Ship)
class ShipAdmin(admin.ModelAdmin):
    list_display = ("name", "status", "total_rooms")
    list_filter = ("status",)
    inlines = [RoomInline]


@admin.register(RoomType)
class RoomTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "max_adults", "max_kids", "base_price")


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("room_number", "floor_number", "ship", "room_type")
    list_filter = ("ship", "room_type", "floor_number")
    search_fields = ("room_number",)


@admin.register(FoodMenuItem)
class FoodMenuItemAdmin(admin.ModelAdmin):
    list_display = ("name", "ship", "day", "meal_type", "order", "is_active")
    list_filter = ("ship", "day", "meal_type", "is_active")
    list_editable = ("order", "is_active")
    search_fields = ("name",)
