"""Data-integrity checks. Each returns the number of VIOLATING rows (0 = healthy).
Used by the seed tests now and the health-check page later."""
from django.db.models import Count, F, Max, Q, Sum
from django.db.models.functions import Coalesce

from core.models import Invoice, Subscription


def totals_add_up():
    return Invoice.objects.exclude(
        total=F("subtotal") - F("discount_total") + F("tax_total")).count()


def invoice_equals_sum_of_lines():
    return (Invoice.objects.annotate(ls=Sum("lines__line_total"))
            .filter(ls__isnull=False).exclude(ls=F("total")).count())


def payment_status_consistent():
    """Paid <=> payments cover the total; open invoices aren't fully covered; cancelled have none."""
    return (Invoice.objects.annotate(paid=Coalesce(Sum("payments__amount"), 0))
            .filter(Q(status="paid", paid__lt=F("total"))
                    | Q(status__in=("draft", "confirmed"), total__gt=0, paid__gte=F("total"))
                    | Q(status="cancelled", paid__gt=0)).count())


def no_duplicate_periods():
    return (Invoice.objects.exclude(status="cancelled")
            .values("subscription", "period_start").annotate(n=Count("id")).filter(n__gt=1).count())


def schedule_ahead_of_invoices():
    """An active subscription's next_invoice_date is always after its latest billed period."""
    return (Subscription.objects.filter(status="active", next_invoice_date__isnull=False)
            .annotate(last=Max("invoices__period_start"))
            .filter(last__gte=F("next_invoice_date")).count())


CHECKS = [
    ("Invoice total = subtotal - discount + tax", totals_add_up),
    ("Invoice total = sum of its lines", invoice_equals_sum_of_lines),
    ("Paid/open/cancelled status matches payments", payment_status_consistent),
    ("No two live invoices for the same period", no_duplicate_periods),
    ("Active subscriptions are scheduled after their last invoice", schedule_ahead_of_invoices),
]


def check_all():
    return [(name, fn()) for name, fn in CHECKS]
