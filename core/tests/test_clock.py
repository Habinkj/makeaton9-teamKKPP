"""Simulated clock: offsets, admin-only control, and that the whole app follows it.

Run:  docker compose run --rm web python manage.py test core -v 2
"""
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core import billing, clock
from core.models import Invoice
from core.tests.test_auth import make_user
from core.tests.test_billing import BillingBase


class ClockUnitTests(TestCase):
    def test_default_is_real_date(self):
        self.assertEqual(clock.today(), clock.real_today())
        self.assertEqual(clock.offset_days(), 0)

    def test_advance_set_and_reset(self):
        clock.advance(30)
        clock.advance(5)
        self.assertEqual(clock.today(), clock.real_today() + timedelta(days=35))
        clock.set_date(clock.real_today() + timedelta(days=100))
        self.assertEqual(clock.offset_days(), 100)
        clock.reset()
        self.assertEqual(clock.today(), clock.real_today())

    def test_only_forward_and_bounded(self):
        for bad in (lambda: clock.advance(0), lambda: clock.advance(-3),
                    lambda: clock.set_date(clock.real_today() - timedelta(days=1)),
                    lambda: clock.set_offset(clock.MAX_OFFSET_DAYS + 1)):
            with self.assertRaises(ValueError):
                bad()
        self.assertEqual(clock.offset_days(), 0)

    def test_model_and_migration_in_sync(self):
        call_command("makemigrations", "core", "--check", "--dry-run", stdout=StringIO())


class ClockViewTests(TestCase):
    def setUp(self):
        self.admin = make_user("boss@brightloop.test", "admin")
        self.staff = make_user("staff@brightloop.test", "staff")
        self.url = reverse("clock_control")

    def test_admin_advances_and_resets(self):
        self.client.force_login(self.admin)
        r = self.client.post(self.url, {"action": "advance", "days": "30"})
        self.assertRedirects(r, reverse("dashboard"))
        self.assertEqual(clock.offset_days(), 30)
        r = self.client.get(reverse("dashboard"))
        self.assertContains(r, "Simulated date")
        self.client.post(self.url, {"action": "set", "date": (clock.real_today() + timedelta(days=7)).isoformat()})
        self.assertEqual(clock.offset_days(), 7)
        self.client.post(self.url, {"action": "reset"})
        self.assertEqual(clock.offset_days(), 0)
        self.assertNotContains(self.client.get(reverse("dashboard")), "Simulated date:")

    def test_bad_input_shows_error_not_500(self):
        self.client.force_login(self.admin)
        for data in ({"action": "advance", "days": "-5"}, {"action": "advance", "days": "abc"},
                     {"action": "set", "date": "not-a-date"}, {"action": "explode"}):
            with self.subTest(data=data):
                self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.assertEqual(clock.offset_days(), 0)

    def test_staff_and_customer_cannot_change_date(self):
        cust = make_user("c@brightloop.test", "customer", with_customer=True)
        for user in (self.staff, cust):
            self.client.force_login(user)
            self.assertEqual(self.client.post(self.url, {"action": "advance", "days": "30"}).status_code, 403)
        self.assertEqual(clock.offset_days(), 0)

    def test_next_cannot_redirect_off_site(self):
        self.client.force_login(self.admin)
        r = self.client.post(self.url, {"action": "reset", "next": "https://evil.example/"})
        self.assertRedirects(r, reverse("dashboard"))


class AppFollowsClockTests(BillingBase):
    def test_advancing_makes_invoice_overdue_and_bills_next_period(self):
        today = clock.real_today()
        sub = self.active_sub(start=today)                      # net 15
        billing.run_billing(clock.today())
        inv = Invoice.objects.get()
        billing.confirm_invoice(inv, self.staff)
        self.client.force_login(self.staff)
        k = self.client.get(reverse("dashboard")).context["kpis"]
        self.assertEqual(k["overdue_count"], 0)                 # due in 15 days

        clock.advance(40)
        k = self.client.get(reverse("dashboard")).context["kpis"]
        self.assertEqual(k["overdue_count"], 1)                 # now 25 days late
        r = self.client.get(reverse("invoice_detail", args=[inv.pk]))
        self.assertContains(r, "Overdue")
        r = self.client.get(reverse("billing_home"))            # "as of" defaults to simulated date
        self.assertEqual(r.context["as_of"], today + timedelta(days=40))
        self.assertGreaterEqual(r.context["p"]["due_count"], 1)
        self.assertGreaterEqual(billing.run_billing(clock.today()).invoices_created, 1)
        sub.refresh_from_db()
        self.assertGreater(sub.next_invoice_date, clock.today())
