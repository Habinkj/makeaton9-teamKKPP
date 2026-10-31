"""Dashboard: every figure is one aggregate query, so it stays fast on 10k+ subscriptions.
Customers get the same page scoped to their own rows."""
import time
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import BigIntegerField, Count, F, Q, Sum
from django.db.models.functions import Coalesce, TruncMonth
from django.shortcuts import redirect, render

from core import clock
from core.dashboard_calc import mrr
from core.models import Invoice, Payment, Subscription, SubscriptionLine
from core.roles import ADMIN, STAFF

OVERDUE_LIMIT = 25


def home(request):
    """'/' shows the dashboard for signed-in users, the login page otherwise."""
    if not request.user.is_authenticated:
        return redirect("login")
    return dashboard(request)


def _scope(user):
    internal = user.role in (ADMIN, STAFF)
    subs = Subscription.objects.all() if internal else Subscription.objects.filter(customer__user=user)
    invs = Invoice.objects.all() if internal else Invoice.objects.filter(customer__user=user)
    return internal, subs, invs


def _sum(qs, field):
    return qs.aggregate(t=Sum(field))["t"] or 0


def _date(raw, default):
    from datetime import date
    try:
        return date.fromisoformat(raw) if raw else default
    except ValueError:
        return default


def overdue_rows(invs, today, q="", min_days=0):
    qs = invs.filter(status="confirmed", due_date__lt=today)       # overdue is derived, never stored
    if min_days:
        qs = qs.filter(due_date__lte=today - timedelta(days=min_days))
    if q:
        qs = qs.filter(Q(number__icontains=q) | Q(customer__name__icontains=q)
                       | Q(customer__company__icontains=q))
    total = qs.count()
    rows = (qs.select_related("customer")
            .annotate(paid=Coalesce(Sum("payments__amount"), 0))
            .annotate(balance=F("total") - F("paid"))
            .order_by("due_date", "id")[:OVERDUE_LIMIT])
    return [{"inv": r, "days": (today - r.due_date).days} for r in rows], total


@login_required
def dashboard(request):
    t0 = time.perf_counter()
    today = clock.today()
    internal, subs, invs = _scope(request.user)
    q = request.GET.get("q", "").strip()
    try:
        min_days = int(request.GET.get("min_days", 0))
    except ValueError:
        min_days = 0
    rows, overdue_count = overdue_rows(invs, today, q, min_days)

    if request.headers.get("HX-Request"):                          # HTMX filter: table only
        return render(request, "dashboard/_overdue.html", {
            "rows": rows, "overdue_count": overdue_count, "limit": OVERDUE_LIMIT})

    d_from = _date(request.GET.get("from"), today - timedelta(days=29))
    d_to = _date(request.GET.get("to"), today)
    payments = Payment.objects.filter(invoice__in=invs)

    active = subs.filter(status="active")
    mrr_rows = (SubscriptionLine.objects
                .filter(subscription__in=active.filter(is_paused=False))
                .values_list("subscription__plan__billing_period")
                .annotate(t=Sum(F("quantity") * F("unit_price"), output_field=BigIntegerField())))
    confirmed = invs.filter(status="confirmed")
    overdue = confirmed.filter(due_date__lt=today)

    kpis = {
        "active": active.count(),
        "mrr": mrr(mrr_rows),
        "collected": _sum(payments.filter(paid_on__range=(d_from, d_to)), "amount"),
        "outstanding": _sum(confirmed, "total") - _sum(payments.filter(invoice__in=confirmed), "amount"),
        "overdue_amount": _sum(overdue, "total") - _sum(payments.filter(invoice__in=overdue), "amount"),
        "overdue_count": overdue.count(),
    }

    # Chart 1: collected per month, last 6 months (zero-filled)
    first = (today.replace(day=1) - timedelta(days=150)).replace(day=1)
    by_month = {r["m"].strftime("%Y-%m") if hasattr(r["m"], "strftime") else str(r["m"])[:7]: r["t"]
                for r in payments.filter(paid_on__gte=first, paid_on__lte=today)
                .annotate(m=TruncMonth("paid_on")).values("m").annotate(t=Sum("amount"))}
    months, m = [], first
    while m <= today:
        months.append(m)
        m = (m + timedelta(days=32)).replace(day=1)
    chart_months = {"labels": [x.strftime("%b %Y") for x in months],
                    "values": [round(by_month.get(x.strftime("%Y-%m"), 0) / 100, 2) for x in months]}

    # Chart 2: subscriptions by status
    status_counts = dict(subs.values_list("status").annotate(n=Count("id")))
    order = ["draft", "quotation", "confirmed", "active", "closed"]
    chart_status = {"labels": [s.title() for s in order], "values": [status_counts.get(s, 0) for s in order]}

    return render(request, "dashboard/index.html", {
        "kpis": kpis, "today": today, "d_from": d_from, "d_to": d_to, "internal": internal,
        "rows": rows, "overdue_count": overdue_count, "limit": OVERDUE_LIMIT, "q": q, "min_days": min_days,
        "chart_months": chart_months, "chart_status": chart_status,
        "elapsed_ms": round((time.perf_counter() - t0) * 1000),
    })
