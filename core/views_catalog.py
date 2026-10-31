"""One generic list view and one generic create/edit view drive every catalog
screen. Adding a catalog entity = one CONFIG entry + one form."""
from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from core.forms_catalog import DiscountForm, PlanForm, ProductForm, TaxRuleForm, VariantForm
from core.formatting import format_bps, format_rupees
from core.models import Discount, Plan, Product, ProductVariant, TaxRule
from core.roles import ADMIN, STAFF, role_required

CONFIG = {
    "products": {
        "title": "Products", "model": Product, "form": ProductForm,
        "order": ("name",), "prefetch": ("tax_rules",),
        "columns": [
            ("Name", "name", "text"),
            ("Type", lambda o: o.get_product_type_display(), "text"),
            ("Price", "sales_price", "money"),
            ("Cost", "cost_price", "money"),
            ("Tax", lambda o: ", ".join(t.name for t in o.tax_rules.all()), "text"),
            ("Active", "is_active", "bool"),
        ],
    },
    "variants": {
        "title": "Variants", "model": ProductVariant, "form": VariantForm,
        "order": ("product__name", "attribute", "value"), "related": ("product",),
        "columns": [
            ("Product", "product", "text"),
            ("Attribute", "attribute", "text"),
            ("Value", "value", "text"),
            ("Extra price", "extra_price", "money"),
        ],
    },
    "plans": {
        "title": "Plans", "model": Plan, "form": PlanForm, "order": ("name",),
        "columns": [
            ("Name", "name", "text"),
            ("Price", "price", "money"),
            ("Billing", lambda o: o.get_billing_period_display(), "text"),
            ("Min qty", "min_quantity", "text"),
            ("From", "available_from", "date"),
            ("Until", "available_until", "date"),
        ],
    },
    "taxes": {
        "title": "Tax rules", "model": TaxRule, "form": TaxRuleForm, "order": ("name",),
        "columns": [
            ("Name", "name", "text"),
            ("Rate", "rate_bps", "bps"),
            ("Active", "is_active", "bool"),
        ],
    },
    "discounts": {
        "title": "Discounts", "model": Discount, "form": DiscountForm, "order": ("-starts_on", "name"),
        "columns": [
            ("Name", "name", "text"),
            ("Value", lambda o: format_bps(o.value) if o.kind == "percent" else format_rupees(o.value), "text"),
            ("Scope", lambda o: o.get_scope_display(), "text"),
            ("From", "starts_on", "date"),
            ("Until", "ends_on", "date"),
            ("Used", lambda o: f"{o.used_count}/{o.usage_limit if o.usage_limit is not None else '∞'}", "text"),
        ],
    },
}
SECTIONS = [(k, c["title"]) for k, c in CONFIG.items()]


def _cfg(key):
    try:
        return CONFIG[key]
    except KeyError:
        raise Http404("Unknown catalog section")


def _cell(obj, source, kind):
    val = source(obj) if callable(source) else getattr(obj, source)
    if kind == "money":
        return format_rupees(val)
    if kind == "bps":
        return format_bps(val)
    if kind == "bool":
        return "Yes" if val else "No"
    if kind == "date":
        return val.strftime("%d %b %Y") if val else "—"
    return "—" if val in (None, "") else str(val)


@role_required(ADMIN, STAFF)
def catalog_list(request, key):
    cfg = _cfg(key)
    qs = cfg["model"].objects.all()
    if cfg.get("related"):
        qs = qs.select_related(*cfg["related"])
    if cfg.get("prefetch"):
        qs = qs.prefetch_related(*cfg["prefetch"])
    rows = [
        {"pk": o.pk, "cells": [_cell(o, src, kind) for _, src, kind in cfg["columns"]]}
        for o in qs.order_by(*cfg["order"])[:500]
    ]
    return render(request, "catalog/list.html", {
        "cfg": cfg, "current": key, "sections": SECTIONS,
        "headers": [c[0] for c in cfg["columns"]], "rows": rows,
        "can_edit": request.user.role == ADMIN,
    })


@role_required(ADMIN)  # staff can look, only the admin can change the catalog
def catalog_edit(request, key, pk=None):
    cfg = _cfg(key)
    obj = get_object_or_404(cfg["model"], pk=pk) if pk else None
    form = cfg["form"](request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        messages.success(request, f"Saved {saved}.")
        return redirect("catalog_list", key=key)
    return render(request, "catalog/form.html", {
        "cfg": cfg, "current": key, "form": form, "obj": obj, "sections": SECTIONS})
