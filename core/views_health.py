import time

from django.db import connection
from django.shortcuts import render

from core.invariants import check_all
from core.models import Invoice, Payment, Subscription
from core.roles import ADMIN, STAFF, role_required


@role_required(ADMIN, STAFF)
def health_check(request):
    checks = check_all()

    # Counts
    subscription_count = Subscription.objects.count()
    invoice_count = Invoice.objects.count()
    payment_count = Payment.objects.count()

    # Measure DB ping query time in ms
    t0 = time.perf_counter()
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    db_time_ms = round((time.perf_counter() - t0) * 1000, 2)

    context = {
        "checks": checks,
        "subscription_count": subscription_count,
        "invoice_count": invoice_count,
        "payment_count": payment_count,
        "db_time_ms": db_time_ms,
    }
    return render(request, "health.html", context)
