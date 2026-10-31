"""Payments: partial payments, paid transition, overdue, access.

Run:  docker compose run --rm web python manage.py test core -v 2
"""
from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse

from core import billing, clock, payments
from core.models import Discount, Invoice, Payment
from core.tests.test_billing import JAN31, BillingBase


class PaymentBase(BillingBase):
    def setUp(self):
        super().setUp()
        self.active_sub()
        billing.run_billing(JAN31)
        self.inv = Invoice.objects.get()                  # 2 x 560 + 18% = 132160
        billing.confirm_invoice(self.inv, self.staff)
        self.inv.refresh_from_db()

    def pay(self, amount, method="upi"):
        return payments.record_payment(self.inv, amount=amount, method=method,
                                       paid_on=JAN31, actor=self.staff)


class PaymentRuleTests(PaymentBase):
    def test_partial_payment_leaves_invoice_outstanding(self):
        self.pay(50000)
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, "confirmed")
        self.assertEqual(billing.amount_paid(self.inv), 50000)
        self.assertEqual(self.inv.total - billing.amount_paid(self.inv), 82160)
        self.assertTrue(self.inv.events.filter(note__contains="Partial payment").exists())

    def test_payments_summing_to_total_mark_paid(self):
        self.pay(50000)
        self.pay(82160, method="cash")
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, "paid")
        self.assertEqual(self.inv.payments.count(), 2)

    def test_overpayment_and_paid_invoice_refused(self):
        with self.assertRaises(billing.BillingError):
            self.pay(self.inv.total + 1)
        self.assertEqual(Payment.objects.count(), 0)
        self.pay(self.inv.total)
        with self.assertRaises(billing.BillingError):
            self.pay(1)

    def test_zero_amount_refused(self):
        with self.assertRaises(billing.BillingError):
            self.pay(0)

    def test_draft_and_cancelled_invoices_take_no_payments(self):
        self.client.force_login(self.staff)
        self.active_sub(self.b)
        billing.run_billing(JAN31)
        draft = Invoice.objects.exclude(pk=self.inv.pk).get()
        with self.assertRaises(billing.BillingError):
            payments.record_payment(draft, amount=100, method="upi", paid_on=JAN31)
        billing.cancel_invoice(draft, self.staff)
        with self.assertRaises(billing.BillingError):
            payments.record_payment(draft, amount=100, method="upi", paid_on=JAN31)

    def test_cannot_cancel_after_partial_payment(self):
        self.pay(100)
        with self.assertRaises(billing.BillingError):
            billing.cancel_invoice(self.inv, self.staff)

    def test_zero_total_invoice_is_paid_on_confirmation(self):
        d = Discount.objects.create(name="Free", kind="percent", value=10000, scope="subscription",
                                    starts_on=date(2026, 1, 1))
        sub = self.active_sub(self.b, discount=d)
        billing.run_billing(JAN31)
        free = Invoice.objects.get(subscription=sub)
        self.assertEqual(free.total, 0)
        billing.confirm_invoice(free, self.staff)
        free.refresh_from_db()
        self.assertEqual(free.status, "paid")


class PaymentViewTests(PaymentBase):
    def url(self, name):
        return reverse(name, args=[self.inv.pk])

    def test_staff_records_rupees_converted_to_paise(self):
        self.client.force_login(self.staff)
        self.client.post(self.url("invoice_payment"),
                         {"amount": "1321.60", "method": "bank", "paid_on": "2026-01-31", "note": "NEFT"})
        self.inv.refresh_from_db()
        self.assertEqual((self.inv.status, self.inv.payments.get().amount), ("paid", 132160))

    def test_bad_amounts_and_future_dates_rejected(self):
        self.client.force_login(self.staff)
        future = (clock.today() + timedelta(days=3)).isoformat()
        for data in ({"amount": "0", "method": "upi", "paid_on": "2026-01-31"},
                     {"amount": "10.005", "method": "upi", "paid_on": "2026-01-31"},
                     {"amount": "10", "method": "upi", "paid_on": future},
                     {"amount": "99999", "method": "upi", "paid_on": "2026-01-31"}):
            with self.subTest(data=data):
                self.client.post(self.url("invoice_payment"), data)
        self.assertEqual(Payment.objects.count(), 0)

    def test_customer_cannot_record_but_sees_payments_and_balance(self):
        self.pay(50000)
        self.client.force_login(self.a)
        r = self.client.post(self.url("invoice_payment"),
                             {"amount": "10", "method": "upi", "paid_on": "2026-01-31"})
        self.assertEqual(r.status_code, 403)
        r = self.client.get(self.url("invoice_detail"))
        self.assertContains(r, "₹500.00")                 # the payment
        self.assertContains(r, "₹821.60")                 # balance due
        self.assertNotContains(r, "Record payment")

    def test_other_customer_gets_404(self):
        self.client.force_login(self.b)
        self.assertEqual(self.client.get(self.url("invoice_detail")).status_code, 404)

    def test_overdue_is_derived_and_filterable(self):
        self.client.force_login(self.staff)
        r = self.client.get(reverse("invoice_list"), {"status": "overdue"})
        self.assertContains(r, self.inv.number)           # due Feb 2026, clearly past
        self.pay(self.inv.total)
        r = self.client.get(reverse("invoice_list"), {"status": "overdue"})
        self.assertNotContains(r, self.inv.number)        # paid -> no longer overdue
        r = self.client.get(reverse("invoice_list"), {"status": "paid", "page": 99})
        self.assertContains(r, self.inv.number)

    def test_list_is_scoped_to_customer(self):
        self.client.force_login(self.b)
        self.assertNotContains(self.client.get(reverse("invoice_list")), self.inv.number)
        self.client.force_login(self.a)
        self.assertContains(self.client.get(reverse("invoice_list")), self.inv.number)
