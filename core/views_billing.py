import json
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from django.core.paginator import Paginator
from django.db.models import F, Q, Sum
from django.db.models.functions import Coalesce

from core import billing, clock, payments
from core.forms_payments import PaymentForm
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
    balance = max(invoice.total - paid, 0)
    can_pay = internal and invoice.status == "confirmed" and balance > 0
    return render(request, "invoices/detail.html", {
        "payments": invoice.payments.select_related("recorded_by").order_by("-paid_on", "-id"),
        "payment_form": PaymentForm() if can_pay else None,
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


PAGE_SIZE = 25


@login_required
def invoice_list(request):
    qs = (scoped_invoices(request.user)
          .annotate(paid=Coalesce(Sum("payments__amount"), 0))
          .annotate(balance=F("total") - F("paid")))
    status = request.GET.get("status", "")
    q = request.GET.get("q", "").strip()
    if status == "overdue":
        qs = qs.filter(status="confirmed", due_date__lt=clock.today())     # derived, not stored
    elif status in ("draft", "confirmed", "paid", "cancelled"):
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(Q(number__icontains=q) | Q(customer__name__icontains=q))
    page = Paginator(qs.order_by("-issue_date", "-id"), PAGE_SIZE).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "invoices/list.html", {
        "page": page, "status": status, "q": q, "today": clock.today(),
        "querystring": params.urlencode()})


@role_required(ADMIN, STAFF)
@require_POST
def invoice_payment(request, pk):
    invoice = get_object_or_404(scoped_invoices(request.user), pk=pk)
    form = PaymentForm(request.POST)
    if not form.is_valid():
        messages.error(request, "; ".join(f"{k}: {' '.join(v)}" for k, v in form.errors.items()))
    else:
        d = form.cleaned_data
        try:
            payments.record_payment(invoice, amount=form.amount_minor(), method=d["method"],
                                    paid_on=d["paid_on"], note=d["note"], actor=request.user)
        except billing.BillingError as e:
            messages.error(request, str(e))
        else:
            invoice.refresh_from_db()
            messages.success(request, "Invoice is now fully paid." if invoice.status == "paid"
                             else "Partial payment recorded; invoice stays outstanding.")
    return redirect("invoice_detail", pk=invoice.pk)
