from django import template

register = template.Library()


@register.filter
def rupees(paise):
    """Integer paise -> '₹1,234.56'. Display only; never used for arithmetic."""
    try:
        paise = int(paise)
    except (TypeError, ValueError):
        return ""
    sign = "-" if paise < 0 else ""
    rupees_part, p = divmod(abs(paise), 100)
    return f"{sign}₹{rupees_part:,}.{p:02d}"
