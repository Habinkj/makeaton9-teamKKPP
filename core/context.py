def role(request):
    """Feeds the role-aware nav in base.html (replaces the hardcoded stub)."""
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        return {"user_role": user.role}
    return {"user_role": ""}
