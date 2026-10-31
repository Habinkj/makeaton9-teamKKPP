from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction

from core.models import Customer

User = get_user_model()
EMAIL_MAX = 150  # username (= email) column is 150 chars


class EmailAuthenticationForm(AuthenticationForm):
    """Email + password. The field is a CharField (not EmailField) so that a
    createsuperuser account without an email can still log in by username."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["username"] = forms.CharField(
            label="Email", max_length=254,
            widget=forms.TextInput(attrs={"autofocus": True, "class": "input input-bordered w-full"}),
        )
        self.fields["password"].widget.attrs["class"] = "input input-bordered w-full"


class _AccountForm(forms.Form):
    """Shared email + password handling for signup and admin-created staff."""
    name = forms.CharField(max_length=150)
    email = forms.EmailField(max_length=EMAIL_MAX)
    password1 = forms.CharField(label="Password", strip=False, widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirm password", strip=False, widget=forms.PasswordInput)

    group_name = None  # set by subclasses

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for f in self.fields.values():
            f.widget.attrs.setdefault("class", "input input-bordered w-full")

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("An account with this email already exists.")
        return email

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "The two passwords don't match.")
        elif p1:
            probe = User(username=data.get("email", ""), email=data.get("email", ""),
                         first_name=data.get("name", ""))
            try:
                validate_password(p1, user=probe)
            except ValidationError as e:
                self.add_error("password1", e)
        return data

    def _create_user(self):
        d = self.cleaned_data
        user = User(username=d["email"], email=d["email"], first_name=d["name"])
        user.set_password(d["password1"])
        user.save()
        group, _ = Group.objects.get_or_create(name=self.group_name)
        user.groups.add(group)
        return user


class SignupForm(_AccountForm):
    company = forms.CharField(max_length=200, required=False)
    group_name = "customer"

    @transaction.atomic
    def save(self):
        user = self._create_user()
        Customer.objects.create(
            user=user, name=self.cleaned_data["name"], company=self.cleaned_data["company"])
        return user


class StaffCreateForm(_AccountForm):
    group_name = "staff"

    @transaction.atomic
    def save(self):
        return self._create_user()
