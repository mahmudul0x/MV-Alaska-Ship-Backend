"""
Tests for the /healthz/ endpoint.

Deliberately does NOT use ThrottlelessTestMixin. The whole point of the endpoint
being a plain Django view is that DRF's throttling cannot reach it, and a test
that disables throttling first could not tell the difference. These tests run
against the real configured rates.
"""

from unittest import mock

from django.test import TestCase
from django.urls import reverse


class HealthEndpointTests(TestCase):
    def test_returns_ok_when_database_is_reachable(self):
        response = self.client.get(reverse("health"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_is_never_throttled(self):
        """A throttled health check is worse than none at all.

        DRF's DEFAULT_THROTTLE_CLASSES applies AnonRateThrottle (100/min) to
        everything it serves. If /healthz/ were ever moved onto a DRF view, the
        container health check plus an external uptime monitor would eventually
        draw a 429, the orchestrator would read that as unhealthy, and it would
        restart a container that was fine — repeatedly. Hammer it well past the
        anon budget to prove no throttle is in the path.
        """
        url = reverse("health")

        for _ in range(150):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)

    def test_reports_503_when_database_is_unreachable(self):
        """The check must fail when the DB is down, not merely prove Python runs.

        A health check that only answers "the process is alive" stays green
        through the exact outage you most need paging you.
        """
        with mock.patch("config.health.connection") as fake_connection:
            fake_connection.cursor.side_effect = Exception("connection refused")

            response = self.client.get(reverse("health"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "error"})

    def test_leaks_no_detail_on_failure(self):
        """The endpoint is public: no exception text, hostname or versions."""
        with mock.patch("config.health.connection") as fake_connection:
            fake_connection.cursor.side_effect = Exception(
                "FATAL: password authentication failed for user 'alaska_user'"
            )

            response = self.client.get(reverse("health"))

        body = response.content.decode()
        self.assertNotIn("password", body)
        self.assertNotIn("alaska_user", body)
        self.assertEqual(response.json(), {"status": "error"})

    def test_response_is_not_cacheable(self):
        """A cached health check would report a stale verdict."""
        response = self.client.get(reverse("health"))

        self.assertIn("no-cache", response.headers.get("Cache-Control", ""))
