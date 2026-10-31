from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.forms_subscriptions import LineForm, SubscriptionCreateForm
from core.models import QuotationTemplate, QuotationTemplateLine, Subscription
from core.roles import ADMIN, STAFF, role_required, scoped_subscriptions
from core.statemachine import InvalidTransition, allowed_next
from core import subscriptions as svc

STATUS_BADGE = {"draft": "badge-ghost", "quotation": "badge-info", "confirmed": "badge-warning",
                "active": "badge-success", "closed": "badge-neutral"}
BUTTON_CSS = {"closed": "btn-outline btn-error", "draft": "btn-ghost", "active": "btn-success"}
PAGE_SIZE = 25


@login_required
def subscription_list(request):
    qs = scoped_subscriptions(request.user)
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    if q:
        qs = qs.filter(Q(number__icontains=q) | Q(customer__name__icontains=q)
                       | Q(customer__company__icontains=q))
    if status in Subscription.Status.values:
        qs = qs.filter(status=status)
    page = Paginator(qs.order_by("-id"), PAGE_SIZE).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "subscriptions/list.html", {
        "page": page, "q": q, "status": status, "statuses": Subscription.Status.choices,
        "badge": STATUS_BADGE, "querystring": params.urlencode(),
    })


@role_required(ADMIN, STAFF)
def subscription_create(request):
    form = SubscriptionCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            sub = svc.create_subscription(
                customer=d["customer"], plan=d["plan"], start_date=d["start_date"],
                payment_terms=d["payment_terms"], actor=request.user, template=d["template"])
        except svc.RuleViolation as e:
            form.add_error(None, str(e))
        else:
            messages.success(request, f"Created {sub.number}.")
            return redirect("subscription_detail", pk=sub.pk)
    return render(request, "subscriptions/form.html", {"form": form})


def _get_sub(request, pk):
    # Same scoping as the list: another customer's subscription is a 404.
    return get_object_or_404(scoped_subscriptions(request.user), pk=pk)


@login_required
def subscription_detail(request, pk):
    sub = _get_sub(request, pk)
    internal = request.user.role in (ADMIN, STAFF)
    lines = list(sub.lines.select_related("product", "variant").order_by("id"))
    rows = [{"line": l, "amount": l.quantity * l.unit_price} for l in lines]
    actions = [(to, svc.LABELS[to], BUTTON_CSS.get(to, "btn-primary"))
               for to in allowed_next(sub) if svc.can_move(request.user, sub, to)]
    editable = internal and sub.status == Subscription.Status.DRAFT
    return render(request, "subscriptions/detail.html", {
        "sub": sub, "rows": rows, "subtotal": sum(r["amount"] for r in rows),
        "actions": actions, "badge": STATUS_BADGE[sub.status],
        "events": sub.events.select_related("actor"),
        "editable": editable, "line_form": LineForm() if editable else None,
        "can_pause": sub.status == "active" and (sub.plan.can_pause or sub.is_paused),
        "can_renew": sub.status == "closed" and sub.plan.can_renew,
    })


@login_required
@require_POST
def subscription_transition(request, pk):
    sub = _get_sub(request, pk)
    to = request.POST.get("to", "")
    try:
        svc.do_transition(sub, to, request.user)
    except PermissionDenied:
        raise
    except (svc.RuleViolation, InvalidTransition) as e:
        messages.error(request, str(e))
    else:
        messages.success(request, f"{sub.number} is now {sub.get_status_display()}.")
    return redirect("subscription_detail", pk=sub.pk)


@role_required(ADMIN, STAFF)
@require_POST
def subscription_line_add(request, pk):
    sub = _get_sub(request, pk)
    form = LineForm(request.POST)
    if form.is_valid():
        d = form.cleaned_data
        try:
            svc.add_line(sub, d["product"], d["variant"], d["quantity"])
        except svc.RuleViolation as e:
            messages.error(request, str(e))
    else:
        messages.error(request, "; ".join(f"{k}: {' '.join(v)}" for k, v in form.errors.items()))
    return redirect("subscription_detail", pk=sub.pk)


@role_required(ADMIN, STAFF)
@require_POST
def subscription_line_delete(request, pk, line_pk):
    sub = _get_sub(request, pk)
    if sub.status != Subscription.Status.DRAFT:
        messages.error(request, "Lines can only be edited while the subscription is a draft.")
    else:
        sub.lines.filter(pk=line_pk).delete()
    return redirect("subscription_detail", pk=sub.pk)


@login_required
@require_POST
def subscription_pause(request, pk):
    sub = _get_sub(request, pk)
    try:
        locked = svc.toggle_pause(sub, request.user)
    except svc.RuleViolation as e:
        messages.error(request, str(e))
    else:
        messages.success(request, "Paused." if locked.is_paused else "Resumed.")
    return redirect("subscription_detail", pk=sub.pk)


@login_required
@require_POST
def subscription_renew(request, pk):
    sub = _get_sub(request, pk)
    try:
        new = svc.renew(sub, request.user)
    except svc.RuleViolation as e:
        messages.error(request, str(e))
        return redirect("subscription_detail", pk=sub.pk)
    messages.success(request, f"Renewal draft {new.number} created at current prices.")
    return redirect("subscription_detail", pk=new.pk)


# --- quotation template lines (admin) ---------------------------------------

@role_required(ADMIN)
def template_lines(request, pk):
    tpl = get_object_or_404(QuotationTemplate.objects.select_related("plan"), pk=pk)
    form = LineForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        QuotationTemplateLine.objects.create(
            template=tpl, product=d["product"], variant=d["variant"], quantity=d["quantity"])
        messages.success(request, "Line added.")
        return redirect("template_lines", pk=tpl.pk)
    lines = tpl.lines.select_related("product", "variant").order_by("id")
    return render(request, "catalog/template_lines.html", {
        "tpl": tpl, "lines": lines, "form": form, "current": "templates"})


@role_required(ADMIN)
@require_POST
def template_line_delete(request, pk, line_pk):
    tpl = get_object_or_404(QuotationTemplate, pk=pk)
    tpl.lines.filter(pk=line_pk).delete()
    return redirect("template_lines", pk=tpl.pk)
