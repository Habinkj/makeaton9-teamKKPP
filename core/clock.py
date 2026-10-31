"""Single source of 'today'. Everything date-dependent (billing due dates,
overdue, quotation expiry, dashboard) calls clock.today(), so the admin's
simulated date applies everywhere at once.

The simulation is stored as an OFFSET in days from the real date (in the DB, so
the web server and management commands agree). It only moves forward; reset
returns to the real date.
"""
from datetime import timedelta

from django.utils import timezone

KEY = "clock_offset_days"
MAX_OFFSET_DAYS = 3660          # ten years is plenty for a demo


def real_today():
    return timezone.localdate()


def offset_days() -> int:
    from core.models import Setting
    raw = Setting.objects.filter(key=KEY).values_list("value", flat=True).first()
    try:
        return int(raw) if raw is not None else 0
    except ValueError:
        return 0


def today():
    return real_today() + timedelta(days=offset_days())


def set_offset(days: int):
    from core.models import Setting
    if not 0 <= days <= MAX_OFFSET_DAYS:
        raise ValueError(f"The simulated date must be between today and {MAX_OFFSET_DAYS} days ahead.")
    if days == 0:
        Setting.objects.filter(key=KEY).delete()
    else:
        Setting.objects.update_or_create(key=KEY, defaults={"value": str(days)})


def advance(days: int):
    if days < 1:
        raise ValueError("You can only move the date forward.")
    set_offset(offset_days() + days)


def set_date(d):
    set_offset((d - real_today()).days)


def reset():
    set_offset(0)
