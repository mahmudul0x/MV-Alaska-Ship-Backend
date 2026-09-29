from django.contrib.auth.models import AbstractUser
from django.db import models

from .capabilities import CAPABILITY_KEYS, DEFAULT_CAPABILITIES


class User(AbstractUser):
    """Custom user model.

    `is_staff` still answers "may this person open the dashboard at all"; the
    role answers "and how much of it". The two are separate on purpose —
    revoking `is_staff` locks someone out entirely, which is what you want when
    somebody leaves, while changing their role narrows what they can reach
    without disturbing their account or their history.
    """

    class Role(models.TextChoices):
        ADMIN = "admin", "Administrator"
        # The stored value stays "booking" — it predates per-account
        # capabilities, and renaming a stored value is a data migration for
        # no gain. What the account can do is its `capabilities`, not this.
        BOOKING = "booking", "Staff (chosen permissions)"

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        # ADMIN, deliberately: the existing accounts predate this field and are
        # administrators, and a migration that silently demoted them would lock
        # the owner out of their own dashboard. New accounts are created
        # through the staff API, which asks for the role explicitly.
        default=Role.ADMIN,
        help_text=(
            "Administrators can do everything, including managing other "
            "accounts. Everyone else can do exactly what is ticked in their "
            "capabilities, and can never manage accounts."
        ),
    )

    capabilities = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Which areas of the dashboard this account may use. Ignored for "
            "administrators, who have all of them. See "
            "apps/accounts/capabilities.py for the list."
        ),
    )

    def has_capability(self, key: str) -> bool:
        """Whether this account may work in the given area.

        Administrators always may. Everyone else holds an explicit list, so
        access is something that was granted rather than something that was
        not taken away — a new capability added to the catalogue next year
        starts off ungranted for everybody, which is the safe direction for it
        to fail in.
        """
        if self.is_admin_role:
            return True
        return key in (self.capabilities or [])

    @property
    def is_admin_role(self) -> bool:
        """Whether this account has the run of the dashboard.

        A superuser always counts, whatever the field says. Otherwise a
        mis-set role on the only superuser would lock the system's owner out
        of the screens that fix it — and `createsuperuser` does not ask for a
        role.
        """
        return self.is_superuser or self.role == self.Role.ADMIN
