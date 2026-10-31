"""Auth + RBAC acceptance tests (the 11:40 'done when' list).

Run:  docker compose run --rm web python manage.py test core
"""
import re
from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import mail
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from core.models import Customer, Invoice, Plan, Subscription
from core.roles import scoped_invoices, scoped_subscriptions

User = get_user_model()
GOOD_PW = "Zx9!kLm2q"   # 9 chars: upper, lower, special


def signup_data(email="new@brightloop.test", pw=GOOD_PW, **extra):
    return {"name": "Asha", "company": "Acme", "email": email,
            "password1": pw, "password2": pw, **extra}


def make_user(email, role, with_customer=False):
    u = User.objects.create_user(username=email, email=email, password=GOOD_PW)
    u.groups.add(Group.objects.get_or_create(name=role)[0])
    if with_customer:
        Customer.objects.create(user=u, name=email.split("@")[0])
    return u


class PasswordRuleTests(TestCase):
    def post(self, pw):
        return self.client.post(reverse("signup"), signup_data(pw=pw))

    def test_8_chars_rejected_9_accepted(self):
        r = self.post("Zx9!kLm2")                       # 8 chars, all classes present
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.exists())
        r = self.post("Zx9!kLm2q")                      # 9 chars
        self.assertRedirects(r, reverse("home"))
        self.assertTrue(User.objects.filter(email="new@brightloop.test").exists())

    def test_each_character_class_required(self):
        for pw in ["zx9!klm2q", "ZX9!KLM2Q", "Zx9kLm2qw"]:  # no upper / no lower / no special
            with self.subTest(pw=pw):
                r = self.post(pw)
                self.assertEqual(r.status_code, 200)
                self.assertTrue(r.context["form"].errors.get("password1"))
        self.assertFalse(User.objects.exists())

    def test_signup_creates_customer_role_and_portal_record(self):
        self.post(GOOD_PW)
        u = User.objects.get()
        self.assertEqual(u.role, "customer")
        self.assertEqual(u.customer.company, "Acme")


class UniqueEmailTests(TestCase):
    def test_case_variant_duplicate_rejected_by_form(self):
        self.client.post(reverse("signup"), signup_data(email="dup@brightloop.test"))
        self.client.logout()
        r = self.client.post(reverse("signup"), signup_data(email="DUP@BrightLoop.Test"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("email", r.context["form"].errors)
        self.assertEqual(User.objects.count(), 1)

    def test_case_variant_duplicate_rejected_by_database(self):
        User.objects.create_user(username="a1", email="x@brightloop.test")
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create_user(username="a2", email="X@Brightloop.TEST")

    def test_login_with_any_email_case(self):
        make_user("case@brightloop.test", "customer")
        ok = self.client.login(username="CASE@Brightloop.test", password=GOOD_PW)
        self.assertTrue(ok)


class PasswordResetTests(TestCase):
    def test_reset_email_sent_and_link_sets_new_password(self):
        make_user("reset@brightloop.test", "customer")
        r = self.client.post(reverse("password_reset"), {"email": "Reset@Brightloop.test"})
        self.assertRedirects(r, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["reset@brightloop.test"])
        link = re.search(r"https?://[^/\s]+(/reset/\S+/)", mail.outbox[0].body).group(1)

        r = self.client.get(link, follow=True)
        set_url = r.redirect_chain[-1][0]
        weak = self.client.post(set_url, {"new_password1": "weakpass1", "new_password2": "weakpass1"})
        self.assertEqual(weak.status_code, 200)            # rule enforced on reset too
        r = self.client.post(set_url, {"new_password1": "New!Pass99", "new_password2": "New!Pass99"})
        self.assertRedirects(r, reverse("password_reset_complete"))
        self.assertTrue(self.client.login(username="reset@brightloop.test", password="New!Pass99"))

    def test_unknown_email_sends_nothing_but_looks_the_same(self):
        r = self.client.post(reverse("password_reset"), {"email": "ghost@brightloop.test"})
        self.assertRedirects(r, reverse("password_reset_done"))   # no account enumeration
        self.assertEqual(len(mail.outbox), 0)


class StaffCreationTests(TestCase):
    def setUp(self):
        self.url = reverse("staff_create")
        self.data = {"name": "Ravi", "email": "ravi@brightloop.test",
                     "password1": GOOD_PW, "password2": GOOD_PW}

    def test_admin_can_create_staff(self):
        self.client.force_login(make_user("boss@brightloop.test", "admin"))
        r = self.client.post(self.url, self.data)
        self.assertRedirects(r, reverse("staff_list"))
        self.assertEqual(User.objects.get(email="ravi@brightloop.test").role, "staff")

    def test_staff_and_customer_get_403(self):
        for role in ["staff", "customer"]:
            with self.subTest(role=role):
                self.client.force_login(make_user(f"{role}@brightloop.test", role))
                self.assertEqual(self.client.get(self.url).status_code, 403)
                self.assertEqual(self.client.post(self.url, self.data).status_code, 403)
        self.assertFalse(User.objects.filter(email="ravi@brightloop.test").exists())

    def test_anonymous_redirected_to_login(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("login"), r["Location"])


class CustomerScopingTests(TestCase):
    def setUp(self):
        self.a = make_user("a@brightloop.test", "customer", with_customer=True)
        self.b = make_user("b@brightloop.test", "customer", with_customer=True)
        plan = Plan.objects.create(name="Pro", price=99900, billing_period="monthly",
                                   available_from=date(2026, 1, 1))
        self.inv = {}
        for i, u in enumerate([self.a, self.b], start=1):
            sub = Subscription.objects.create(number=f"SUB-{i:06d}", customer=u.customer,
                                              plan=plan, start_date=date(2026, 10, 1))
            self.inv[u.email] = Invoice.objects.create(
                number=f"INV-{i:06d}", subscription=sub, customer=u.customer,
                period_start=date(2026, 10, 1), period_end=date(2026, 11, 1),
                issue_date=date(2026, 10, 1), due_date=date(2026, 10, 1), total=99900)

    def test_customer_a_gets_404_on_b_invoice(self):
        self.client.force_login(self.a)
        own = self.client.get(reverse("invoice_detail", args=[self.inv["a@brightloop.test"].pk]))
        other = self.client.get(reverse("invoice_detail", args=[self.inv["b@brightloop.test"].pk]))
        self.assertEqual(own.status_code, 200)
        self.assertEqual(other.status_code, 404)

    def test_list_shows_only_own_rows(self):
        self.client.force_login(self.a)
        r = self.client.get(reverse("invoice_list"))
        self.assertContains(r, "INV-000001")
        self.assertNotContains(r, "INV-000002")

    def test_scoping_helpers(self):
        self.assertEqual(scoped_invoices(self.a).count(), 1)
        self.assertEqual(scoped_subscriptions(self.b).count(), 1)
        staff = make_user("s@brightloop.test", "staff")
        self.assertEqual(scoped_invoices(staff).count(), 2)
        nobody = make_user("n@brightloop.test", "customer")      # no Customer row
        self.assertEqual(scoped_invoices(nobody).count(), 0)
