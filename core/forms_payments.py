from decimal import Decimal

from django import forms

from core import clock
from core.formatting import to_minor
from core.models import Payment


class PaymentForm(forms.Form):
    amount = forms.DecimalField(label="Amount (₹)", min_value=Decimal("0.01"),
                                max_digits=12, decimal_places=2)
    method = forms.ChoiceField(choices=Payment.Method.choices)
    paid_on = forms.DateField(label="Paid on",
                              widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    note = forms.CharField(max_length=300, required=False)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["paid_on"].initial = clock.today()
        for f in self.fields.values():
            f.widget.attrs["class"] = ("select select-bordered w-full"
                                       if isinstance(f.widget, forms.Select)
                                       else "input input-bordered w-full")

    def clean_paid_on(self):
        d = self.cleaned_data["paid_on"]
        if d > clock.today():
            raise forms.ValidationError("A payment can't be dated in the future.")
        return d

    def amount_minor(self):
        return to_minor(self.cleaned_data["amount"])
