from rest_framework import viewsets
from apps.accounts.permissions import HasCapability

from .models import Promotion
from .staff_serializers import StaffPromotionSerializer


class StaffPromotionViewSet(viewsets.ModelViewSet):
    """Promotions page in the dashboard: write the announcement, upload the
    artwork, choose where it appears and when it runs.

    Unpaginated — a site runs a handful of promotions, and the page wants the
    expired ones visible too so last Eid's banner can be copied rather than
    rewritten.
    """

    permission_classes = [HasCapability.of("promotions")]
    pagination_class = None
    serializer_class = StaffPromotionSerializer
    queryset = Promotion.objects.select_related("ship", "linked_package").order_by(
        "sort_order", "-created_at"
    )
