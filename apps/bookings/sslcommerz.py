"""SSLCommerz HTTP client — the only module that talks to the gateway.

Endpoints and credentials come from settings (which read .env); nothing is
hardcoded, and switching sandbox → live is a .env change only.
"""

import hashlib
import hmac

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


def query_transaction(tran_id):
    """Look up a transaction at the gateway by OUR tran_id (no val_id needed).

    Used by the reconciliation job and the fail/cancel redirects: it answers
    "did any money actually move on this session?" straight from the gateway,
    so PENDING payments can be settled or closed definitively even when the
    IPN (and its val_id) never arrived. Returns the list of attempt records
    (possibly empty — the customer may never have attempted payment).
    """
    response = requests.get(
        settings.SSLCOMMERZ_TXN_QUERY_URL,
        params={
            "tran_id": tran_id,
            "store_id": settings.SSLCOMMERZ_STORE_ID,
            "store_passwd": settings.SSLCOMMERZ_STORE_PASSWORD,
            "format": "json",
        },
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("APIConnect") != "DONE":
        raise GatewayError(data.get("APIConnect") or "Transaction query failed.")
    if data.get("no_of_trans_found") in (0, "0", None) and not data.get("element"):
        return []
    element = data.get("element") or []
    # The API returns a bare object when exactly one attempt exists.
    return [element] if isinstance(element, dict) else list(element)


def verify_ipn_signature(data):
    """Check the verify_sign/verify_key hash SSLCommerz sends with every IPN.

    Scheme (per SSLCommerz docs): verify_key names the POSTed params covered
    by the hash; those key=value pairs plus store_passwd=md5(store password)
    are sorted by key, joined with '&', and MD5-hashed to produce verify_sign.

    The IPN endpoint is unauthenticated by nature — this signature is what
    proves a notification genuinely came from SSLCommerz. Anything failing
    here must be ignored BEFORE any state change (a forged status=FAILED
    could otherwise kill a live payment session).
    """
    verify_sign = data.get("verify_sign")
    verify_key = data.get("verify_key")
    if not verify_sign or not verify_key:
        return False
    keys = [key for key in str(verify_key).split(",") if key]
    if not keys:
        return False
    pairs = {key: str(data.get(key, "")) for key in keys}
    pairs["store_passwd"] = hashlib.md5(
        settings.SSLCOMMERZ_STORE_PASSWORD.encode()
    ).hexdigest()
    signable = "&".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    expected = hashlib.md5(signable.encode()).hexdigest()
    return hmac.compare_digest(expected, str(verify_sign).lower())
