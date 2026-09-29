from django.contrib.auth.models import AbstractUser
from django.db import models


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
        BOOKING = "booking", "Booking staff"

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        # ADMIN, deliberately: the existing accounts predate this field and are
        # administrators, and a migration that silently demoted them would lock
        # the owner out of their own dashboard. New accounts are created
        # through the staff API, which asks for the role explicitly.
        default=Role.ADMIN,
        help_text=(
            "Administrators manage everything. Booking staff work with "
            "bookings, payments and invoices, and can read the sailings and "
            "cabins they need to book against — but cannot change prices, "
            "offers, refunds or other staff."
        ),
    )

    @property
    def is_admin_role(self) -> bool:
        """Whether this account has the run of the dashboard.

        A superuser always counts, whatever the field says. Otherwise a
        mis-set role on the only superuser would lock the system's owner out
        of the screens that fix it — and `createsuperuser` does not ask for a
        role.
        """
        return self.is_superuser or self.role == self.Role.ADMIN
