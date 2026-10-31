from django import forms

from core.models import Customer, PaymentTerms, Plan, Product, ProductVariant, QuotationTemplate
from core.subscriptions import RuleViolation, check_plan_available


def _style(form):
    for f in form.fields.values():
        w = f.widget
        w.attrs["class"] = ("select select-bordered w-full" if isinstance(w, forms.Select)
                            else "input input-bordered w-full")


class SubscriptionCreateForm(forms.Form):
    customer = forms.ModelChoiceField(Customer.objects.order_by("name"))
    plan = forms.ModelChoiceField(Plan.objects.order_by("name"))
    template = forms.ModelChoiceField(
        QuotationTemplate.objects.select_related("plan").order_by("name"), required=False,
        label="Copy lines from quotation template (optional)")
    start_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    payment_terms = forms.ChoiceField(choices=PaymentTerms.choices, initial=PaymentTerms.IMMEDIATE)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        _style(self)

    def clean(self):
        data = super().clean()
        plan, tpl, start = data.get("plan"), data.get("template"), data.get("start_date")
        if plan and tpl and tpl.plan_id != plan.id:
            self.add_error("template", f"This template is for {tpl.plan}, not {plan}.")
        if plan and start:
            try:
                check_plan_available(plan, start)
            except RuleViolation as e:
                self.add_error("start_date", str(e))
        return data


class LineForm(forms.Form):
    """Product + optional variant + quantity; shared by subscriptions and templates."""
    product = forms.ModelChoiceField(Product.objects.filter(is_active=True).order_by("name"))
    variant = forms.ModelChoiceField(
        ProductVariant.objects.select_related("product").order_by("product__name", "attribute", "value"),
        required=False)
    quantity = forms.IntegerField(min_value=1, initial=1)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        _style(self)

    def clean(self):
        data = super().clean()
        p, v = data.get("product"), data.get("variant")
        if p and v and v.product_id != p.id:
            self.add_error("variant", "That variant belongs to a different product.")
        return data
