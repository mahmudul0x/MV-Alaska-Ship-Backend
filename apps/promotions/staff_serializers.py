from rest_framework import serializers

from .models import Promotion


class StaffPromotionSerializer(serializers.ModelSerializer):
    """The dashboard's view of a promotion — every field, plus the one piece of
    derived state staff actually need: whether it is showing right now.

    Same image contract as the gallery and cabin uploads: `image` is
    upload-only (multipart), reads carry `image_url`.
    """

    image = serializers.ImageField(write_only=True, required=False, allow_null=True)
    image_url = serializers.ImageField(source="image", read_only=True, use_url=True)
    ship_name = serializers.CharField(source="ship.name", read_only=True)
    linked_package_label = serializers.SerializerMethodField()

    # Without explicit defaults a multipart POST omits unchecked boxes, DRF
    # reads the absent field as False, and a promotion saves itself switched
    # off and invisible. The gallery serializer hit exactly this.
    is_active = serializers.BooleanField(default=True)
    show_in_modal = serializers.BooleanField(default=True)
    show_in_top_bar = serializers.BooleanField(default=True)
    show_in_home_section = serializers.BooleanField(default=True)

    is_live = serializers.SerializerMethodField()

    class Meta:
        model = Promotion
        fields = [
            "id",
            "ship",
            "ship_name",
            "badge_label",
            "title",
            "subtitle",
            "body",
            "image",
            "image_url",
            "cta_label",
            "cta_url",
            "linked_package",
            "linked_package_label",
            "show_in_modal",
            "show_in_top_bar",
            "show_in_home_section",
            "modal_frequency",
            "modal_delay_seconds",
            "starts_at",
            "ends_at",
            "is_active",
            "sort_order",
            "is_live",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def get_is_live(self, obj) -> bool:
        """Whether the public site is showing this at this moment.

        Staff otherwise have to work it out from three separate fields and the
        clock, which is how a promotion ends up switched on, in date, and still
        invisible because its sailing has departed.
        """
        return obj.is_live()

    def get_linked_package_label(self, obj) -> str:
        """How the linked sailing is named back to staff.

        A bare start date identifies nothing — there are several sailings a
        month and they all read as "2026-10-17". Lead with the name staff gave
        the package, fall back to the ship when they gave it none, and carry
        the date range so two runs of the same package stay distinguishable.

        Mirrors `sailingLabel()` in the dashboard's promotions page, so the
        picker and the saved row read identically. Keep the two in step.
        """
        if not obj.linked_package_id:
            return ""
        package = obj.linked_package
        name = (package.marketing_title or "").strip() or package.ship.name

        # `.day` rather than strftime's %-d: that directive is glibc-only and
        # raises ValueError on Windows, where this project is developed.
        def short(day):
            return f"{day.day} {day:%b %Y}"

        return f"{name} — {short(package.start_date)} → {short(package.end_date)}"

    def validate(self, attrs):
        """Run the model's own rules on the way in.

        ModelSerializer does not call Model.clean(), so without this the CTA
        and placement rules would hold in the admin and not over the API —
        and the API is what the dashboard uses.
        """
        instance = Promotion(**{**self._current_values(), **attrs})
        instance.full_clean(
            exclude=[f.name for f in Promotion._meta.fields if f.name != "id"],
            validate_unique=False,
        )
        instance.clean()
        return attrs

    def _current_values(self):
        """Existing field values on update, so partial edits validate against
        the whole row rather than only the fields that were sent."""
        if self.instance is None:
            return {}
        return {
            field.name: getattr(self.instance, field.name)
            for field in Promotion._meta.fields
            if field.name != "id"
        }
