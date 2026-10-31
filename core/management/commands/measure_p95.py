"""Measure server-side p50/p95/max for the main pages and write docs/performance.md.

    docker compose run --rm web python manage.py measure_p95
    docker compose run --rm web python manage.py measure_p95 --runs 100 --budget 500

Reads only (GET requests). Writes nothing to the database except the login session row.
"""
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.models import Q
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core import perf
from core.models import Invoice, Payment, Subscription, User

HTMX = {"HTTP_HX_REQUEST": "true"}


def build_pages():
    """(name, url, extra headers). Detail pages use real rows, so the seed must exist."""
    pages = [
        ("Dashboard", reverse("dashboard"), {}),
        ("Dashboard overdue filter (HTMX)", reverse("dashboard") + "?q=a&min_days=30", HTMX),
        ("Subscriptions list", reverse("subscription_list"), {}),
        ("Subscriptions: active", reverse("subscription_list") + "?status=active", {}),
        ("Subscriptions: search", reverse("subscription_list") + "?q=SUB-0001", {}),
        ("Subscriptions: deep page", reverse("subscription_list") + "?page=300", {}),
        ("Invoices list", reverse("invoice_list"), {}),
        ("Invoices: overdue", reverse("invoice_list") + "?status=overdue", {}),
        ("Invoices: search", reverse("invoice_list") + "?q=INV-0004", {}),
        ("Billing preview", reverse("billing_home"), {}),
        ("Catalog: products", reverse("catalog_list", args=["products"]), {}),
    ]
    sub = Subscription.objects.order_by("-id").first()
    if sub:
        pages.append(("Subscription detail", reverse("subscription_detail", args=[sub.pk]), {}))
    inv = Invoice.objects.order_by("-id").first()
    if inv:
        pages.append(("Invoice detail", reverse("invoice_detail", args=[inv.pk]), {}))
    return pages


class Command(BaseCommand):
    help = "Time the main pages (server side) and write p50/p95/max to docs/performance.md."

    def add_arguments(self, parser):
        parser.add_argument("--runs", type=int, default=50, help="Timed requests per page.")
        parser.add_argument("--warmup", type=int, default=3, help="Untimed requests per page first.")
        parser.add_argument("--budget", type=float, default=2000.0, help="p95 budget in ms.")
        parser.add_argument("--email", default=None, help="Admin to log in as (default: first admin).")
        parser.add_argument("--out", default=str(Path(settings.BASE_DIR) / "docs" / "performance.md"))

    def handle(self, *args, runs, warmup, budget, email, out, **opts):
        if runs < 1:
            raise CommandError("--runs must be at least 1")
        user = self._admin(email)
        client = Client()
        client.force_login(user)
        rows = []
        with override_settings(DEBUG=False):
            for name, url, headers in build_pages():
                for _ in range(warmup):
                    client.get(url, **headers)
                samples, status = [], None
                for _ in range(runs):
                    t0 = time.perf_counter()
                    r = client.get(url, **headers)
                    samples.append((time.perf_counter() - t0) * 1000)
                    status = r.status_code
                if status != 200:
                    raise CommandError(f"{name} ({url}) returned {status}; fix that before measuring.")
                with CaptureQueriesContext(connection) as q:
                    client.get(url, **headers)
                s = perf.summarize(samples)
                rows.append({"name": name, "url": url, "status": status, "queries": len(q), **s})
                self.stdout.write(f"{name:34} p50 {s['p50']:7.1f}  p95 {s['p95']:7.1f}  "
                                  f"max {s['max']:7.1f} ms  {len(q):3} queries")

        counts = (f"{Subscription.objects.count():,} subscriptions, {Invoice.objects.count():,} invoices, "
                  f"{Payment.objects.count():,} payments")
        stamp = timezone.localtime().strftime("%Y-%m-%d %H:%M %Z")
        text, verdict = perf.markdown_report(rows, runs=runs, warmup=warmup, counts=counts,
                                             budget_ms=budget, stamp=stamp)
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        style = self.style.SUCCESS if verdict == "PASS" else self.style.ERROR
        self.stdout.write(style(f"{verdict}: worst p95 {max(r['p95'] for r in rows):.1f} ms "
                                f"(budget {budget:.0f} ms). Wrote {path}"))

    def _admin(self, email):
        qs = User.objects.filter(is_active=True)
        if email:
            user = qs.filter(email__iexact=email).first()
        else:
            user = qs.filter(Q(is_superuser=True) | Q(groups__name="admin")).order_by("id").first()
        if not user:
            raise CommandError("No admin user found. Create one with createsuperuser or pass --email.")
        return user
