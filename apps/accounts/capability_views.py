from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .capabilities import CAPABILITIES, DEFAULT_CAPABILITIES


class CapabilityCatalogueView(APIView):
    """The list of things an account can be given permission to do.

    Served rather than duplicated in the frontend: the checkboxes an
    administrator ticks are then generated from the same catalogue the
    endpoints enforce. A hardcoded second copy drifts, and the way that drift
    shows is a box that grants nothing, or a screen that appears and then 403s.

    Readable by any signed-in staff member — it is a list of feature names,
    not a list of who holds them.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            {
                "capabilities": [
                    {
                        "key": c.key,
                        "label": c.label,
                        "description": c.description,
                        "group": c.group,
                        "sensitive": c.sensitive,
                    }
                    for c in CAPABILITIES
                ],
                "defaults": list(DEFAULT_CAPABILITIES),
            }
        )
