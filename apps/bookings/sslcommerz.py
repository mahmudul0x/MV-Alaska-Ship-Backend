"""SSLCommerz HTTP client — the only module that talks to the gateway.

Endpoints and credentials come from settings (which read .env); nothing is
hardcoded, and switching sandbox → live is a .env change only.
"""

import requests
from django.conf import settings


class GatewayError(Exception):
    """Session creation or validation could not be completed."""


def create_session(payment):
    """Create a gateway checkout session; returns the GatewayPageURL."""
    booking = payment.booking
    payload = {
        "store_id": settings.SSLCOMMERZ_STORE_ID,
        "store_passwd": settings.SSLCOMMERZ_STORE_PASSWORD,
        "total_amount": str(payment.amount),
        "currency": "BDT",
        "tran_id": payment.transaction_id,
        "success_url": f"{settings.BACKEND_URL}/api/payments/success/",
        "fail_url": f"{settings.BACKEND_URL}/api/payments/fail/",
        "cancel_url": f"{settings.BACKEND_URL}/api/payments/cancel/",
        "ipn_url": f"{settings.BACKEND_URL}/api/payments/ipn/",
        "cus_name": booking.customer_name,
        "cus_email": booking.email,
        "cus_phone": booking.phone,
        "cus_add1": "N/A",
        "cus_city": "N/A",
        "cus_country": "Bangladesh",
        "shipping_method": "NO",
        "num_of_item": 1,
        "product_name": f"Ship package {booking.booking_code}",
        "product_category": "Travel",
        # "general" needs no vertical-specific extra fields (travel-vertical
        # demands hotel_name etc., which don't fit a ship tour).
        "product_profile": "general",
        "value_a": booking.booking_code,
    }
    response = requests.post(settings.SSLCOMMERZ_SESSION_URL, data=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    if data.get("status") != "SUCCESS" or not data.get("GatewayPageURL"):
        raise GatewayError(data.get("failedreason") or "Gateway session failed.")
    return data["GatewayPageURL"]


def validate_payment(val_id):
    """Server-to-server validation of a payment by val_id.

    This authenticated outbound call is the ONLY source of truth about a
    payment's outcome — IPN/redirect POST data is never trusted directly.
    """
    response = requests.get(
        settings.SSLCOMMERZ_VALIDATION_URL,
        params={
            "val_id": val_id,
            "store_id": settings.SSLCOMMERZ_STORE_ID,
            "store_passwd": settings.SSLCOMMERZ_STORE_PASSWORD,
            "format": "json",
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()
