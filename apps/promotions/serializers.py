from rest_framework import serializers

from .models import Promotion


class PromotionSerializer(serializers.ModelSerializer):
    """What the website reads.

    Write-side concerns (which ship, the schedule, the manual switch) are
    deliberately absent: the endpoint only ever returns promotions that are
    already live, so publishing dates are an implementation detail the public
    payload has no reason to carry.

    `cta_url` is the *resolved* destination, so the frontend never has to know
    that a linked package beats a typed URL.
    """

    image_url = serializers.ImageField(source="image", read_only=True, use_url=True)
    cta_url = serializers.CharField(source="resolved_cta_url", read_only=True)

    class Meta:
        model = Promotion
        fields = [
            "id",
            "badge_label",
            "title",
            "subtitle",
            "body",
            "image_url",
            "cta_label",
            "cta_url",
            "show_in_modal",
            "show_in_top_bar",
            "show_in_home_section",
            "modal_frequency",
            "modal_delay_seconds",
            # The browser keys "already dismissed" on id + updated_at, so an
            # edited promotion is shown again to someone who dismissed the
            # previous wording. Without this field a correction would never
            # reach the people who had already closed it once.
            "updated_at",
        ]
