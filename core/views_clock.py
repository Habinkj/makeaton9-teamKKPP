from datetime import date

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core import clock
from core.roles import ADMIN, role_required


@role_required(ADMIN)
@require_POST
def clock_control(request):
    action = request.POST.get("action", "")
    try:
        if action == "advance":
            clock.advance(int(request.POST.get("days", "0")))
        elif action == "set":
            clock.set_date(date.fromisoformat(request.POST.get("date", "")))
        elif action == "reset":
            clock.reset()
        else:
            raise ValueError("Unknown action.")
    except ValueError as e:
        messages.error(request, f"Date not changed: {e}")
    else:
        days = clock.offset_days()
        messages.success(request, f"Simulated date is now {clock.today():%d %b %Y}"
                         + (f" (+{days} days)." if days else " (the real date)."))
    nxt = request.POST.get("next") or reverse("dashboard")
    if not url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        nxt = reverse("dashboard")
    return redirect(nxt)
