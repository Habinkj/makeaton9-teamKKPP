"""The seed command must produce data that satisfies every invariant, and billing must
agree with it (nothing is due on the seed date, plenty is due 40 days later)."""
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from core import billing, clock, invariants
from core.models import Customer, Discount, Invoice, Payment, Subscription


class SeedTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", "--subs", "150", stdout=StringIO())

    def test_row_counts_and_numbering(self):
        self.assertEqual(Subscription.objects.count(), 150)
        self.assertGreater(Invoice.objects.count(), 150)
        self.assertGreater(Payment.objects.count(), 100)
        self.assertEqual(Subscription.objects.exclude(number__regex=r"^SUB-\d{6}$").count(), 0)
        self.assertEqual(Invoice.objects.exclude(number__regex=r"^INV-\d{6}$").count(), 0)
        s = Subscription.objects.first()
        self.assertEqual(s.number, f"SUB-{s.pk:06d}")

    def test_every_invariant_holds(self):
        for name, violations in invariants.check_all():
            with self.subTest(check=name):
                self.assertEqual(violations, 0)

    def test_mix_of_statuses_and_overdue_exist(self):
        statuses = set(Invoice.objects.values_list("status", flat=True))
        self.assertTrue({"paid", "confirmed", "draft"} <= statuses)
        self.assertGreater(Invoice.objects.filter(status="confirmed", due_date__lt=clock.today()).count(), 0)

    def test_billing_agrees_with_seeded_schedule(self):
        today = clock.today()
        self.assertEqual(billing.run_billing(today).invoices_created, 0)       # nothing due today
        later = billing.run_billing(today + timedelta(days=40))
        self.assertGreater(later.invoices_created, 0)                         # advancing time bills
        self.assertEqual(billing.run_billing(today + timedelta(days=40)).invoices_created, 0)
        for name, violations in invariants.check_all():
            with self.subTest(check=name):
                self.assertEqual(violations, 0)

    def test_discount_usage_counted_and_reset_cleans_up(self):
        self.assertGreater(Discount.objects.get(name="Demo Loyalty 10%").used_count, 0)
        call_command("seed_demo", "--reset", stdout=StringIO())
        self.assertEqual((Subscription.objects.count(), Invoice.objects.count(),
                          Payment.objects.count(), Customer.objects.count()), (0, 0, 0, 0))
        self.assertEqual(Discount.objects.get(name="Demo Loyalty 10%").used_count, 0)
