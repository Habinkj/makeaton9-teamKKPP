from datetime import date

from django.test import TestCase
from django.urls import reverse

from core.invariants import CHECKS
from core.models import Customer, Invoice, Plan, Subscription
from core.tests.test_auth import GOOD_PW, make_user


class HealthCheckTests(TestCase):
    def setUp(self):
        self.staff_user = make_user("staff@brightloop.test", "staff")
        self.customer_user = make_user("cust@brightloop.test", "customer", with_customer=True)
        self.url = reverse("health")

    def test_anonymous_redirected_to_login(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login/", r.url)

    def test_customer_gets_403(self):
        self.client.login(username=self.customer_user.email, password=GOOD_PW)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 403)

    def test_staff_gets_200_and_sees_all_checks(self):
        self.client.login(username=self.staff_user.email, password=GOOD_PW)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertTemplateUsed(r, "health.html")

        # Check all 5 invariant check names are rendered
        for name, _ in CHECKS:
            self.assertContains(r, name)

        # Status badge OK is rendered
        self.assertContains(r, "OK")

    def test_broken_invoice_shows_failure(self):
        cust = Customer.objects.get(user=self.customer_user)
        plan = Plan.objects.create(
            name="Pro Plan",
            billing_period=Plan.Period.MONTHLY,
            price=10000,
            available_from=date(2026, 1, 1),
        )
        sub = Subscription.objects.create(
            number="SUB-999",
            customer=cust,
            plan=plan,
            status=Subscription.Status.ACTIVE,
            start_date=date(2026, 1, 1),
        )
        inv = Invoice.objects.create(
            number="INV-999",
            customer=cust,
            subscription=sub,
            status=Invoice.Status.DRAFT,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 2, 1),
            issue_date=date(2026, 1, 1),
            due_date=date(2026, 1, 1),
            subtotal=10000,
            discount_total=0,
            tax_total=1800,
            total=11800,
        )

        # Deliberately break the invoice total
        Invoice.objects.filter(pk=inv.pk).update(total=99999)

        self.client.login(username=self.staff_user.email, password=GOOD_PW)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "failed")
