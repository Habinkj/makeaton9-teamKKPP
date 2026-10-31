from django import template

from core.formatting import format_bps, format_rupees

register = template.Library()
register.filter("rupees", format_rupees)
register.filter("bps", format_bps)
