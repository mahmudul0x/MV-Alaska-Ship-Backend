"""
Who may reach which part of the dashboard.

Access is granted per capability — the areas of work listed in
`capabilities.py` — which an administrator ticks per account. Administrators
hold all of them implicitly.

The important property is that this fails CLOSED. A capability is something an
account was given, not something it was not denied, so a new area added to the
catalogue next year starts out ungranted for everyone and has to be handed out
deliberately.

Managing staff accounts is the one thing NOT expressible as a capability. It is
tied to the administrator role, because granting it is indistinguishable from
granting everything: whoever holds it can make themselves an administrator.
"""

from rest_framework.permissions import SAFE_METHODS, BasePermission


def _is_dashboard_user(request) -> bool:
    user = getattr(request, "user", None)
    return bool(user and user.is_authenticated and user.is_active and user.is_staff)


class IsDashboardUser(BasePermission):
    """Any active staff account.

    For the few endpoints every signed-in person needs whatever they can do —
    the overview, their notifications, logging out.
    """

    message = "This account does not have dashboard access."

    def has_permission(self, request, view):
        return _is_dashboard_user(request)


class IsAdminRole(BasePermission):
    """Administrators only. Staff account management, and nothing else."""

    message = "Only an administrator can do this."

    def has_permission(self, request, view):
        return _is_dashboard_user(request) and request.user.is_admin_role


class HasCapability(BasePermission):
    """Requires a named capability — or any one of several.

    Used as `permission_classes = [HasCapability.of("bookings")]`, or
    `HasCapability.of("bookings", "packages")` where either is enough. A
    subclass per combination rather than an instance, because DRF instantiates
    permission classes itself and cannot pass arguments to them.

    "Any of" rather than "all of" is the only combination offered, because it
    is the only one the dashboard needs: a screen two different jobs both
    legitimately use. Where a single request needs two things at once — a
    sailing edit that also changes its price — the serializer checks the
    second one against the fields actually being changed.
    """

    capabilities: tuple[str, ...] = ()
    message = "This account has not been given access to this part of the dashboard."

    @classmethod
    def of(cls, *capabilities: str):
        return type(
            f"{cls.__name__}_{'_or_'.join(capabilities)}",
            (cls,),
            {
                "capabilities": tuple(capabilities),
                "message": (
                    "This account has not been given access to this part of "
                    "the dashboard. An administrator can grant it under Staff."
                ),
            },
        )

    def _holds_one(self, request) -> bool:
        return any(request.user.has_capability(c) for c in self.capabilities)

    def has_permission(self, request, view):
        return _is_dashboard_user(request) and self._holds_one(request)


class CapabilityOrReadOnly(HasCapability):
    """Everyone on the dashboard may look; the capability is needed to change.

    For the catalogue — sailings, cabins, room types, ships. Somebody taking
    bookings must be able to SEE these, because a booking is made against
    them, whether or not they were given permission to edit them. Hiding them
    instead would leave the booking form unable to name what it is booking.
    """

    def has_permission(self, request, view):
        if not _is_dashboard_user(request):
            return False
        if request.method in SAFE_METHODS:
            return True
        return self._holds_one(request)


class IsAdminOrReadOnly(BasePermission):
    """Read for any dashboard user, write for administrators only.

    Kept for the handful of settings that have no capability of their own.
    """

    message = "Only an administrator can change this."

    def has_permission(self, request, view):
        if not _is_dashboard_user(request):
            return False
        if request.method in SAFE_METHODS:
            return True
        return request.user.is_admin_role
