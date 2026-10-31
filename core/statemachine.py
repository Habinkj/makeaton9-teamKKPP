"""
Explicit, inspectable state machines for Subscription and Invoice.

The tables are plain data so they can be printed in the README, asserted in
tests, and checked by reviewers without reading any view code.
Every status change in the app goes through `apply_transition`.
"""
from django.db import transaction

SUBSCRIPTION_TRANSITIONS = {
    "draft":     {"quotation", "closed"},
    "quotation": {"confirmed", "draft", "closed"},
    "confirmed": {"active", "closed"},
    "active":    {"closed"},
    "closed":    set(),            # terminal; renewal creates a new subscription
}

INVOICE_TRANSITIONS = {
    "draft":     {"confirmed", "cancelled"},
    "confirmed": {"paid", "cancelled"},   # paid is set by payment recording, not by hand
    "paid":      set(),                   # terminal
    "cancelled": set(),                   # terminal; releases discount use
}

TABLES = {
    "Subscription": SUBSCRIPTION_TRANSITIONS,
    "Invoice": INVOICE_TRANSITIONS,
}


class InvalidTransition(Exception):
    pass


def can_transition(obj, to_status: str) -> bool:
    table = TABLES[type(obj).__name__]
    return to_status in table.get(obj.status, set())


def allowed_next(obj) -> list[str]:
    """For the UI: which buttons to show on a detail page."""
    return sorted(TABLES[type(obj).__name__].get(obj.status, set()))


def apply_transition(obj, to_status: str, actor=None, note: str = ""):
    """
    Lock the row, validate against the table, save, and log an Event.
    Locking prevents two staff clicking 'Confirm' at once from double-applying.
    """
    from core.models import Event, Invoice, Subscription  # avoid circular import

    model = type(obj)
    with transaction.atomic():
        locked = model.objects.select_for_update().get(pk=obj.pk)
        if not can_transition(locked, to_status):
            raise InvalidTransition(
                f"{model.__name__} {locked} cannot go {locked.status} -> {to_status}"
            )
        from_status = locked.status
        locked.status = to_status
        locked.save(update_fields=["status"])
        Event.objects.create(
            subscription=locked if isinstance(locked, Subscription) else None,
            invoice=locked if isinstance(locked, Invoice) else None,
            from_status=from_status,
            to_status=to_status,
            actor=actor,
            note=note,
        )
    obj.status = to_status
    return obj