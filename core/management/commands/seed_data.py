from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Bulk-generate 10k subscriptions for load and benchmark testing."

    def add_arguments(self, parser):
        parser.add_argument(
            "--count",
            type=int,
            default=10000,
            help="Number of subscriptions to seed (default: 10000)",
        )

    def handle(self, *args, **options):
        count = options["count"]
        self.stdout.write(
            self.style.SUCCESS(
                f"Stub seed_data command invoked. Target count: {count} subscriptions."
            )
        )
