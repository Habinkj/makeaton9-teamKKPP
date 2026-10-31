from datetime import date

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.shortcuts import render

from core.models import Payment
from core.roles import scoped_invoices

PAGE_SIZE = 25


@login_required
def payment_list(request):
    qs = (Payment.objects
          .filter(invoice__in=scoped_invoices(request.user))
          .select_related("invoice", "invoice__customer")
          .order_by("-paid_on", "-id"))

    # --- filters ---
    method = request.GET.get("method", "")
    valid_methods = {c[0] for c in Payment.Method.choices}
    if method not in valid_methods:
        method = ""
    if method:
        qs = qs.filter(method=method)

    date_from = request.GET.get("from", "")
    date_to = request.GET.get("to", "")
    try:
        qs = qs.filter(paid_on__gte=date.fromisoformat(date_from)) if date_from else qs
    except ValueError:
        date_from = ""
    try:
        qs = qs.filter(paid_on__lte=date.fromisoformat(date_to)) if date_to else qs
    except ValueError:
        date_to = ""

    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(invoice__number__icontains=q) | Q(invoice__customer__name__icontains=q))

    # --- aggregates over the full filtered set ---
    total = qs.aggregate(total=Sum("amount"))["total"] or 0
    count = qs.count()

    # --- paginate ---
    page = Paginator(qs, PAGE_SIZE).get_page(request.GET.get("page"))

    # build querystring without page for pagination links
    params = request.GET.copy()
    params.pop("page", None)

    return render(request, "payments/list.html", {
        "page": page,
        "total": total,
        "count": count,
        "method": method,
        "date_from": date_from,
        "date_to": date_to,
        "q": q,
        "methods": Payment.Method.choices,
        "querystring": params.urlencode(),
    })
