"""Shared test utilities."""

from rest_framework.throttling import SimpleRateThrottle


class ThrottlelessTestMixin:
    """Disable DRF throttling for API tests.

    override_settings(REST_FRAMEWORK=...) is not enough: SimpleRateThrottle
    bakes THROTTLE_RATES in as a class attribute at import time, so the real
    rates (e.g. booking 10/min) would still apply and tests would hit 429s.
    A None rate makes every throttle allow the request.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._saved_throttle_rates = SimpleRateThrottle.THROTTLE_RATES
        SimpleRateThrottle.THROTTLE_RATES = {"anon": None, "booking": None}

    @classmethod
    def tearDownClass(cls):
        SimpleRateThrottle.THROTTLE_RATES = cls._saved_throttle_rates
        super().tearDownClass()
