"""Quote -> Subscription service layer. Views stay thin; every business rule
lives here so it can be tested without HTTP and reused by the billing engine.

Plan flags (decided per plan by the admin):
  min_quantity     total quantity across lines must reach it before quoting/confirming
  available_from/until   start date must fall inside the window
  can_close_early  customers may close an active subscription themselves
                   (staff/admin can always close)
  can_pause        allows is_paused toggling (there is no Paused state; see README)
  can_renew        allows creating a renewal from a closed subscription
  auto_close       handled by the billing/clock block (closes at expiry_date)
"""
from datetime import timedelta
from uuid import uuid4

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Sum

from core import clock
from core.models import Event, Subscription, SubscriptionLine
from core.roles import CUSTOMER
from core.statemachine import allowed_next, apply_transition

DEFAULT_QUOTE_DAYS = 30

# What a customer may do to their own subscription through the portal.
CUSTOMER_MOVES = {("quotation", "confirmed"), ("quotation", "closed"), ("active", "closed")}

LABELS = {
    "draft": "Back to draft", "quotation": "Send quotation", "confirmed": "Confirm",
    "active": "Activate", "closed": "Close",
}


class RuleViolation(Exception):
    """A business rule stopped the action. The message is safe to show the user."""


def check_plan_available(plan, on_date):
    if on_date < plan.available_from or (plan.available_until and on_date > plan.available_until):
        until = plan.available_until.strftime("%d %b %Y") if plan.available_until else "no end date"
        raise RuleViolation(
            f"{plan.name} is only available from {plan.available_from.strftime('%d %b %Y')} ({until}).")


def line_snapshot(product, variant=None):
    """Freeze today's price and tax rate onto the line."""
    if not product.is_active:
        raise RuleViolation(f"{product.name} is inactive.")
    if variant is not None and variant.product_id != product.id:
        raise RuleViolation("That variant belongs to a different product.")
    unit = product.sales_price + (variant.extra_price if variant else 0)
    rate = sum(r.rate_bps for r in product.tax_rules.all() if r.is_active)
    return {"unit_price": unit, "tax_rate_bps": rate}


@transaction.atomic
def add_line(sub, product, variant, quantity):
    if sub.status != Subscription.Status.DRAFT:
        raise RuleViolation("Lines can only be edited while the subscription is a draft.")
    if quantity < 1:
        raise RuleViolation("Quantity must be at least 1.")
    existing = SubscriptionLine.objects.filter(
        subscription=sub, product=product, variant=variant).first()
    if existing:
        existing.quantity += quantity
        existing.save(update_fields=["quantity"])
        return existing
    return SubscriptionLine.objects.create(
        subscription=sub, product=product, variant=variant, quantity=quantity,
        **line_snapshot(product, variant))


@transaction.atomic
def create_subscription(*, customer, plan, start_date, payment_terms, actor,
                        template=None, note=""):
    check_plan_available(plan, start_date)
    days = template.validity_days if template else DEFAULT_QUOTE_DAYS
    sub = Subscription.objects.create(
        number=f"TMP-{uuid4().hex[:12]}",            # replaced below, once the pk exists
        customer=customer, plan=plan, status=Subscription.Status.DRAFT,
        start_date=start_date, payment_terms=payment_terms,
        quotation_valid_until=clock.today() + timedelta(days=days), created_by=actor)
    sub.number = f"SUB-{sub.pk:06d}"
    sub.save(update_fields=["number"])
    if template:
        # Copy, don't reference: later template edits never touch this subscription.
        tlines = template.lines.select_related("product", "variant").prefetch_related(
            "product__tax_rules")
        for tl in tlines:
            add_line(sub, tl.product, tl.variant, tl.quantity)
        note = note or f"Created from template {template.name}"
    Event.objects.create(subscription=sub, from_status="", to_status="draft",
                         actor=actor, note=note or "Created")
    return sub


def customer_may(sub, to_status):
    if (sub.status, to_status) not in CUSTOMER_MOVES:
        return False
    if sub.status == "active" and to_status == "closed" and not sub.plan.can_close_early:
        return False
    return True


def can_move(user, sub, to_status):
    """Used for both the buttons the page shows and the server-side check."""
    if to_status not in allowed_next(sub):
        return False
    return user.role != CUSTOMER or customer_may(sub, to_status)


def check_ready(sub, *, for_confirmation):
    lines = sub.lines.aggregate(total=Sum("quantity"))
    total = lines["total"] or 0
    if total == 0:
        raise RuleViolation("Add at least one line first.")
    if total < sub.plan.min_quantity:
        raise RuleViolation(
            f"{sub.plan.name} needs a minimum quantity of {sub.plan.min_quantity}; "
            f"this subscription has {total}.")
    check_plan_available(sub.plan, sub.start_date)
    if for_confirmation and sub.quotation_valid_until and sub.quotation_valid_until < clock.today():
        raise RuleViolation(
            f"This quotation expired on {sub.quotation_valid_until.strftime('%d %b %Y')}.")


def do_transition(sub, to_status, actor):
    """The one entry point for status changes from the UI."""
    if actor.role == CUSTOMER and not customer_may(sub, to_status):
        raise PermissionDenied
    if to_status == "quotation":
        check_ready(sub, for_confirmation=False)
    elif to_status == "confirmed":
        check_ready(sub, for_confirmation=True)
    with transaction.atomic():
        apply_transition(sub, to_status, actor=actor)    # raises InvalidTransition (table)
        if to_status == "active":
            # Billing is in advance: the first invoice is due on the start date.
            sub.next_invoice_date = sub.start_date
            sub.anchor_day = sub.start_date.day          # keeps Jan 31 -> Feb 28 -> Mar 31
            sub.save(update_fields=["next_invoice_date", "anchor_day"])
        elif to_status == "closed":
            sub.next_invoice_date = None
            sub.save(update_fields=["next_invoice_date"])
    return sub


@transaction.atomic
def toggle_pause(sub, actor):
    locked = Subscription.objects.select_for_update().select_related("plan").get(pk=sub.pk)
    if locked.status != Subscription.Status.ACTIVE:
        raise RuleViolation("Only active subscriptions can be paused.")
    if not locked.is_paused and not locked.plan.can_pause:
        raise RuleViolation(f"{locked.plan.name} does not allow pausing.")
    locked.is_paused = not locked.is_paused
    locked.save(update_fields=["is_paused"])
    Event.objects.create(subscription=locked, from_status="active", to_status="active",
                         actor=actor, note="Paused" if locked.is_paused else "Resumed")
    return locked


@transaction.atomic
def renew(sub, actor):
    """A closed subscription is terminal; renewal is a NEW draft at today's prices."""
    if sub.status != Subscription.Status.CLOSED:
        raise RuleViolation("Only closed subscriptions can be renewed.")
    if not sub.plan.can_renew:
        raise RuleViolation(f"{sub.plan.name} does not allow renewal.")
    new = create_subscription(
        customer=sub.customer, plan=sub.plan, start_date=clock.today(),
        payment_terms=sub.payment_terms, actor=actor, note=f"Renewal of {sub.number}")
    for line in sub.lines.select_related("product", "variant"):
        add_line(new, line.product, line.variant, line.quantity)
    return new
