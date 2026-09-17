"""Run the payment maintenance jobs in their required order (QA C6).

The individual jobs are correct, but their ORDER is a safety property, not a
preference:

  reconcile_pending_payments   drives every stale PENDING payment to a terminal
                               state by asking the gateway what really happened
  expire_stale_bookings        only then releases rooms whose holds are dead —
                               it refuses to touch a booking that still has a
                               PENDING payment, so without the reconcile step
                               above it would never release ANY abandoned
                               checkout's cabin
  enforce_due_deadlines        reminds, then cancels, overdue balances
  close_sailed_bookings        closes out bookings whose tour has finished
  send_unsent_invoices         retries invoice emails that failed to send

Scheduling these as five independent crons makes their ordering depend on cron
timing and on nothing going slow — and getting it wrong means releasing a room
while money is still in flight against it. Running them in-process, in order,
in one command removes that class of failure entirely.

Failures are isolated: one job blowing up must not stop the rest (a broken
email backend must never prevent rooms being reclaimed). The command exits
non-zero if any job failed, so the platform's cron alerting sees it.

`--quick` runs only the two jobs that are time-sensitive (money in flight +
room holds); the full set is a daily run.

⚠️ NOTHING SCHEDULES THIS IN PRODUCTION YET. An earlier version of this note
claimed railway.json did; it never had a cron block, and neither render.yaml
nor the installed packages provide a scheduler. Until one exists, a customer
who abandons a checkout leaves a PENDING payment that is never reconciled —
and expire_stale_bookings rightly refuses to release a cabin while a gateway
session is open on it, so that cabin never comes back. Whoever wires up the
schedule: `--quick` every ~10 minutes, the full set once a day.
"""

import logging

from django.core.management import call_command
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

#: (command, args) in the order they MUST run.
QUICK_JOBS = [
    ("reconcile_pending_payments", []),
    ("expire_stale_bookings", []),
]

DAILY_JOBS = [
    ("enforce_due_deadlines", []),
    ("close_sailed_bookings", []),
    ("send_unsent_invoices", []),
    # Not a payment job, but it is the same problem and there is no second
    # scheduler to put it in: every staff logout blacklists a refresh token,
    # and simplejwt never removes the rows once they expire. Left alone the
    # table grows without limit for tokens that can no longer authenticate
    # anything. Last, because it is the one job nothing else depends on.
    ("flushexpiredtokens", []),
]


class Command(BaseCommand):
    help = (
        "Run the payment maintenance jobs in their required order. "
        "--quick runs only the time-sensitive pair (reconcile, then expire)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--quick",
            action="store_true",
            help=(
                "Only reconcile payments and expire stale holds — the two jobs "
                "that must run frequently. Schedule this every ~10 minutes."
            ),
        )

    def handle(self, *args, **options):
        jobs = QUICK_JOBS if options["quick"] else QUICK_JOBS + DAILY_JOBS

        failed = []
        for name, job_args in jobs:
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n== {name} =="))
            try:
                call_command(name, *job_args)
            except Exception as exc:
                # One job's failure must never stop the others: a broken email
                # backend must not prevent rooms from being reclaimed.
                failed.append(name)
                logger.exception("Payment job %s failed", name)
                self.stderr.write(self.style.ERROR(f"{name} FAILED: {exc}"))

        if failed:
            self.stderr.write(
                self.style.ERROR(f"\n{len(failed)} job(s) failed: {', '.join(failed)}")
            )
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS("\nAll payment jobs completed."))
