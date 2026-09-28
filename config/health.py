"""
Liveness/readiness endpoint for the container health check and the external
uptime monitor.

Deliberately a plain Django view, NOT a DRF view: DRF applies
DEFAULT_THROTTLE_CLASSES to everything it serves, and a throttled health check
returns 429 -> the orchestrator marks a healthy container unhealthy and restarts
it in a loop. A plain view cannot be throttled, and needs no throttle scope
registered in settings or in the test mixin.

It touches the database on purpose. A check that only proves "Python is running"
stays green while every request 500s on a dead connection, which is the exact
outage you most need to hear about.

The response body is intentionally tiny. A health endpoint is public, so it
reveals no version, hostname, dependency list or exception text.
"""

import logging

from django.db import connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache

logger = logging.getLogger(__name__)


@never_cache
def health(request):
    """Return 200 when the app can reach its database, 503 when it cannot."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        # Logged with the traceback (so Sentry and the host log see it), but the
        # detail never reaches the response.
        logger.exception("Health check failed: database unreachable")
        return JsonResponse({"status": "error"}, status=503)

    return JsonResponse({"status": "ok"})
