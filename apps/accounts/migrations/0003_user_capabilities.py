"""Add per-account capabilities, and fill them in for accounts that exist.

The field alone would default every existing account to an empty list. For
administrators that is harmless — `has_capability` short-circuits on the role —
but any booking-staff account created between the role shipping and this
landing would sign in to a dashboard that refuses everything, which reads as
the system being broken rather than as permissions being unset.

So the data migration gives those accounts the desk job they were created to
do. Administrators are left with an empty list on purpose: their access comes
from the role, and filling it in would suggest it comes from the list.
"""

from django.db import migrations, models


def grant_the_desk_job(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    # Literal rather than imported from capabilities.py: a migration has to
    # keep meaning what it meant on the day it ran, and that catalogue will
    # grow.
    User.objects.filter(role="booking").update(
        capabilities=["bookings", "payments", "invoices", "messages"]
    )


def unset(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.update(capabilities=[])


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_user_role"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="capabilities",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text=(
                    "Which areas of the dashboard this account may use. "
                    "Ignored for administrators, who have all of them. See "
                    "apps/accounts/capabilities.py for the list."
                ),
            ),
        ),
        migrations.RunPython(grant_the_desk_job, unset),
    ]
