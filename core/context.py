from datetime import timedelta


def role(request):
    """Feeds base.html: role-aware nav and the simulated-date banner."""
    from core import clock
    user = getattr(request, "user", None)
    signed_in = user is not None and user.is_authenticated
    days = clock.offset_days() if signed_in else 0
    return {
        "user_role": user.role if signed_in else "",
        "sim_days": days,
        "sim_today": clock.real_today() + timedelta(days=days),
    }
