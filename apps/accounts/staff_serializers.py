from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

User = get_user_model()


class StaffUserSerializer(serializers.ModelSerializer):
    """A dashboard account, as an administrator manages it.

    The password is write-only and never echoed, not even straight after being
    set: an account list that can be made to reveal passwords is one screenshot
    away from being a leak. Resetting is how a forgotten one is dealt with.
    """

    password = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        style={"input_type": "password"},
        help_text=(
            "Set to create the account or to reset it. Leave out when editing "
            "to keep the current one."
        ),
    )
    role_display = serializers.CharField(source="get_role_display", read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "first_name",
            "last_name",
            "email",
            "role",
            "role_display",
            "is_active",
            "password",
            "last_login",
            "date_joined",
        ]
        read_only_fields = ["last_login", "date_joined"]

    def validate_password(self, value):
        """Run Django's own password rules rather than inventing weaker ones.

        These are the same checks `createsuperuser` applies — length, not
        entirely numeric, not a known-common password, not too close to the
        username — and staff accounts reach every booking and payment in the
        system, so they are held to it too.
        """
        if not value:
            return value
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from exc
        return value

    def validate(self, attrs):
        if self.instance is None and not attrs.get("password"):
            raise serializers.ValidationError(
                {"password": "Give the new account a password."}
            )

        # The last route back in must stay open. Removing your own access, or
        # standing down the only administrator, leaves a dashboard nobody can
        # administer and no screen on which to fix it.
        request = self.context.get("request")
        me = getattr(request, "user", None)

        if self.instance and me and self.instance.pk == me.pk:
            if attrs.get("role", self.instance.role) != User.Role.ADMIN:
                raise serializers.ValidationError(
                    {"role": "You cannot remove your own administrator access."}
                )
            if attrs.get("is_active", self.instance.is_active) is False:
                raise serializers.ValidationError(
                    {"is_active": "You cannot deactivate your own account."}
                )

        if self.instance and self.instance.role == User.Role.ADMIN:
            losing_admin = (
                attrs.get("role", self.instance.role) != User.Role.ADMIN
                or attrs.get("is_active", self.instance.is_active) is False
            )
            if losing_admin and not self._other_admins_exist():
                raise serializers.ValidationError(
                    "This is the only active administrator. Promote somebody "
                    "else first, or the dashboard will have nobody who can "
                    "manage it."
                )

        return attrs

    def _other_admins_exist(self) -> bool:
        return (
            User.objects.filter(
                is_staff=True, is_active=True, role=User.Role.ADMIN
            )
            .exclude(pk=self.instance.pk)
            .exists()
        )

    def create(self, validated_data):
        password = validated_data.pop("password")
        # is_staff is set here rather than exposed as a field: every account
        # this endpoint creates is a dashboard account by definition, and
        # leaving it writable would allow one that exists but cannot log in.
        user = User(**validated_data, is_staff=True)
        user.set_password(password)
        user.save()
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop("password", "")
        user = super().update(instance, validated_data)
        if password:
            user.set_password(password)
            user.save(update_fields=["password"])
        return user
