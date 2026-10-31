"""
Brightloop domain models.

Conventions (see CONTEXT.md):
- All money is integer paise (100 paise = 1 INR). Never floats.
- Tax rates are integer basis points (1800 = 18.00%).
- Statuses are CharFields with choices; allowed transitions live in core/statemachine.py.
- Invoice "overdue" is derived (due_date < today and not paid), never stored.
"""
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower


# ---------------------------------------------------------------------------
# Users and roles
# ---------------------------------------------------------------------------

class User(AbstractUser):
    """Login identity. Roles are Django Groups: 'admin', 'staff', 'customer'."""

    email = models.EmailField()

    class Meta:
        constraints = [
            # One account per email, case-insensitive (A@x.com == a@x.com).
            models.UniqueConstraint(Lower("email"), name="user_email_ci_unique"),
        ]

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()
        super().save(*args, **kwargs)

    def has_role(self, role: str) -> bool:
        return self.groups.filter(name=role).exists()

    @property
    def role(self) -> str:
        if self.is_superuser or self.has_role("admin"):
            return "admin"
        if self.has_role("staff"):
            return "staff"
        return "customer"


class Customer(models.Model):
    """Billing party. Linked to a User when the customer uses the portal."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="customer",
    )
    name = models.CharField(max_length=200)
    company = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    address = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.company or self.name


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

class TaxRule(models.Model):
    name = models.CharField(max_length=100)          # e.g. "GST 18%"
    rate_bps = models.PositiveIntegerField()         # 1800 = 18.00%
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Product(models.Model):
    class Type(models.TextChoices):
        SERVICE = "service", "Service"
        GOODS = "goods", "Goods"

    name = models.CharField(max_length=200)
    product_type = models.CharField(max_length=20, choices=Type.choices, default=Type.SERVICE)
    sales_price = models.PositiveIntegerField(help_text="paise, tax-exclusive")
    cost_price = models.PositiveIntegerField(default=0, help_text="paise")
    notes = models.TextField(blank=True)
    tax_rules = models.ManyToManyField(TaxRule, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class ProductVariant(models.Model):
    """e.g. attribute='Brand', value='Odoo', extra_price=56000 (₹560)."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    attribute = models.CharField(max_length=100)
    value = models.CharField(max_length=100)
    extra_price = models.PositiveIntegerField(default=0, help_text="paise")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product", "attribute", "value"], name="variant_unique_per_product"
            ),
        ]

    def __str__(self):
        return f"{self.product.name} ({self.attribute}: {self.value})"


class Plan(models.Model):
    class Period(models.TextChoices):
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"
        MONTHLY = "monthly", "Monthly"
        YEARLY = "yearly", "Yearly"

    name = models.CharField(max_length=200)
    price = models.PositiveIntegerField(help_text="paise per period, tax-exclusive")
    billing_period = models.CharField(max_length=10, choices=Period.choices)
    min_quantity = models.PositiveIntegerField(default=1)
    available_from = models.DateField()
    available_until = models.DateField(null=True, blank=True)
    # Per-plan behaviour flags, decided by the admin.
    auto_close = models.BooleanField(default=False)
    can_close_early = models.BooleanField(default=True)
    can_pause = models.BooleanField(default=False)
    can_renew = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(min_quantity__gte=1), name="plan_min_quantity_positive"
            ),
            models.CheckConstraint(
                condition=Q(available_until__isnull=True)
                | Q(available_until__gte=F("available_from")),
                name="plan_dates_ordered",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_billing_period_display()})"


class Discount(models.Model):
    """Admin-only. Best single discount per line, max one at subscription level."""

    class Kind(models.TextChoices):
        PERCENT = "percent", "Percentage"
        FIXED = "fixed", "Fixed amount"

    class Scope(models.TextChoices):
        PRODUCT = "product", "Specific products"
        SUBSCRIPTION = "subscription", "Whole subscription"

    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    value = models.PositiveIntegerField(help_text="bps if percent (1000 = 10%), paise if fixed")
    scope = models.CharField(max_length=20, choices=Scope.choices)
    products = models.ManyToManyField(Product, blank=True, related_name="discounts")
    min_purchase = models.PositiveIntegerField(default=0, help_text="paise")
    min_quantity = models.PositiveIntegerField(default=0)
    starts_on = models.DateField()
    ends_on = models.DateField(null=True, blank=True)
    usage_limit = models.PositiveIntegerField(null=True, blank=True, help_text="blank = unlimited")
    used_count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(usage_limit__isnull=True) | Q(used_count__lte=F("usage_limit")),
                name="discount_usage_within_limit",
            ),
            models.CheckConstraint(
                condition=~Q(kind="percent") | Q(value__lte=10000),
                name="discount_percent_max_100",
            ),
        ]

    def __str__(self):
        return self.name


class QuotationTemplate(models.Model):
    name = models.CharField(max_length=200)
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT)
    validity_days = models.PositiveIntegerField(default=30)

    def __str__(self):
        return self.name


class QuotationTemplateLine(models.Model):
    template = models.ForeignKey(QuotationTemplate, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    variant = models.ForeignKey(ProductVariant, null=True, blank=True, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------

class PaymentTerms(models.TextChoices):
    IMMEDIATE = "immediate", "Immediate"
    NET_15 = "net15", "Net 15"
    NET_30 = "net30", "Net 30"


class Subscription(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        QUOTATION = "quotation", "Quotation"
        CONFIRMED = "confirmed", "Confirmed"
        ACTIVE = "active", "Active"
        CLOSED = "closed", "Closed"

    number = models.CharField(max_length=20, unique=True)           # e.g. SUB-000123
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="subscriptions")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    start_date = models.DateField()
    expiry_date = models.DateField(null=True, blank=True)
    payment_terms = models.CharField(
        max_length=20, choices=PaymentTerms.choices, default=PaymentTerms.IMMEDIATE
    )
    # Billing schedule. anchor_day keeps Jan 31 -> Feb 28 -> Mar 31 correct.
    next_invoice_date = models.DateField(null=True, blank=True)
    anchor_day = models.PositiveSmallIntegerField(null=True, blank=True)
    is_paused = models.BooleanField(default=False)
    # At most one subscription-level discount (no stacking).
    discount = models.ForeignKey(
        Discount, null=True, blank=True, on_delete=models.SET_NULL,
        limit_choices_to={"scope": "subscription"}, related_name="subscriptions",
    )
    quotation_valid_until = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            # Billing run: "which active subscriptions are due?"
            models.Index(fields=["status", "next_invoice_date"], name="sub_due_idx"),
            models.Index(fields=["customer", "status"], name="sub_customer_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(expiry_date__isnull=True) | Q(expiry_date__gte=F("start_date")),
                name="sub_dates_ordered",
            ),
        ]

    def __str__(self):
        return self.number


class SubscriptionLine(models.Model):
    """Prices and tax rate are snapshots, so later catalog edits don't rewrite history."""

    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    variant = models.ForeignKey(ProductVariant, null=True, blank=True, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.PositiveIntegerField(help_text="paise snapshot incl. variant extra")
    tax_rate_bps = models.PositiveIntegerField(default=0, help_text="snapshot")

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gte=1), name="subline_qty_positive"),
        ]


# ---------------------------------------------------------------------------
# Invoices and payments
# ---------------------------------------------------------------------------

class Invoice(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        CONFIRMED = "confirmed", "Confirmed"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"

    number = models.CharField(max_length=20, unique=True)            # e.g. INV-000456
    subscription = models.ForeignKey(Subscription, on_delete=models.PROTECT, related_name="invoices")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="invoices")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    period_start = models.DateField()
    period_end = models.DateField()                                   # exclusive: [start, end)
    issue_date = models.DateField()
    due_date = models.DateField()
    subtotal = models.PositiveIntegerField(default=0)
    discount_total = models.PositiveIntegerField(default=0)
    tax_total = models.PositiveIntegerField(default=0)
    total = models.PositiveIntegerField(default=0)
    discount_applied = models.ForeignKey(
        Discount, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    calc_trace = models.JSONField(default=dict, blank=True)           # how every number was derived
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # Idempotency: one live invoice per subscription per period.
            # Cancelled invoices don't block re-billing that period.
            models.UniqueConstraint(
                fields=["subscription", "period_start"],
                condition=~Q(status="cancelled"),
                name="invoice_one_per_period",
            ),
            models.CheckConstraint(
                condition=Q(period_end__gt=F("period_start")), name="invoice_period_valid"
            ),
            models.CheckConstraint(
                condition=Q(due_date__gte=F("issue_date")), name="invoice_due_after_issue"
            ),
        ]
        indexes = [
            models.Index(fields=["status", "due_date"], name="inv_status_due_idx"),
            models.Index(fields=["customer", "status"], name="inv_customer_idx"),
            models.Index(fields=["issue_date"], name="inv_issue_idx"),
        ]

    def __str__(self):
        return self.number


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, null=True, on_delete=models.SET_NULL)
    description = models.CharField(max_length=300)
    quantity = models.PositiveIntegerField()
    unit_price = models.PositiveIntegerField()
    discount_amount = models.PositiveIntegerField(default=0)
    tax_rate_bps = models.PositiveIntegerField(default=0)
    tax_amount = models.PositiveIntegerField(default=0)
    line_total = models.PositiveIntegerField()                        # after discount + tax


class Payment(models.Model):
    class Method(models.TextChoices):
        UPI = "upi", "UPI"
        CARD = "card", "Card"
        BANK = "bank", "Bank transfer"
        CASH = "cash", "Cash"
        CHEQUE = "cheque", "Cheque"

    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="payments")
    method = models.CharField(max_length=10, choices=Method.choices)
    amount = models.PositiveIntegerField(help_text="paise")
    paid_on = models.DateField()
    note = models.CharField(max_length=300, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="payment_amount_positive"),
        ]
        indexes = [models.Index(fields=["paid_on"], name="pay_paid_on_idx")]


# ---------------------------------------------------------------------------
# Audit trail (the timeline on detail pages)
# ---------------------------------------------------------------------------

class Event(models.Model):
    """Who moved what, from which state to which, and when."""

    subscription = models.ForeignKey(
        Subscription, null=True, blank=True, on_delete=models.CASCADE, related_name="events"
    )
    invoice = models.ForeignKey(
        Invoice, null=True, blank=True, on_delete=models.CASCADE, related_name="events"
    )
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(subscription__isnull=False) | Q(invoice__isnull=False),
                name="event_has_target",
            ),
        ]