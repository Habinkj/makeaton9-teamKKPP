from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect, render

from core.forms import SignupForm, StaffCreateForm
from core.roles import ADMIN, role_required, scoped_invoices
from django.contrib.auth.decorators import login_required

User = get_user_model()
BACKEND = "core.backends.EmailBackend"


def home(request):
    if not request.user.is_authenticated:
        return redirect("login")
    return render(request, "home.html")


def signup(request):
    if request.user.is_authenticated:
        return redirect("home")
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            user = form.save()
        except IntegrityError:  # two simultaneous signups with the same email
            form.add_error("email", "An account with this email already exists.")
        else:
            login(request, user, backend=BACKEND)
            messages.success(request, "Welcome to Brightloop!")
            return redirect("home")
    return render(request, "registration/signup.html", {"form": form})


@role_required(ADMIN)
def staff_list(request):
    staff = User.objects.filter(groups__name="staff").order_by("email")
    return render(request, "staff/list.html", {"staff": staff})


@role_required(ADMIN)
def staff_create(request):
    form = StaffCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            user = form.save()
        except IntegrityError:
            form.add_error("email", "An account with this email already exists.")
        else:
            messages.success(request, f"Staff account created for {user.email}.")
            return redirect("staff_list")
    return render(request, "staff/form.html", {"form": form})


@login_required
def invoice_list(request):
    invoices = scoped_invoices(request.user).order_by("-issue_date", "-id")[:100]
    return render(request, "invoices/list.html", {"invoices": invoices})


@login_required
def invoice_detail(request, pk):
    # Out-of-scope rows are not in the queryset -> 404, never 403.
    invoice = get_object_or_404(scoped_invoices(request.user), pk=pk)
    return render(request, "invoices/detail.html", {"invoice": invoice})
