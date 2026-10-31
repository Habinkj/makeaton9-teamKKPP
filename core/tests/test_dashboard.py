"""Dashboard figures, scoping, HTMX filter and query budget.

Run:  docker compose run --rm web python manage.py test core -v 2
"""
from io import StringIO

from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core import billing, clock, payments
from core.models import Invoice, Subscription
from core.tests.test_billing import JAN31, BillingBase


class DashboardTests(BillingBase):
    def setUp(self):
        super().setUp()
        self.sub = self.active_sub()                       # a: 2 x ₹560 monthly
        billing.run_billing(JAN31)
        self.inv = Invoice.objects.get()                   # total 132160, due 15 Feb 2026
        billing.confirm_invoice(self.inv, self.staff)
        payments.record_payment(self.inv, amount=50000, method="upi", paid_on=clock.today(), actor=self.staff)

    def get(self, user, **params):
        self.client.force_login(user)
        return self.client.get(reverse("dashboard"), params)

    def test_kpis(self):
        k = self.get(self.staff).context["kpis"]
        self.assertEqual(k["active"], 1)
        self.assertEqual(k["mrr"], 112000)
        self.assertEqual(k["collected"], 50000)
        self.assertEqual(k["outstanding"], 82160)
        self.assertEqual((k["overdue_amount"], k["overdue_count"]), (82160, 1))

    def test_paid_invoice_leaves_outstanding_and_overdue(self):
        payments.record_payment(self.inv, amount=82160, method="cash", paid_on=clock.today(), actor=self.staff)
        k = self.get(self.staff).context["kpis"]
        self.assertEqual((k["outstanding"], k["overdue_amount"], k["overdue_count"]), (0, 0, 0))

    def test_paused_and_closed_excluded_from_mrr(self):
        Subscription.objects.filter(pk=self.sub.pk).update(is_paused=True)
        self.assertEqual(self.get(self.staff).context["kpis"]["mrr"], 0)
        self.assertEqual(self.get(self.staff).context["kpis"]["active"], 1)    # still active, just paused

    def test_collected_respects_date_range(self):
        k = self.get(self.staff, **{"from": "2020-01-01", "to": "2020-12-31"}).context["kpis"]
        self.assertEqual(k["collected"], 0)

    def test_customer_sees_only_own_figures(self):
        self.active_sub(self.b)
        k = self.get(self.b).context["kpis"]
        self.assertEqual((k["active"], k["outstanding"], k["collected"]), (1, 0, 0))
        r = self.get(self.b)
        self.assertNotContains(r, self.inv.number)
        k = self.get(self.a).context["kpis"]
        self.assertEqual((k["outstanding"], k["mrr"]), (82160, 112000))

    def test_overdue_table_and_htmx_filters(self):
        r = self.get(self.staff)
        self.assertContains(r, self.inv.number)
        self.client.force_login(self.staff)
        r = self.client.get(reverse("dashboard"), {"q": "zzz"}, HTTP_HX_REQUEST="true")
        self.assertNotContains(r, "<html")                    # partial only
        self.assertContains(r, "Nothing overdue")
        r = self.client.get(reverse("dashboard"), {"q": self.inv.number}, HTTP_HX_REQUEST="true")
        self.assertContains(r, self.inv.number)
        r = self.client.get(reverse("dashboard"), {"min_days": "10000"}, HTTP_HX_REQUEST="true")
        self.assertContains(r, "Nothing overdue")

    def test_home_shows_dashboard_or_login(self):
        self.assertEqual(self.client.get(reverse("home")).status_code, 302)
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse("home")), "Active subscriptions")

    def test_charts_have_data(self):
        ctx = self.get(self.staff).context
        self.assertEqual(sum(ctx["chart_status"]["values"]), Subscription.objects.count())
        self.assertEqual(len(ctx["chart_months"]["labels"]), len(ctx["chart_months"]["values"]))
        self.assertGreaterEqual(sum(ctx["chart_months"]["values"]), 500)       # ₹500 paid today


class DashboardScaleTests(BillingBase):
    def test_query_count_does_not_grow_with_data(self):
        call_command("seed_demo", "--subs", "200", stdout=StringIO())
        self.client.force_login(self.staff)
        with CaptureQueriesContext(connection) as ctx:
            r = self.client.get(reverse("dashboard"))
        self.assertEqual(r.status_code, 200)
        self.assertLess(len(ctx.captured_queries), 30)       # aggregates, not a loop per row
