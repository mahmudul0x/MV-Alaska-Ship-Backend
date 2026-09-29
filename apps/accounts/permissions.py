"""
Who may reach which part of the dashboard.

Three classes, because there are three honest answers:

- `IsDashboardUser` — the day-to-day work. Bookings, the payments taken
  against them, invoices, customer messages. Both roles.
- `IsAdminOrReadOnly` — the catalogue. Booking staff must be able to SEE
  sailings, cabins and room types, because a booking is made against them;
  they must not be able to edit them, because that is the price list.
- `IsAdminRole` — everything that moves money outward, changes what things
  cost, speaks to the public in the company's name, or grants access to
  another person.

The default stays closed: a viewset with no `permission_classes` inherits
DRF's project default, so every new staff endpoint must say which of these it
is. When in doubt, `IsAdminRole` — the failure mode of guessing too narrow is
somebody asking for access, and of guessing too wide is a refund being issued
by someone who should not be able to.
"""

from rest_framework.permissions import SAFE_METHODS, BasePermission


def _is_dashboard_user(request) -> bool:
    user = getattr(request, "user", None)
    return bool(user and user.is_authenticated and user.is_active and user.is_staff)


class IsDashboardUser(BasePermission):
    """Any active staff account, of either role."""

    message = "This account does not have dashboard access."

    def has_permission(self, request, view):
        return _is_dashboard_user(request)


class IsAdminRole(BasePermission):
    """Administrators only."""

    message = (
        "Only an administrator can do this. Ask whoever manages the dashboard."
    )

    def has_permission(self, request, view):
        return _is_dashboard_user(request) and request.user.is_admin_role


class IsAdminOrReadOnly(BasePermission):
    """Everyone on the dashboard may look; only an administrator may change.

    For the catalogue — sailings, cabins, room types, ships. Booking staff
    genuinely need to read these to take a booking at all, and equally must not
    be able to edit a price or move a sailing's dates.
    """

    message = (
        "Only an administrator can change this. You can still view it, and "
        "book against it."
    )

    def has_permission(self, request, view):
        if not _is_dashboard_user(request):
            return False
        if request.method in SAFE_METHODS:
            return True
        return request.user.is_admin_role
