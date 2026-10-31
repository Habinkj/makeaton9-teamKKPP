"""Role checks and row-level scoping. Every customer-facing query goes
through the `scoped_*` helpers so a customer can never reach another
customer's rows: out-of-scope objects are simply not in the queryset,
so get_object_or_404 returns 404 (not 403 — we don't confirm they exist).
"""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from core.models import Invoice, Subscription

ADMIN, STAFF, CUSTOMER = "admin", "staff", "customer"
ALL_ROLES = (ADMIN, STAFF, CUSTOMER)


def role_required(*roles):
    """Anonymous -> redirect to login. Logged in with wrong role -> 403."""
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.user.role not in roles:
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return login_required(wrapped)
    return decorator


def _is_internal(user):
    return user.role in (ADMIN, STAFF)


def scoped_invoices(user):
    qs = Invoice.objects.select_related("customer", "subscription")
    if _is_internal(user):
        return qs
    return qs.filter(customer__user=user)  # no Customer row -> empty


def scoped_subscriptions(user):
    qs = Subscription.objects.select_related("customer", "plan")
    if _is_internal(user):
        return qs
    return qs.filter(customer__user=user)
