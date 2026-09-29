"""
What a dashboard account can be given permission to do.

One catalogue, in one file, served to the dashboard over the API so the
checkboxes an administrator ticks are generated from the same list the
endpoints enforce. A second copy in the frontend would drift, and the way that
drift shows up is a box somebody ticks that grants nothing, or a screen that
appears and then 403s.

Granularity is per *area of work*, not per endpoint. "Can take bookings" is
something an owner can reason about; "can POST to /api/staff/booking-rooms/"
is not, and a list of forty of those is one nobody reads before ticking.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Capability:
    key: str
    label: str
    #: What ticking it actually allows, in the words the owner would use.
    description: str
    #: Heading it sits under on the form.
    group: str
    #: Warn before granting. Not a block — an owner may well want a manager
    #: who can refund — but these are the ones where a mis-tick costs money or
    #: reaches the public, so the form says so out loud.
    sensitive: bool = False


#: Ordered: the form renders them in this order, grouped by `group`.
CAPABILITIES: tuple[Capability, ...] = (
    # ── Day-to-day ──────────────────────────────────────────────────────────
    Capability(
        key="bookings",
        label="Take and manage bookings",
        description=(
            "Create bookings, edit them, change their status, and see who is "
            "in each cabin on the room map."
        ),
        group="Day-to-day",
    ),
    Capability(
        key="payments",
        label="Record payments",
        description="Mark money received against a booking and resolve payments.",
        group="Day-to-day",
    ),
    Capability(
        key="invoices",
        label="Invoices and guide reports",
        description=(
            "View and send invoices, and print the guide's collection sheet — "
            "which is the passenger list, with phone numbers and balances."
        ),
        group="Day-to-day",
    ),
    Capability(
        key="messages",
        label="Customer messages",
        description="Read and handle messages sent through the contact form.",
        group="Day-to-day",
    ),
    # ── Money out ───────────────────────────────────────────────────────────
    Capability(
        key="refunds",
        label="Cancellations and refunds",
        description=(
            "Approve cancellations and issue refunds. This is money leaving "
            "the company."
        ),
        group="Money",
        sensitive=True,
    ),
    Capability(
        key="pricing",
        label="Prices and pricing rules",
        description=(
            "Change what a sailing costs and the offer it is sold at, deposit "
            "terms, the kid-pricing tiers and the foreigner surcharge."
        ),
        group="Money",
        sensitive=True,
    ),
    # ── Setting up ──────────────────────────────────────────────────────────
    Capability(
        key="packages",
        label="Create and edit sailings",
        description=(
            "Add sailings and change their dates and details, open and close "
            "booking, and block or release cabins. A new sailing can be added "
            "at the ship's standard fare; any other price needs the prices "
            "permission too."
        ),
        group="Setting up",
    ),
    Capability(
        key="rooms",
        label="Rooms and cabins",
        description="Room types, cabins, room numbers and their photos.",
        group="Setting up",
    ),
    Capability(
        key="food_menu",
        label="Food menu",
        description="What the chef may serve on each day of the voyage.",
        group="Setting up",
    ),
    # ── The public site ─────────────────────────────────────────────────────
    Capability(
        key="promotions",
        label="Publish offers",
        description=(
            "Write the pop-up, the top bar and the home-page offer card. This "
            "speaks to the public in the company's name."
        ),
        group="Public site",
        sensitive=True,
    ),
    Capability(
        key="media",
        label="Gallery photos",
        description="Photographs on the public gallery page.",
        group="Public site",
    ),
    Capability(
        key="settings",
        label="Ship settings",
        description="Helpline numbers, default fares and report settings.",
        group="Setting up",
        sensitive=True,
    ),
)

CAPABILITY_KEYS = frozenset(c.key for c in CAPABILITIES)

#: Ticked by default when an administrator creates a new account. The desk job
#: as it is usually meant — enough to take a booking end to end, and nothing
#: that changes what anything costs or what the public sees.
DEFAULT_CAPABILITIES: tuple[str, ...] = (
    "bookings",
    "payments",
    "invoices",
    "messages",
)

#: NOT in the catalogue, and deliberately so: managing staff accounts.
#:
#: Granting it is indistinguishable from granting everything, because whoever
#: holds it can make themselves an administrator in two clicks. It stays tied
#: to the administrator role, where it is visible as one decision rather than
#: hidden as one tick among twelve.
