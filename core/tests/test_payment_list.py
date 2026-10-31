"""Payments list page: scoping, filters, pagination, totals.

Run:  docker compose run --rm web python manage.py test core.tests.test_payment_list -v 2
"""
from datetime import date

from django.test import TestCase
from django.urls import reverse

from core import billing, payments
from core.models import Invoice, Payment
from core.tests.test_auth import GOOD_PW, make_user
from core.tests.test_billing import JAN31, FEB28, BillingBase


class PaymentListBase(BillingBase):
    """Set up two customers each with a confirmed invoice and payments."""

    def setUp(self):
        super().setUp()
        # Customer A: active sub, billed, confirmed, one UPI payment
        self.sub_a = self.active_sub(self.a)
        billing.run_billing(JAN31)
        self.inv_a = Invoice.objects.get(subscription=self.sub_a)
        billing.confirm_invoice(self.inv_a, self.staff)
        self.inv_a.refresh_from_db()
        self.pay_a = payments.record_payment(
            self.inv_a, amount=50000, method="upi", paid_on=JAN31, actor=self.staff,
        )

        # Customer B: active sub, billed, confirmed, one card payment
        self.sub_b = self.active_sub(self.b)
        billing.run_billing(JAN31)
        self.inv_b = Invoice.objects.filter(subscription=self.sub_b).get()
        billing.confirm_invoice(self.inv_b, self.staff)
        self.inv_b.refresh_from_db()
        self.pay_b = payments.record_payment(
            self.inv_b, amount=30000, method="card", paid_on=JAN31, actor=self.staff,
        )

        self.url = reverse("payment_list")


class ScopingTests(PaymentListBase):
    def test_staff_sees_all_payments(self):
        self.client.login(username=self.staff.email, password=GOOD_PW)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, self.inv_a.number)
        self.assertContains(r, self.inv_b.number)
        self.assertEqual(r.context["count"], 2)

    def test_customer_a_sees_only_own(self):
        self.client.login(username=self.a.email, password=GOOD_PW)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, self.inv_a.number)
        self.assertNotContains(r, self.inv_b.number)
        self.assertEqual(r.context["count"], 1)

    def test_anonymous_redirects_to_login(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login/", r.url)


class FilterTests(PaymentListBase):
    def test_method_filter(self):
        self.client.login(username=self.staff.email, password=GOOD_PW)
        r = self.client.get(self.url, {"method": "upi"})
        self.assertEqual(r.context["count"], 1)
        self.assertContains(r, self.inv_a.number)
        self.assertNotContains(r, self.inv_b.number)

    def test_date_range_filter(self):
        self.client.login(username=self.staff.email, password=GOOD_PW)
        # Both payments are on JAN31; a range excluding that day returns nothing
        r = self.client.get(self.url, {"from": "2026-02-01", "to": "2026-02-28"})
        self.assertEqual(r.context["count"], 0)
        # A range including JAN31 returns both
        r = self.client.get(self.url, {"from": "2026-01-01", "to": "2026-01-31"})
        self.assertEqual(r.context["count"], 2)

    def test_search_by_invoice_number(self):
        self.client.login(username=self.staff.email, password=GOOD_PW)
        r = self.client.get(self.url, {"q": self.inv_a.number})
        self.assertEqual(r.context["count"], 1)
        self.assertContains(r, self.inv_a.number)

    def test_invalid_date_does_not_crash(self):
        self.client.login(username=self.staff.email, password=GOOD_PW)
        r = self.client.get(self.url, {"from": "not-a-date", "to": "also-bad"})
        self.assertEqual(r.status_code, 200)


class PaginationAndTotalTests(PaymentListBase):
    def test_page_2_works_and_total_covers_all(self):
        self.client.login(username=self.staff.email, password=GOOD_PW)
        # Create 24 more ₹1 payments against inv_a to get 26 total (2 existing + 24 new)
        for _ in range(24):
            Payment.objects.create(
                invoice=self.inv_a, method="cash", amount=1, paid_on=JAN31,
            )
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["count"], 26)
        # total should be 50000 + 30000 + 24*1 = 80024
        self.assertEqual(r.context["total"], 80024)
        # Page 1 has 25 items
        self.assertEqual(len(r.context["page"]), 25)

        # Page 2
        r2 = self.client.get(self.url, {"page": "2"})
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(len(r2.context["page"]), 1)
        # total on page 2 should still cover all filtered payments
        self.assertEqual(r2.context["total"], 80024)
