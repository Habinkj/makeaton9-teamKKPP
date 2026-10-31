"""Catalog: money/percent conversion, admin-only writes, validation.

Run:  docker compose run --rm web python manage.py test core -v 2
"""
from datetime import date

from django.test import TestCase
from django.urls import reverse

from core.models import Discount, Plan, Product, ProductVariant, TaxRule
from core.tests.test_auth import make_user


def new_url(key):
    return reverse("catalog_new", kwargs={"key": key})


def product_data(**kw):
    d = {"name": "Brightloop CRM", "product_type": "service", "sales_price": "560",
         "cost_price": "", "notes": "", "is_active": "on"}
    d.update(kw)
    return d


def discount_data(**kw):
    d = {"name": "Seats 10%", "kind": "percent", "value": "10", "scope": "subscription",
         "min_purchase": "", "min_quantity": "0", "starts_on": "2026-10-01",
         "ends_on": "", "usage_limit": ""}
    d.update(kw)
    return d


class CatalogTests(TestCase):
    def setUp(self):
        self.admin = make_user("boss@brightloop.test", "admin")
        self.staff = make_user("staff@brightloop.test", "staff")
        self.cust = make_user("cust@brightloop.test", "customer", with_customer=True)
        self.client.force_login(self.admin)

    # --- money & percent conversion -------------------------------------
    def test_product_price_rupees_stored_as_paise(self):
        r = self.client.post(new_url("products"), product_data())
        self.assertRedirects(r, reverse("catalog_list", kwargs={"key": "products"}))
        p = Product.objects.get()
        self.assertEqual((p.sales_price, p.cost_price), (56000, 0))

    def test_no_float_rounding_error(self):
        self.client.post(new_url("products"), product_data(sales_price="99.99"))
        self.assertEqual(Product.objects.get().sales_price, 9999)

    def test_sub_paise_input_rejected(self):
        r = self.client.post(new_url("products"), product_data(sales_price="10.005"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("sales_price", r.context["form"].errors)
        self.assertFalse(Product.objects.exists())

    def test_edit_form_shows_rupees(self):
        p = Product.objects.create(name="X", sales_price=56000)
        r = self.client.get(reverse("catalog_edit", kwargs={"key": "products", "pk": p.pk}))
        self.assertContains(r, 'value="560.00"')

    def test_tax_rate_percent_to_bps(self):
        self.client.post(new_url("taxes"), {"name": "GST 18%", "rate_bps": "18", "is_active": "on"})
        self.client.post(new_url("taxes"), {"name": "Half", "rate_bps": "12.5", "is_active": "on"})
        self.assertEqual(TaxRule.objects.get(name="GST 18%").rate_bps, 1800)
        self.assertEqual(TaxRule.objects.get(name="Half").rate_bps, 1250)

    def test_variant_extra_price_and_duplicate_rejected(self):
        p = Product.objects.create(name="X", sales_price=100)
        data = {"product": p.pk, "attribute": "Brand", "value": "Odoo", "extra_price": "560"}
        self.client.post(new_url("variants"), data)
        self.assertEqual(ProductVariant.objects.get().extra_price, 56000)
        r = self.client.post(new_url("variants"), {**data, "value": "ODOO"})
        self.assertEqual(r.status_code, 200)          # friendly error, not a 500
        self.assertEqual(ProductVariant.objects.count(), 1)

    # --- plans ------------------------------------------------------------
    def test_plan_created_and_date_order_enforced(self):
        base = {"name": "Pro", "price": "999", "billing_period": "monthly", "min_quantity": "1",
                "available_from": "2026-01-01", "available_until": "", "can_renew": "on"}
        self.client.post(new_url("plans"), base)
        plan = Plan.objects.get()
        self.assertEqual((plan.price, plan.can_renew, plan.can_pause), (99900, True, False))
        r = self.client.post(new_url("plans"), {**base, "name": "Bad", "available_until": "2025-12-31"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Plan.objects.count(), 1)

    # --- discounts ----------------------------------------------------------
    def test_discount_conversion(self):
        self.client.post(new_url("discounts"), discount_data())
        self.client.post(new_url("discounts"), discount_data(name="Flat", kind="fixed", value="250"))
        self.assertEqual(Discount.objects.get(name="Seats 10%").value, 1000)
        self.assertEqual(Discount.objects.get(name="Flat").value, 25000)

    def test_discount_validation(self):
        bad = [
            discount_data(value="150"),                                       # >100%
            discount_data(scope="product"),                                   # no products
            discount_data(starts_on="2026-10-10", ends_on="2026-10-01"),      # ends before start
        ]
        for data in bad:
            with self.subTest(data=data):
                r = self.client.post(new_url("discounts"), data)
                self.assertEqual(r.status_code, 200)
        self.assertFalse(Discount.objects.exists())

    # --- access control -----------------------------------------------------
    def test_only_admin_can_create_discounts(self):
        for user in (self.staff, self.cust):
            self.client.force_login(user)
            self.assertEqual(self.client.post(new_url("discounts"), discount_data()).status_code, 403)
        self.assertFalse(Discount.objects.exists())

    def test_staff_can_view_but_not_edit(self):
        p = Product.objects.create(name="X", sales_price=100)
        self.client.force_login(self.staff)
        r = self.client.get(reverse("catalog_list", kwargs={"key": "products"}))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, reverse("catalog_new", kwargs={"key": "products"}))
        self.assertEqual(self.client.get(new_url("products")).status_code, 403)
        edit = reverse("catalog_edit", kwargs={"key": "products", "pk": p.pk})
        self.assertEqual(self.client.post(edit, product_data(name="Hacked")).status_code, 403)
        self.assertEqual(Product.objects.get().name, "X")

    def test_customer_and_anonymous_blocked(self):
        url = reverse("catalog_list", kwargs={"key": "products"})
        self.client.force_login(self.cust)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_unknown_section_404(self):
        self.assertEqual(self.client.get("/catalog/nope/").status_code, 404)

    def test_every_list_and_form_renders(self):
        for key in ["products", "variants", "plans", "taxes", "discounts"]:
            with self.subTest(key=key):
                self.assertEqual(self.client.get(reverse("catalog_list", kwargs={"key": key})).status_code, 200)
                self.assertEqual(self.client.get(new_url(key)).status_code, 200)
