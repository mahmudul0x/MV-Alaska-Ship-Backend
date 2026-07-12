"""Shared test utilities."""

import hashlib

from django.conf import settings
from rest_framework.throttling import SimpleRateThrottle


def sign_ipn(payload):
    """Return the payload with a genuine verify_sign/verify_key pair attached
    (SSLCommerz's documented MD5 scheme, computed with the configured store
    password) so PaymentIPNView's signature check passes.

    Tests that exercise FORGED notifications simply post without calling this
    — those must be rejected with 400 and change nothing.
    """
    data = {key: str(value) for key, value in payload.items()}
    keys = sorted(data)
    data["verify_key"] = ",".join(keys)
    pairs = dict(data)
    pairs.pop("verify_key")
    pairs["store_passwd"] = hashlib.md5(
        settings.SSLCOMMERZ_STORE_PASSWORD.encode()
    ).hexdigest()
    signable = "&".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    data["verify_sign"] = hashlib.md5(signable.encode()).hexdigest()
    return data


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
        SimpleRateThrottle.THROTTLE_RATES = {
            "anon": None,
            "booking": None,
            "quote": None,
        }

    @classmethod
    def tearDownClass(cls):
        SimpleRateThrottle.THROTTLE_RATES = cls._saved_throttle_rates
        super().tearDownClass()
