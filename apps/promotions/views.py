from rest_framework import viewsets
from rest_framework.throttling import ScopedRateThrottle

from .models import Promotion
from .serializers import PromotionSerializer


class PromotionViewSet(viewsets.ReadOnlyModelViewSet):
    """Live promotions for the public site.

    Read on essentially every home-page visit, so it shares the generous
    "read" throttle bucket with the other browsing endpoints rather than the
    100/min anon budget — a family browsing from one carrier IP must not trip
    a rate limit and lose the banner.

    Unpaginated: at most a handful of rows, read as a bare array.
    """

    pagination_class = None
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "read"
    serializer_class = PromotionSerializer

    def get_queryset(self):
        """Only what is live *right now*.

        The date window is applied in Python via `is_live()` rather than as a
        queryset filter, so there is exactly one definition of "live" — the
        one the staff dashboard and the tests also call. The set is tiny, so
        the cost of evaluating it in Python is irrelevant next to the cost of
        the two definitions drifting apart.
        """
        rows = (
            Promotion.objects.filter(is_active=True)
            .select_related("ship", "linked_package")
            .order_by("sort_order", "-created_at")
        )
        return [row for row in rows if row.is_live()]
