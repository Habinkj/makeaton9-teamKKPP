from datetime import date

from django.core.management.base import BaseCommand

from core import billing, clock


class Command(BaseCommand):
    help = "Bill every subscription that is due. Safe to run any number of times (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--as-of", type=date.fromisoformat, default=None,
                            help="Bill as if today were this date (YYYY-MM-DD).")
        parser.add_argument("--dry-run", action="store_true", help="Show what would be billed; write nothing.")
        parser.add_argument("--limit", type=int, default=None)

    def handle(self, *args, as_of=None, dry_run=False, limit=None, **opts):
        as_of = as_of or clock.today()
        if dry_run:
            p = billing.preview(as_of, limit=limit or 50)
            self.stdout.write(f"DRY RUN as of {as_of}: {p['due_count']} subscription(s) due, "
                              f"{len(p['exceptions'])} exception(s), {p['paused_count']} paused.")
            for r in p["rows"]:
                self.stdout.write(f"  {r['sub'].number}: {r['periods']} period(s), first total "
                                  f"{r['first_total']} paise from {r['first_start']}")
            return
        s = billing.run_billing(as_of, limit=limit)
        self.stdout.write(self.style.SUCCESS(s.as_text()))
        for number, msg in s.errors:
            self.stdout.write(self.style.ERROR(f"  {number}: {msg}"))
