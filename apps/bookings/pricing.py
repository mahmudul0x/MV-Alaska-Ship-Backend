"""Booking price calculation — the single pricing authority.

Consumers: Booking.clean() (admin + API create), the public quote endpoint,
and later payment verification (Phase 4) / invoice breakdown (Phase 5).
Amounts must never be trusted from the client; they are always recomputed here.
All arithmetic is Decimal — never float.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.packages.models import KidPricingRule

ZERO = Decimal("0.00")


def price_breakdown(room_type, package, adult_count, kid_ages):
    """Full price breakdown, every amount a Decimal.

    Room total = base_price + (adults × adult_price) + Σ kid tier charges.
    """
    adults_subtotal = package.adult_price * adult_count
    kids = [{"age": age, "charge": kid_charge(age, package)} for age in kid_ages]
    kids_subtotal = sum((kid["charge"] for kid in kids), ZERO)
    return {
        "room_base": room_type.base_price,
        "adult_price": package.adult_price,
        "adult_count": adult_count,
        "adults_subtotal": adults_subtotal,
        "kids": kids,
        "kids_subtotal": kids_subtotal,
        "total": room_type.base_price + adults_subtotal + kids_subtotal,
    }


def calculate_total(room_type, package, adult_count, kid_ages):
    return price_breakdown(room_type, package, adult_count, kid_ages)["total"]


def snapshot_breakdown(breakdown):
    """Breakdown → JSON-safe dict for Booking.price_snapshot.

    Decimals become strings ("1500.00"), never floats — a float would round
    money the moment it is stored. `restore_breakdown` is the exact inverse.
    """
    return {
        "room_base": str(breakdown["room_base"]),
        "adult_price": str(breakdown["adult_price"]),
        "adult_count": breakdown["adult_count"],
        "adults_subtotal": str(breakdown["adults_subtotal"]),
        "kids": [
            {"age": kid["age"], "charge": str(kid["charge"])}
            for kid in breakdown["kids"]
        ],
        "kids_subtotal": str(breakdown["kids_subtotal"]),
        "total": str(breakdown["total"]),
    }


def restore_breakdown(snapshot):
    """Booking.price_snapshot → breakdown with Decimals back (inverse of
    snapshot_breakdown). Returns None for an empty/absent snapshot."""
    if not snapshot:
        return None
    return {
        "room_base": Decimal(snapshot["room_base"]),
        "adult_price": Decimal(snapshot["adult_price"]),
        "adult_count": snapshot["adult_count"],
        "adults_subtotal": Decimal(snapshot["adults_subtotal"]),
        "kids": [
            {"age": kid["age"], "charge": Decimal(kid["charge"])}
            for kid in snapshot["kids"]
        ],
        "kids_subtotal": Decimal(snapshot["kids_subtotal"]),
        "total": Decimal(snapshot["total"]),
    }


def kid_charge(age, package):
    rule = KidPricingRule.rule_for_age(age)
    if rule is None:
        raise ValidationError(
            f"No kid pricing rule covers age {age}. "
            "Configure KidPricingRules in the admin panel."
        )
    if rule.charge_type == KidPricingRule.ChargeType.FREE:
        return ZERO
    if rule.charge_type == KidPricingRule.ChargeType.FIXED:
        return rule.amount
    return package.adult_price
