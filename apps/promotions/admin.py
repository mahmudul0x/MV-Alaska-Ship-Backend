from django.contrib import admin

from .models import Promotion


@admin.register(Promotion)
class PromotionAdmin(admin.ModelAdmin):
    list_display = ("title", "ship", "is_active", "starts_at", "ends_at", "sort_order")
    list_filter = ("ship", "is_active", "show_in_modal", "show_in_hero")
    search_fields = ("title", "subtitle", "badge_label")
