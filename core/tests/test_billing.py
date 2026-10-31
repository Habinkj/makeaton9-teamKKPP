"""Billing engine with the database: idempotency, catch-up, discounts, invoice actions.

Run:  docker compose run --rm web python manage.py test core -v 2
"""
from datetime import date
from io import StringIO
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core import billing
from core import subscriptions as svc
from core.models import Discount, Invoice, Payment, Plan, Product, Subscription, TaxRule
from core.statemachine import InvalidTransition
from core.tests.test_auth import make_user

JAN31, FEB28, MAR31, APR30 = date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)


class BillingBase(TestCase):
    def setUp(self):
        self.staff = make_user("staff@brightloop.test", "staff")
        self.a = make_user("a@brightloop.test", "customer", with_customer=True)
        self.b = make_user("b@brightloop.test", "customer", with_customer=True)
        gst = TaxRule.objects.create(name="GST 18%", rate_bps=1800)
        self.product = Product.objects.create(name="CRM Seat", sales_price=56000)
        self.product.tax_rules.add(gst)
        self.plan = Plan.objects.create(name="Monthly", price=0, billing_period="monthly",
                                        min_quantity=1, available_from=date(2026, 1, 1))

    def active_sub(self, user=None, start=JAN31, qty=2, plan=None, **fields):
        sub = svc.create_subscription(customer=(user or self.a).customer, plan=plan or self.plan,
                                      start_date=start, payment_terms="net15", actor=self.staff)
        svc.add_line(sub, self.product, None, qty)
        for s in ("quotation", "confirmed", "active"):
            svc.do_transition(sub, s, self.staff)
        if fields:
            Subscription.objects.filter(pk=sub.pk).update(**fields)
        sub.refresh_from_db()
        return sub

    def sub_discount(self, **kw):
        d = dict(name="Loyalty 10%", kind="percent", value=1000, scope="subscription",
                 starts_on=date(2026, 1, 1))
        d.update(kw)
        return Discount.objects.create(**d)


class RunTests(BillingBase):
    def test_billing_run_twice_creates_zero_new_invoices(self):
        sub = self.active_sub()
        first = billing.run_billing(JAN31)
        second = billing.run_billing(JAN31)
        self.assertEqual((first.invoices_created, second.invoices_created), (1, 0))
        self.assertEqual(Invoice.objects.count(), 1)
        sub.refresh_from_db()
        self.assertEqual(sub.next_invoice_date, FEB28)

    def test_schedule_reset_still_cannot_duplicate(self):
        sub = self.active_sub()
        billing.run_billing(JAN31)
        Subscription.objects.filter(pk=sub.pk).update(next_invoice_date=JAN31)   # simulate a crash mid-run
        self.assertEqual(billing.run_billing(JAN31).invoices_created, 0)
        self.assertEqual(Invoice.objects.count(), 1)

    def test_catch_up_with_month_end_anchor(self):
        sub = self.active_sub()
        s = billing.run_billing(MAR31)
        self.assertEqual(s.invoices_created, 3)
        invs = list(Invoice.objects.order_by("period_start"))
        self.assertEqual([i.period_start for i in invs], [JAN31, FEB28, MAR31])
        self.assertEqual([i.period_end for i in invs], [FEB28, MAR31, APR30])
        self.assertEqual(invs[0].due_date, date(2026, 2, 15))          # net 15
        self.assertEqual(invs[0].number, f"INV-{invs[0].pk:06d}")
        sub.refresh_from_db()
        self.assertEqual(sub.next_invoice_date, APR30)

    def test_tax_after_discount_on_real_invoice(self):
        d = self.sub_discount()
        self.active_sub(discount=d)
        billing.run_billing(JAN31)
        inv = Invoice.objects.get()
        self.assertEqual((inv.subtotal, inv.discount_total, inv.tax_total, inv.total),
                         (112000, 11200, 18144, 118944))
        self.assertEqual(inv.total, sum(l.line_total for l in inv.lines.all()))
        self.assertEqual(inv.discount_applied, d)
        self.assertEqual(inv.calc_trace["total"], inv.total)
        d.refresh_from_db()
        self.assertEqual(d.used_count, 1)

    def test_discount_at_usage_limit_stops_applying(self):
        d = self.sub_discount(usage_limit=1)
        self.active_sub(self.a, discount=d)
        self.active_sub(self.b, discount=d)
        billing.run_billing(JAN31)
        totals = sorted(Invoice.objects.values_list("discount_total", flat=True))
        self.assertEqual(totals, [0, 11200])
        d.refresh_from_db()
        self.assertEqual(d.used_count, 1)

    def test_paused_and_expired_are_not_billed(self):
        self.active_sub(self.a, is_paused=True)
        exp = self.active_sub(self.b, expiry_date=date(2026, 2, 15))
        billing.run_billing(MAR31)
        self.assertEqual(list(Invoice.objects.values_list("subscription_id", "period_start")), [(exp.pk, JAN31)])

    def test_auto_close_at_expiry(self):
        plan = Plan.objects.create(name="Fixed term", price=0, billing_period="monthly",
                                   available_from=date(2026, 1, 1), auto_close=True)
        sub = self.active_sub(plan=plan, expiry_date=date(2026, 2, 15))
        s = billing.run_billing(MAR31)
        sub.refresh_from_db()
        self.assertEqual((sub.status, s.invoices_created, len(s.auto_closed)), ("closed", 1, 1))
        self.assertTrue(sub.events.filter(note="Auto-closed at expiry").exists())

    def test_one_failing_subscription_does_not_stop_the_run(self):
        bad, good = self.active_sub(self.a), self.active_sub(self.b)
        real = billing._create_invoice

        def flaky(sub, start, actor=None):
            if sub.pk == bad.pk:
                raise RuntimeError("boom")
            return real(sub, start, actor)

        with mock.patch("core.billing._create_invoice", side_effect=flaky):
            s = billing.run_billing(JAN31)
        self.assertEqual((s.invoices_created, len(s.errors)), (1, 1))
        self.assertTrue(Invoice.objects.filter(subscription=good).exists())
        bad.refresh_from_db()
        self.assertEqual(bad.next_invoice_date, JAN31)                  # rolled back, retried next run

    def test_preview_writes_nothing(self):
        self.active_sub()
        p = billing.preview(MAR31)
        self.assertEqual((p["due_count"], p["rows"][0]["periods"], p["rows"][0]["first_total"]), (1, 3, 132160))
        self.assertEqual(Invoice.objects.count(), 0)

    def test_management_command_dry_run_then_real(self):
        self.active_sub()
        out = StringIO()
        call_command("run_billing", "--as-of", "2026-01-31", "--dry-run", stdout=out)
        self.assertIn("DRY RUN", out.getvalue())
        self.assertEqual(Invoice.objects.count(), 0)
        call_command("run_billing", "--as-of", "2026-01-31", stdout=out)
        self.assertEqual(Invoice.objects.count(), 1)


class InvoiceActionTests(BillingBase):
    def setUp(self):
        super().setUp()
        self.d = self.sub_discount()
        self.sub = self.active_sub(discount=self.d)
        billing.run_billing(JAN31)
        self.inv = Invoice.objects.get()

    def post(self, action, user=None):
        self.client.force_login(user or self.staff)
        return self.client.post(reverse("invoice_action", args=[self.inv.pk, action]))

    def test_cancel_releases_discount_use(self):
        billing.cancel_invoice(self.inv, self.staff)
        self.d.refresh_from_db()
        self.assertEqual((self.inv.status, self.d.used_count), ("cancelled", 0))
        with self.assertRaises(InvalidTransition):
            billing.cancel_invoice(self.inv, self.staff)
        self.d.refresh_from_db()
        self.assertEqual(self.d.used_count, 0)                          # never goes negative

    def test_cancel_refused_once_paid_something(self):
        billing.confirm_invoice(self.inv, self.staff)
        Payment.objects.create(invoice=self.inv, method="upi", amount=100, paid_on=JAN31)
        with self.assertRaises(billing.BillingError):
            billing.cancel_invoice(self.inv, self.staff)
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, "confirmed")

    def test_confirm_then_email_lands_in_outbox(self):
        self.post("send")                                               # draft: refused
        self.assertEqual(len(mail.outbox), 0)
        self.post("confirm")
        self.post("send")
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, "confirmed")
        self.assertEqual(mail.outbox[0].to, ["a@brightloop.test"])
        self.assertIn("₹1,189.44", mail.outbox[0].body)
        self.assertIsNotNone(self.inv.sent_at)

    def test_customer_cannot_act_and_cannot_see_others(self):
        self.assertEqual(self.post("confirm", user=self.a).status_code, 403)
        self.client.force_login(self.b)
        self.assertEqual(self.client.get(reverse("invoice_print", args=[self.inv.pk])).status_code, 404)
        self.client.force_login(self.a)
        r = self.client.get(reverse("invoice_print", args=[self.inv.pk]))
        self.assertContains(r, self.inv.number)

    def test_detail_shows_breakdown_overdue_and_trace(self):
        billing.confirm_invoice(self.inv, self.staff)                  # due 15 Feb 2026: past
        self.client.force_login(self.staff)
        r = self.client.get(reverse("invoice_detail", args=[self.inv.pk]))
        self.assertContains(r, "Overdue")
        self.assertContains(r, "₹1,189.44")
        self.assertContains(r, "How this invoice was calculated")
        self.client.force_login(self.a)
        r = self.client.get(reverse("invoice_detail", args=[self.inv.pk]))
        self.assertNotContains(r, "How this invoice was calculated")   # trace is internal only

    def test_billing_page_access_and_run(self):
        self.client.force_login(self.a)
        self.assertEqual(self.client.get(reverse("billing_home")).status_code, 403)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("billing_home"), {"as_of": "2026-03-31"}).status_code, 200)
        self.client.post(reverse("billing_home"), {"as_of": "2026-03-31"})
        self.assertEqual(Invoice.objects.count(), 3)
