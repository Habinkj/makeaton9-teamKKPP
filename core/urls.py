from django.contrib.auth import views as auth_views
from django.urls import path

from core import views_auth as v
from core import views_catalog as c
from core import views_subscriptions as s
from core import views_billing as b
from core import views_dashboard as d
from core import views_clock as k
from core import views_health as h
from core.forms import EmailAuthenticationForm

urlpatterns = [
    path("", d.home, name="home"),
    path("dashboard/", d.dashboard, name="dashboard"),
    path("clock/", k.clock_control, name="clock_control"),
    path("health/", h.health_check, name="health"),
    path("signup/", v.signup, name="signup"),
    path("login/", auth_views.LoginView.as_view(
        authentication_form=EmailAuthenticationForm,
        redirect_authenticated_user=True), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),  # POST only (Django 5)

    path("password-reset/", auth_views.PasswordResetView.as_view(), name="password_reset"),
    path("password-reset/sent/", auth_views.PasswordResetDoneView.as_view(),
         name="password_reset_done"),
    path("reset/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(),
         name="password_reset_confirm"),
    path("reset/done/", auth_views.PasswordResetCompleteView.as_view(),
         name="password_reset_complete"),

    path("staff/", v.staff_list, name="staff_list"),
    path("staff/new/", v.staff_create, name="staff_create"),

    path("catalog/templates/<int:pk>/lines/", s.template_lines, name="template_lines"),
    path("catalog/templates/<int:pk>/lines/<int:line_pk>/delete/", s.template_line_delete,
         name="template_line_delete"),
    path("catalog/<slug:key>/", c.catalog_list, name="catalog_list"),
    path("catalog/<slug:key>/new/", c.catalog_edit, name="catalog_new"),
    path("catalog/<slug:key>/<int:pk>/", c.catalog_edit, name="catalog_edit"),

    path("subscriptions/", s.subscription_list, name="subscription_list"),
    path("subscriptions/new/", s.subscription_create, name="subscription_create"),
    path("subscriptions/<int:pk>/", s.subscription_detail, name="subscription_detail"),
    path("subscriptions/<int:pk>/transition/", s.subscription_transition, name="subscription_transition"),
    path("subscriptions/<int:pk>/lines/add/", s.subscription_line_add, name="subscription_line_add"),
    path("subscriptions/<int:pk>/lines/<int:line_pk>/delete/", s.subscription_line_delete,
         name="subscription_line_delete"),
    path("subscriptions/<int:pk>/pause/", s.subscription_pause, name="subscription_pause"),
    path("subscriptions/<int:pk>/renew/", s.subscription_renew, name="subscription_renew"),

    path("invoices/", b.invoice_list, name="invoice_list"),
    path("invoices/<int:pk>/", b.invoice_detail, name="invoice_detail"),
    path("invoices/<int:pk>/print/", b.invoice_print, name="invoice_print"),
    path("invoices/<int:pk>/payment/", b.invoice_payment, name="invoice_payment"),
    path("invoices/<int:pk>/<slug:action>/", b.invoice_action, name="invoice_action"),
    path("billing/", b.billing_home, name="billing_home"),
]
