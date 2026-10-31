"""Quote -> Subscription: snapshots, numbering, transitions, plan flags, scoping.

Run:  docker compose run --rm web python manage.py test core -v 2
"""
from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse

from core import clock
from core import subscriptions as svc
from core.models import (Event, Plan, Product, ProductVariant, QuotationTemplate,
                         QuotationTemplateLine, Subscription, TaxRule)
from core.statemachine import InvalidTransition
from core.tests.test_auth import make_user

START = date(2026, 11, 1)


class Base(TestCase):
    def setUp(self):
        self.admin = make_user("boss@brightloop.test", "admin")
        self.staff = make_user("staff@brightloop.test", "staff")
        self.a = make_user("a@brightloop.test", "customer", with_customer=True)
        self.b = make_user("b@brightloop.test", "customer", with_customer=True)
        gst = TaxRule.objects.create(name="GST 18%", rate_bps=1800)
        self.product = Product.objects.create(name="CRM Seat", sales_price=56000)
        self.product.tax_rules.add(gst)
        self.variant = ProductVariant.objects.create(
            product=self.product, attribute="Brand", value="Odoo", extra_price=10000)
        self.plan = Plan.objects.create(name="Pro", price=0, billing_period="monthly",
                                        min_quantity=2, available_from=date(2026, 1, 1))
        self.tpl = QuotationTemplate.objects.create(name="Pro x2", plan=self.plan, validity_days=15)
        QuotationTemplateLine.objects.create(template=self.tpl, product=self.product, quantity=2)

    def make_sub(self, customer_user=None, template=True, **kw):
        return svc.create_subscription(
            customer=(customer_user or self.a).customer, plan=self.plan, start_date=START,
            payment_terms="net15", actor=self.staff, template=self.tpl if template else None, **kw)

    def move(self, sub, *statuses, user=None):
        for s in statuses:
            svc.do_transition(sub, s, user or self.staff)
        sub.refresh_from_db()
        return sub


class CreationTests(Base):
    def test_create_from_template_snapshots_lines_and_numbers(self):
        self.client.force_login(self.staff)
        r = self.client.post(reverse("subscription_create"), {
            "customer": self.a.customer.pk, "plan": self.plan.pk, "template": self.tpl.pk,
            "start_date": "2026-11-01", "payment_terms": "net15"})
        sub = Subscription.objects.get()
        self.assertRedirects(r, reverse("subscription_detail", args=[sub.pk]))
        self.assertEqual(sub.number, f"SUB-{sub.pk:06d}")
        self.assertEqual(sub.status, "draft")
        self.assertEqual(sub.quotation_valid_until, clock.today() + timedelta(days=15))
        line = sub.lines.get()
        self.assertEqual((line.quantity, line.unit_price, line.tax_rate_bps), (2, 56000, 1800))
        self.assertTrue(Event.objects.filter(subscription=sub, to_status="draft").exists())

    def test_snapshot_not_live_reference(self):
        sub = self.make_sub()
        self.product.sales_price = 99999
        self.product.save()
        self.tpl.lines.update(quantity=50)
        line = sub.lines.get()
        self.assertEqual((line.unit_price, line.quantity), (56000, 2))

    def test_variant_extra_included_in_unit_price(self):
        sub = self.make_sub(template=False)
        line = svc.add_line(sub, self.product, self.variant, 1)
        self.assertEqual(line.unit_price, 66000)

    def test_plan_availability_enforced(self):
        self.client.force_login(self.staff)
        r = self.client.post(reverse("subscription_create"), {
            "customer": self.a.customer.pk, "plan": self.plan.pk, "template": "",
            "start_date": "2025-12-31", "payment_terms": "immediate"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("start_date", r.context["form"].errors)
        self.assertFalse(Subscription.objects.exists())

    def test_template_must_match_plan(self):
        other = Plan.objects.create(name="Basic", price=0, billing_period="monthly",
                                    available_from=date(2026, 1, 1))
        self.client.force_login(self.staff)
        r = self.client.post(reverse("subscription_create"), {
            "customer": self.a.customer.pk, "plan": other.pk, "template": self.tpl.pk,
            "start_date": "2026-11-01", "payment_terms": "immediate"})
        self.assertIn("template", r.context["form"].errors)


class TransitionTests(Base):
    def test_full_lifecycle_sets_billing_schedule(self):
        sub = self.move(self.make_sub(), "quotation", "confirmed", "active")
        self.assertEqual(sub.status, "active")
        self.assertEqual((sub.next_invoice_date, sub.anchor_day), (START, 1))
        self.assertEqual(sub.events.count(), 4)       # created + 3 moves
        sub = self.move(sub, "closed")
        self.assertIsNone(sub.next_invoice_date)

    def test_skipping_states_is_refused_by_table(self):
        sub = self.make_sub()
        with self.assertRaises(InvalidTransition):
            svc.do_transition(sub, "active", self.staff)
        sub.refresh_from_db()
        self.assertEqual(sub.status, "draft")

    def test_skipping_states_via_http_shows_error_not_500(self):
        sub = self.make_sub()
        self.client.force_login(self.staff)
        r = self.client.post(reverse("subscription_transition", args=[sub.pk]), {"to": "active"}, follow=True)
        self.assertEqual(r.status_code, 200)
        sub.refresh_from_db()
        self.assertEqual(sub.status, "draft")

    def test_min_quantity_enforced(self):
        sub = self.make_sub(template=False)
        svc.add_line(sub, self.product, None, 1)
        with self.assertRaises(svc.RuleViolation):
            svc.do_transition(sub, "quotation", self.staff)
        svc.add_line(sub, self.product, None, 1)        # merges into the same line -> qty 2
        self.assertEqual(sub.lines.count(), 1)
        self.move(sub, "quotation")

    def test_empty_subscription_cannot_be_quoted(self):
        with self.assertRaises(svc.RuleViolation):
            svc.do_transition(self.make_sub(template=False), "quotation", self.staff)

    def test_expired_quotation_cannot_be_confirmed(self):
        sub = self.move(self.make_sub(), "quotation")
        Subscription.objects.filter(pk=sub.pk).update(
            quotation_valid_until=clock.today() - timedelta(days=1))
        sub.refresh_from_db()
        with self.assertRaises(svc.RuleViolation):
            svc.do_transition(sub, "confirmed", self.staff)

    def test_lines_frozen_after_draft(self):
        sub = self.move(self.make_sub(), "quotation")
        with self.assertRaises(svc.RuleViolation):
            svc.add_line(sub, self.product, None, 5)


class PlanFlagTests(Base):
    def test_customer_close_early_needs_flag_staff_always_can(self):
        self.plan.can_close_early = False
        self.plan.save()
        sub = self.move(self.make_sub(), "quotation", "confirmed", "active")
        self.client.force_login(self.a)
        r = self.client.post(reverse("subscription_transition", args=[sub.pk]), {"to": "closed"})
        self.assertEqual(r.status_code, 403)
        sub = self.move(sub, "closed", user=self.staff)
        self.assertEqual(sub.status, "closed")

    def test_pause_needs_flag(self):
        sub = self.move(self.make_sub(), "quotation", "confirmed", "active")
        with self.assertRaises(svc.RuleViolation):
            svc.toggle_pause(sub, self.staff)
        self.plan.can_pause = True
        self.plan.save()
        self.assertTrue(svc.toggle_pause(sub, self.staff).is_paused)
        self.assertFalse(svc.toggle_pause(sub, self.staff).is_paused)
        self.assertEqual(sub.events.filter(note__in=["Paused", "Resumed"]).count(), 2)

    def test_renew_creates_new_draft_at_current_prices(self):
        sub = self.move(self.make_sub(), "quotation", "confirmed", "active", "closed")
        self.product.sales_price = 60000
        self.product.save()
        new = svc.renew(sub, self.staff)
        self.assertNotEqual(new.pk, sub.pk)
        self.assertEqual(new.status, "draft")
        self.assertEqual(new.lines.get().unit_price, 60000)
        self.assertEqual(sub.lines.get().unit_price, 56000)      # history untouched

    def test_renew_refused_without_flag_or_before_close(self):
        sub = self.move(self.make_sub(), "quotation", "confirmed", "active")
        with self.assertRaises(svc.RuleViolation):
            svc.renew(sub, self.staff)
        sub = self.move(sub, "closed")
        self.plan.can_renew = False
        self.plan.save()
        with self.assertRaises(svc.RuleViolation):
            svc.renew(sub, self.staff)


class AccessTests(Base):
    def test_customer_can_accept_own_quote_but_not_activate(self):
        sub = self.move(self.make_sub(), "quotation")
        self.client.force_login(self.a)
        self.client.post(reverse("subscription_transition", args=[sub.pk]), {"to": "confirmed"})
        sub.refresh_from_db()
        self.assertEqual(sub.status, "confirmed")
        r = self.client.post(reverse("subscription_transition", args=[sub.pk]), {"to": "active"})
        self.assertEqual(r.status_code, 403)

    def test_customer_gets_404_on_other_customers_subscription(self):
        sub_b = self.move(self.make_sub(self.b), "quotation")
        self.client.force_login(self.a)
        self.assertEqual(self.client.get(reverse("subscription_detail", args=[sub_b.pk])).status_code, 404)
        r = self.client.post(reverse("subscription_transition", args=[sub_b.pk]), {"to": "confirmed"})
        self.assertEqual(r.status_code, 404)
        sub_b.refresh_from_db()
        self.assertEqual(sub_b.status, "quotation")

    def test_customer_cannot_create_or_edit_lines(self):
        sub = self.make_sub()
        self.client.force_login(self.a)
        self.assertEqual(self.client.get(reverse("subscription_create")).status_code, 403)
        r = self.client.post(reverse("subscription_line_add", args=[sub.pk]),
                             {"product": self.product.pk, "quantity": 9})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(sub.lines.get().quantity, 2)

    def test_list_scoped_filtered_and_paginated(self):
        mine, theirs = self.make_sub(self.a), self.make_sub(self.b)
        self.client.force_login(self.a)
        r = self.client.get(reverse("subscription_list"))
        self.assertContains(r, mine.number)
        self.assertNotContains(r, theirs.number)
        self.client.force_login(self.staff)
        r = self.client.get(reverse("subscription_list"), {"status": "draft", "q": theirs.number, "page": 99})
        self.assertContains(r, theirs.number)
        self.assertNotContains(r, mine.number)

    def test_detail_shows_lines_and_timeline(self):
        sub = self.move(self.make_sub(), "quotation")
        self.client.force_login(self.staff)
        r = self.client.get(reverse("subscription_detail", args=[sub.pk]))
        self.assertContains(r, "Created from template Pro x2")
        self.assertContains(r, "₹1,120.00")          # 2 x ₹560
        self.assertContains(r, "18.00%")

    def test_template_lines_admin_only(self):
        url = reverse("template_lines", args=[self.tpl.pk])
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.admin)
        other = Product.objects.create(name="Other", sales_price=100)
        bad = ProductVariant.objects.create(product=other, attribute="Size", value="L")
        r = self.client.post(url, {"product": self.product.pk, "variant": bad.pk, "quantity": 1})
        self.assertEqual(r.status_code, 200)                       # variant mismatch rejected
        self.client.post(url, {"product": self.product.pk, "variant": self.variant.pk, "quantity": 3})
        self.assertEqual(self.tpl.lines.count(), 2)
        self.assertEqual(self.client.get(reverse("catalog_list", kwargs={"key": "templates"})).status_code, 200)
