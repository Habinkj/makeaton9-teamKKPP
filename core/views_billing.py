import json
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from core import billing, clock
from core.roles import ADMIN, STAFF, role_required, scoped_invoices
from core.statemachine import InvalidTransition


def _as_of(request):
    raw = request.POST.get("as_of") or request.GET.get("as_of")
    try:
        return date.fromisoformat(raw) if raw else clock.today()
    except ValueError:
        return clock.today()


@role_required(ADMIN, STAFF)
def billing_home(request):
    as_of = _as_of(request)
    if request.method == "POST":
        s = billing.run_billing(as_of, actor=request.user)
        (messages.success if not s.errors else messages.warning)(request, s.as_text())
        for number, msg in s.errors[:5]:
            messages.error(request, f"{number}: {msg}")
        return redirect(f"{reverse('billing_home')}?as_of={as_of.isoformat()}")
    return render(request, "billing/index.html", {"as_of": as_of, "p": billing.preview(as_of)})


@login_required
def invoice_detail(request, pk):
    invoice = get_object_or_404(scoped_invoices(request.user), pk=pk)
    internal = request.user.role in (ADMIN, STAFF)
    actions = []
    if internal:
        if invoice.status == "draft":
            actions.append(("confirm", "Confirm", "btn-primary"))
        if invoice.status in ("confirmed", "paid"):
            actions.append(("send", "Email to customer", "btn-outline"))
        if invoice.status in ("draft", "confirmed"):
            actions.append(("cancel", "Cancel", "btn-outline btn-error"))
    paid = billing.amount_paid(invoice)
    return render(request, "invoices/detail.html", {
        "invoice": invoice, "lines": invoice.lines.all().order_by("id"), "actions": actions,
        "paid": paid, "balance": max(invoice.total - paid, 0),
        "overdue": invoice.status == "confirmed" and invoice.due_date < clock.today(),
        "events": invoice.events.select_related("actor"), "internal": internal,
        "trace": json.dumps(invoice.calc_trace, indent=2) if internal and invoice.calc_trace else "",
    })


@role_required(ADMIN, STAFF)
@require_POST
def invoice_action(request, pk, action):
    invoice = get_object_or_404(scoped_invoices(request.user), pk=pk)
    fn = {"confirm": billing.confirm_invoice, "cancel": billing.cancel_invoice,
          "send": billing.send_invoice}.get(action)
    if fn is None:
        messages.error(request, "Unknown action.")
    else:
        try:
            fn(invoice, request.user)
        except (billing.BillingError, InvalidTransition) as e:
            messages.error(request, str(e))
        else:
            messages.success(request, {"confirm": "Invoice confirmed.", "cancel": "Invoice cancelled; discount use released.",
                                       "send": "Invoice emailed (check Mailpit)."}[action])
    return redirect("invoice_detail", pk=invoice.pk)


@login_required
def invoice_print(request, pk):
    invoice = get_object_or_404(scoped_invoices(request.user), pk=pk)
    paid = billing.amount_paid(invoice)
    return render(request, "invoices/print.html", {
        "invoice": invoice, "lines": invoice.lines.all().order_by("id"),
        "paid": paid, "balance": max(invoice.total - paid, 0)})
