"""Catalog forms. Admins type rupees / percentages; we store paise / basis points."""
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError

from core.formatting import from_minor, to_minor
from core.models import Discount, Plan, Product, ProductVariant, TaxRule


def date_widget():
    return forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class CatalogForm(forms.ModelForm):
    scaled = {}  # model field name -> label shown to the human (value is stored x100)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, label in self.scaled.items():
            mf = self._meta.model._meta.get_field(name)
            self.fields[name] = forms.DecimalField(
                label=label, required=not mf.has_default(), min_value=Decimal("0"),
                max_digits=12, decimal_places=2,
            )
            if self.instance.pk:
                self.initial[name] = from_minor(getattr(self.instance, name))
        for f in self.fields.values():
            w = f.widget
            if isinstance(w, forms.CheckboxSelectMultiple):
                continue
            if isinstance(w, forms.CheckboxInput):
                cls = "checkbox"
            elif isinstance(w, (forms.Select, forms.SelectMultiple)):
                cls = "select select-bordered w-full"
            elif isinstance(w, forms.Textarea):
                cls = "textarea textarea-bordered w-full"
            else:
                cls = "input input-bordered w-full"
            w.attrs["class"] = cls

    def clean(self):
        data = super().clean()
        for name in self.scaled:
            if name in data:  # skip fields that already failed validation
                v = data[name]
                data[name] = 0 if v is None else to_minor(v)
        return data


class TaxRuleForm(CatalogForm):
    scaled = {"rate_bps": "Rate (%)"}

    class Meta:
        model = TaxRule
        fields = ["name", "rate_bps", "is_active"]

    def clean(self):
        data = super().clean()
        if data.get("rate_bps", 0) > 10000:
            self.add_error("rate_bps", "A tax rate can't exceed 100%.")
        return data


class ProductForm(CatalogForm):
    scaled = {"sales_price": "Sales price (₹, excl. tax)", "cost_price": "Cost price (₹)"}

    class Meta:
        model = Product
        fields = ["name", "product_type", "sales_price", "cost_price", "tax_rules",
                  "notes", "is_active"]
        widgets = {"tax_rules": forms.CheckboxSelectMultiple, "notes": forms.Textarea(attrs={"rows": 3})}


class VariantForm(CatalogForm):
    scaled = {"extra_price": "Extra price (₹, added to product price)"}

    class Meta:
        model = ProductVariant
        fields = ["product", "attribute", "value", "extra_price"]

    def clean(self):
        data = super().clean()
        p, a, v = data.get("product"), data.get("attribute"), data.get("value")
        if p and a and v:
            dup = ProductVariant.objects.filter(
                product=p, attribute__iexact=a.strip(), value__iexact=v.strip())
            if self.instance.pk:
                dup = dup.exclude(pk=self.instance.pk)
            if dup.exists():
                raise ValidationError("This product already has that variant.")
        return data


class PlanForm(CatalogForm):
    scaled = {"price": "Price per period (₹, excl. tax)"}

    class Meta:
        model = Plan
        fields = ["name", "price", "billing_period", "min_quantity", "available_from",
                  "available_until", "auto_close", "can_close_early", "can_pause", "can_renew"]
        widgets = {"available_from": date_widget(), "available_until": date_widget()}

    def clean(self):
        data = super().clean()
        s, e = data.get("available_from"), data.get("available_until")
        if s and e and e < s:
            self.add_error("available_until", "Must not be before the start date.")
        return data


class DiscountForm(CatalogForm):
    scaled = {"value": "Value (% if percentage, ₹ if fixed amount)",
              "min_purchase": "Minimum purchase (₹)"}

    class Meta:
        model = Discount
        fields = ["name", "kind", "value", "scope", "products", "min_purchase",
                  "min_quantity", "starts_on", "ends_on", "usage_limit"]
        widgets = {"starts_on": date_widget(), "ends_on": date_widget()}

    def clean(self):
        data = super().clean()
        if data.get("kind") == "percent" and data.get("value", 0) > 10000:
            self.add_error("value", "A percentage discount can't exceed 100%.")
        s, e = data.get("starts_on"), data.get("ends_on")
        if s and e and e < s:
            self.add_error("ends_on", "Must not be before the start date.")
        scope, products = data.get("scope"), data.get("products")
        if scope == "product" and not products:
            self.add_error("products", "Pick at least one product for a product discount.")
        if scope == "subscription" and products:
            self.add_error("products", "Whole-subscription discounts don't take products.")
        limit = data.get("usage_limit")
        if limit is not None and self.instance.pk and limit < self.instance.used_count:
            self.add_error("usage_limit", f"Already used {self.instance.used_count} times.")
        return data
