"""Single source of 'today'. Everything that depends on the date calls
clock.today(), so the billing block can add a simulated-date offset in one place."""
from django.utils import timezone


def today():
    return timezone.localdate()
