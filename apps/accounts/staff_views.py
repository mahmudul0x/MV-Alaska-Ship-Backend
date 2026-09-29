from django.contrib.auth import get_user_model
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError

from apps.accounts.permissions import IsAdminRole

from .staff_serializers import StaffUserSerializer

User = get_user_model()


class StaffUserViewSet(viewsets.ModelViewSet):
    """Dashboard accounts, managed by an administrator.

    Scoped to `is_staff` accounts: customers do not have logins in this
    system, so every row here is somebody who works on the dashboard, and a
    queryset that returned anything else would be a list of the wrong people.

    Unpaginated — a cruise operator has a handful of staff, and the page wants
    the deactivated ones visible so somebody returning can be switched back on
    rather than created again.
    """

    permission_classes = [IsAdminRole]
    pagination_class = None
    serializer_class = StaffUserSerializer
    queryset = User.objects.filter(is_staff=True).order_by("-is_active", "username")

    def perform_destroy(self, instance):
        """Accounts are deactivated, not deleted.

        A staff account is referenced by the bookings it created, the payments
        it recorded and the status changes it signed — deleting it would either
        take that history with it or leave it unattributable. Deactivating
        ends the access, which is the part anybody actually wants, and keeps
        the record of who did what.
        """
        if instance.pk == self.request.user.pk:
            raise ValidationError({"detail": "You cannot remove your own account."})

        if (
            instance.role == User.Role.ADMIN
            and instance.is_active
            and not User.objects.filter(
                is_staff=True, is_active=True, role=User.Role.ADMIN
            )
            .exclude(pk=instance.pk)
            .exists()
        ):
            raise ValidationError(
                {
                    "detail": (
                        "This is the only active administrator — promote "
                        "somebody else first."
                    )
                }
            )

        instance.is_active = False
        instance.save(update_fields=["is_active"])
