"""
Promotions — the marketing announcements staff publish from the dashboard.

Deliberately separate from `Package.offer_label` / `discount_type` /
`discount_value`, which are a *price* the sailing carries and feed
`price_breakdown()`, the quote, the booking snapshot and the invoice. Those
fields decide what a customer is charged. What lives here decides only what a
visitor is *shown*, and can never move money.

Keeping them apart is the point: editing a banner headline must not be able to
reprice a cabin, and ending a discount must not require someone to remember to
take a poster down. A promotion may still point at a package
(`linked_package`), which is how "Eid Offer — see the sailing" gets a button
that goes somewhere useful.
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.packages.models import Package
from apps.ships.models import Ship


def promotion_image_path(promotion, filename):
    """promotions/<ship_id>/<original name> — keyed by ship so each ship keeps
    its own folder, the same shape the gallery and cabin uploads use."""
    return f"promotions/{promotion.ship_id}/{filename}"


class ModalFrequency(models.TextChoices):
    """How often one visitor is shown the modal.

    Enforced in the browser (there is no visitor identity to enforce it
    against on the server), but the *policy* is staff-controlled data rather
    than a number buried in the frontend.
    """

    ONCE = "once", "Once per visitor"
    DAILY = "daily", "Once a day"
    EVERY_VISIT = "every_visit", "Every visit"


class Promotion(models.Model):
    """A staff-authored announcement: badge, headline, artwork and a button.

    Where it appears is three independent switches rather than one "type",
    because the same announcement is normally wanted in more than one place —
    a modal on arrival AND a strip across the hero — and staff should not have
    to create the same content twice to get that.
    """

    ship = models.ForeignKey(
        Ship, on_delete=models.CASCADE, related_name="promotions"
    )

    # ── Content ──────────────────────────────────────────────────────────────
    badge_label = models.CharField(
        max_length=40,
        blank=True,
        help_text='Small pill above the headline, e.g. "EID OFFER" or "LIMITED SEATS".',
    )
    title = models.CharField(
        max_length=120,
        help_text="The headline. Keep it short — it is set large.",
    )
    subtitle = models.CharField(
        max_length=200,
        blank=True,
        help_text="One supporting line, shown under the headline everywhere.",
    )
    body = models.TextField(
        blank=True,
        help_text=(
            "Longer text, shown only in the modal where there is room for it. "
            "The hero strip and the home banner never show this."
        ),
    )
    image = models.ImageField(
        upload_to=promotion_image_path,
        blank=True,
        null=True,
        help_text=(
            "Banner artwork for the modal and the home section. Optional — "
            "without it both fall back to a text-only layout that still looks "
            "deliberate, so a promotion is never blocked on waiting for a designer."
        ),
    )

    # ── Action ───────────────────────────────────────────────────────────────
    cta_label = models.CharField(
        max_length=40,
        blank=True,
        help_text='Button text, e.g. "See packages". Leave blank for no button.',
    )
    cta_url = models.CharField(
        max_length=300,
        blank=True,
        help_text=(
            'Where the button goes — a path on this site ("/packages") or a '
            "full URL. Ignored when a package is linked below."
        ),
    )
    linked_package = models.ForeignKey(
        Package,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="promotions",
        help_text=(
            "Optional. When set, the button goes to this sailing and the "
            "promotion disappears on its own once the sailing has departed."
        ),
    )

    # ── Placement ────────────────────────────────────────────────────────────
    show_in_modal = models.BooleanField(
        default=True,
        help_text="Pop up on the home page shortly after the visitor arrives.",
    )
    show_in_top_bar = models.BooleanField(
        default=True,
        help_text=(
            "A slim bar pinned above the navigation, on every page of the site."
        ),
    )
    show_in_home_section = models.BooleanField(
        default=True,
        help_text="A full banner card in the flow of the home page.",
    )

    # ── Modal behaviour ──────────────────────────────────────────────────────
    modal_frequency = models.CharField(
        max_length=12,
        choices=ModalFrequency.choices,
        default=ModalFrequency.DAILY,
        help_text=(
            "How often one visitor sees the modal. 'Every visit' is deliberately "
            "available but annoys returning customers — prefer 'Once a day'."
        ),
    )
    modal_delay_seconds = models.PositiveSmallIntegerField(
        default=3,
        help_text=(
            "Seconds before the modal opens. A short delay lets the page paint "
            "first; opening instantly reads as a popup ad."
        ),
    )

    # ── Scheduling ───────────────────────────────────────────────────────────
    starts_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When it begins showing (Bangladesh time). Blank means immediately, "
            "so a promotion can be written today and published the moment it is "
            "switched on."
        ),
    )
    ends_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When it stops showing. Blank means it runs until switched off by "
            "hand — which someone has to remember to do, so prefer setting a date."
        ),
    )
    is_active = models.BooleanField(
        default=True,
        help_text=(
            "The manual switch, on top of the dates. Unchecking hides it "
            "everywhere at once without deleting the content."
        ),
    )

    sort_order = models.PositiveSmallIntegerField(
        default=0,
        help_text=(
            "Lower shows first when more than one promotion is live. The modal "
            "and the hero strip only ever show the first one."
        ),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "-created_at"]
        indexes = [
            # The public endpoint filters on exactly this, on every home-page
            # visit.
            models.Index(fields=["is_active", "sort_order"]),
        ]

    def __str__(self):
        return f"{self.ship.name} — {self.title}"

    # ── Behaviour ────────────────────────────────────────────────────────────

    def is_live(self, now=None):
        """Whether this should be shown to the public right now.

        Three things have to agree: the manual switch, the scheduled window,
        and — when a package is linked — that the sailing has not already
        departed. That last one exists because the most common promotion is
        for a specific sailing, and nobody remembers to take those down.
        """
        if not self.is_active:
            return False

        now = now or timezone.now()
        if self.starts_at and self.starts_at > now:
            return False
        if self.ends_at and self.ends_at <= now:
            return False

        if self.linked_package_id and self.linked_package.start_date < now.date():
            return False

        return True

    def clean(self):
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError(
                {"ends_at": "The end must come after the start."}
            )

        # A button with no destination renders as a dead control; a destination
        # with no label renders as nothing at all. Catch both here rather than
        # letting staff discover it on the live site.
        if self.cta_label and not (self.cta_url or self.linked_package_id):
            raise ValidationError(
                {
                    "cta_url": (
                        "A button needs somewhere to go — set a link or pick a "
                        "package, or clear the button text."
                    )
                }
            )
        if (self.cta_url or self.linked_package_id) and not self.cta_label:
            raise ValidationError(
                {"cta_label": "Give the button some text, or clear its link."}
            )

        if not (
            self.show_in_modal
            or self.show_in_top_bar
            or self.show_in_home_section
        ):
            raise ValidationError(
                "Choose at least one place to show this, or switch it off "
                "instead — with all three unchecked it is active but invisible."
            )

    @property
    def resolved_cta_url(self):
        """Where the button actually goes.

        A linked package wins over a typed URL: it is the more specific intent,
        and it keeps working if the marketing copy is edited later.

        It lands on the booking flow with that sailing already chosen, not on
        the packages list — somebody who clicked an offer for a named sailing
        has already decided which one, and making them find it again in a list
        is a step at which people leave. `?package=` is the parameter the
        booking route reads (see `validateSearch` in routes/booking.tsx); keep
        the two in step.
        """
        if self.linked_package_id:
            return f"/booking?package={self.linked_package_id}"
        return self.cta_url
