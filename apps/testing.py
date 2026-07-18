"""Shared test utilities."""

import hashlib

from django.conf import settings
from rest_framework.throttling import SimpleRateThrottle


def create_booking(package, rooms, **booking_fields):
    """Create a Booking with one or more BookingRooms at the model layer.

    `rooms` is a list of dicts {"room":…, "adult_count":…, "kid_details":…}.
    Each BookingRoom is full_clean()'d (so pax/availability/pricing validation
    fires exactly as in production), then the booking is repriced to the sum of
    its rooms. Returns the saved booking. The single place model-layer tests
    build a booking, so the multi-room construction lives in one spot.
    """
    from apps.bookings.models import Booking, BookingRoom

    defaults = {
        "customer_name": "Rahim Uddin",
        "phone": "01700000000",
        "email": "rahim@example.com",
    }
    defaults.update(booking_fields)
    booking = Booking(package=package, **defaults)
    booking.full_clean()
    booking.save()
    for entry in rooms:
        br = BookingRoom(
            booking=booking,
            package=package,
            room=entry["room"],
            adult_count=entry["adult_count"],
            kid_details=entry.get("kid_details") or [],
        )
        br.full_clean()
        br.save()
    booking.reprice()
    booking.save(update_fields=["total_amount", "price_snapshot", "due_amount"])
    return booking


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
        # Map EVERY configured scope to None, not a hardcoded subset: a
        # ScopedRateThrottle whose scope is absent from THROTTLE_RATES raises
        # KeyError (→ 500) instead of being disabled, so a new scope in settings
        # (e.g. "read"/"status") would silently break unrelated tests. Deriving
        # the keys from settings keeps this allowlist from going stale.
        configured = settings.REST_FRAMEWORK.get("DEFAULT_THROTTLE_RATES", {})
        SimpleRateThrottle.THROTTLE_RATES = {scope: None for scope in configured}

    @classmethod
    def tearDownClass(cls):
        SimpleRateThrottle.THROTTLE_RATES = cls._saved_throttle_rates
        super().tearDownClass()
