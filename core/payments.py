"""Recording payments. An invoice becomes Paid exactly when payments >= total;
overdue is derived (confirmed + past due date), never stored."""
from django.db import transaction

from core.billing import BillingError, amount_paid
from core.formatting import format_rupees
from core.models import Event, Invoice, Payment
from core.statemachine import apply_transition


@transaction.atomic
def record_payment(invoice, *, amount, method, paid_on, note="", actor=None):
    # Lock the invoice row so two simultaneous payments can't both pass the balance check.
    locked = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if locked.status != "confirmed":
        raise BillingError("Payments can only be recorded against confirmed invoices.")
    if amount <= 0:
        raise BillingError("Payment amount must be greater than zero.")
    paid_before = amount_paid(locked)
    balance = locked.total - paid_before
    if amount > balance:
        raise BillingError(f"That is more than the balance due ({format_rupees(balance)}).")
    payment = Payment.objects.create(invoice=locked, method=method, amount=amount,
                                     paid_on=paid_on, note=note, recorded_by=actor)
    if paid_before + amount >= locked.total:
        apply_transition(locked, "paid", actor=actor,
                         note=f"Paid in full ({format_rupees(amount)} by {payment.get_method_display()})")
    else:
        Event.objects.create(
            invoice=locked, from_status="confirmed", to_status="confirmed", actor=actor,
            note=f"Partial payment {format_rupees(amount)}; balance {format_rupees(balance - amount)}")
    invoice.status = locked.status
    return payment
