"""Generate a realistic dataset for the performance proof and the demo.

    python manage.py seed_demo                  # 10,000 subscriptions (~50k invoices, ~35k payments)
    python manage.py seed_demo --subs 200       # quick smoke test
    python manage.py seed_demo --reset          # remove everything this command created

Everything is generated with bulk_create (a handful of queries per 2,000 rows) and
passes through the same compute() as real billing, so every invariant holds.
Seeded customers carry address="Seeded demo data" so --reset removes only them.
"""
import random
import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.db.models import F

from core import clock
from core.billing import TERMS_DAYS
from core.billing_calc import DiscountIn, LineIn, compute
from core.models import (Customer, Discount, Event, Invoice, InvoiceLine, Payment, Plan, Product,
                         Subscription, SubscriptionLine, TaxRule)
from core.periods import next_period_start

SEED_TAG = "Seeded demo data"
BATCH = 2000

try:
    from seed_data.names import COMPANY_SUFFIXES, FIRST_NAMES, LAST_NAMES   # Karan's file
    assert FIRST_NAMES and LAST_NAMES and COMPANY_SUFFIXES
except Exception:                                                           # fallback keeps seeding working
    FIRST_NAMES = ["Aarav", "Vivaan", "Aditya", "Ishaan", "Rohan", "Kabir", "Arjun", "Ananya", "Diya",
                   "Meera", "Priya", "Sneha", "Kavya", "Nikhil", "Rahul", "Sanjay", "Neha", "Pooja"]
    LAST_NAMES = ["Sharma", "Nair", "Menon", "Iyer", "Patel", "Reddy", "Gupta", "Kumar", "Das", "Joshi",
                  "Pillai", "Verma", "Singh", "Rao", "Mehta", "Bose"]
    COMPANY_SUFFIXES = ["Technologies", "Solutions", "Industries", "Traders", "Labs", "Exports",
                        "Systems", "Foods"]

PRODUCTS = [  # name, price (paise), tax %
    ("Demo CRM Seat", 56000, 18), ("Demo Helpdesk Seat", 42000, 18), ("Demo Analytics Add-on", 120000, 18),
    ("Demo Storage 1TB", 30000, 5), ("Demo Support Plus", 90000, 18), ("Demo API Access", 75000, 18),
    ("Demo Mobile App", 25000, 5), ("Demo Onboarding", 150000, 18),
]
MONTHLY_PLANS = ["Demo Starter Monthly", "Demo Pro Monthly", "Demo Business Monthly"]


def ensure_catalog():
    rates = {}
    for pct in (18, 5):
        rule, _ = TaxRule.objects.get_or_create(name=f"Demo GST {pct}%", defaults={"rate_bps": pct * 100})
        rates[pct] = rule
    products = []
    for name, price, pct in PRODUCTS:
        p, created = Product.objects.get_or_create(
            name=name, defaults={"sales_price": price, "cost_price": price // 3, "product_type": "service"})
        if created:
            p.tax_rules.add(rates[pct])
        products.append(p)
    start = clock.today() - timedelta(days=700)
    monthly = [Plan.objects.get_or_create(name=n, defaults={
        "price": 0, "billing_period": "monthly", "min_quantity": 1, "available_from": start})[0]
        for n in MONTHLY_PLANS]
    yearly = Plan.objects.get_or_create(name="Demo Business Yearly", defaults={
        "price": 0, "billing_period": "yearly", "min_quantity": 1, "available_from": start})[0]
    loyalty, _ = Discount.objects.get_or_create(name="Demo Loyalty 10%", defaults={
        "kind": "percent", "value": 1000, "scope": "subscription", "starts_on": start})
    storage, c = Discount.objects.get_or_create(name="Demo Storage 15%", defaults={
        "kind": "percent", "value": 1500, "scope": "product", "starts_on": start})
    if c:
        storage.products.add(next(p for p in products if p.name == "Demo Storage 1TB"))
    return products, monthly, yearly, loyalty, storage


class Command(BaseCommand):
    help = "Seed demo data (bulk) or --reset it."

    def add_arguments(self, parser):
        parser.add_argument("--subs", type=int, default=10000)
        parser.add_argument("--customers", type=int, default=None, help="default: subs / 4")
        parser.add_argument("--seed", type=int, default=42, help="random seed (same seed = same data)")
        parser.add_argument("--reset", action="store_true")

    # ------------------------------------------------------------------
    def handle(self, *args, subs, customers, seed, reset, **opts):
        t0 = time.time()
        if reset:
            self._reset()
            self.stdout.write(self.style.SUCCESS("Seeded data removed."))
            return
        with transaction.atomic():
            counts = self._seed(subs, customers or max(1, subs // 4), random.Random(seed))
        with connection.cursor() as cur:
            cur.execute("ANALYZE")                 # fresh planner statistics for the timing proof
        self.stdout.write(self.style.SUCCESS(
            "Seeded " + ", ".join(f"{v:,} {k}" for k, v in counts.items()) + f" in {time.time() - t0:.1f}s"))

    # ------------------------------------------------------------------
    def _seed(self, n_subs, n_customers, rng):
        today = clock.today()
        products, monthly, yearly, loyalty, storage = ensure_catalog()
        rate = {p.pk: sum(r.rate_bps for r in p.tax_rules.all()) for p in products}
        prod_discs = [DiscountIn(storage.pk, storage.name, storage.kind, storage.value,
                                 frozenset(p.pk for p in storage.products.all()))]
        loyalty_in = DiscountIn(loyalty.pk, loyalty.name, loyalty.kind, loyalty.value)

        customers = Customer.objects.bulk_create([
            Customer(name=f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}",
                     company=f"{rng.choice(LAST_NAMES)} {rng.choice(COMPANY_SUFFIXES)}",
                     phone=f"+91-9{rng.randint(100000000, 999999999)}", address=SEED_TAG)
            for _ in range(n_customers)], batch_size=BATCH)

        sub_objs, plans = [], []
        for i in range(n_subs):
            r = rng.random()
            status = ("active" if r < 0.72 else "closed" if r < 0.87 else "confirmed" if r < 0.92
                      else "quotation" if r < 0.97 else "draft")
            plan = yearly if rng.random() < 0.10 else rng.choice(monthly)
            billed = status in ("active", "closed")
            start = today - timedelta(days=rng.randint(0, 330 if billed else 20))
            expiry = starts = next_due = None
            starts = []
            if billed:
                limit = today
                if status == "closed":
                    expiry = limit = min(today, start + timedelta(days=rng.randint(30, 300)))
                s = start
                while s <= limit:
                    starts.append(s)
                    s = next_period_start(s, plan.billing_period, start.day)
                next_due = s if status == "active" else None
            chosen = rng.sample(products, rng.choice([1, 1, 2, 3]))
            lines = [(p, rng.randint(1, 20)) for p in chosen]
            use_loyalty = rng.random() < 0.10
            result = compute([LineIn(0, p.pk, p.name, q, p.sales_price, rate[p.pk]) for p, q in lines],
                             prod_discs, loyalty_in if use_loyalty else None)
            sub_objs.append(Subscription(
                number=f"SEED-S-{i}", customer=customers[i % n_customers], plan=plan, status=status,
                start_date=start, expiry_date=expiry, payment_terms=rng.choice(list(TERMS_DAYS)),
                next_invoice_date=next_due, anchor_day=start.day,
                is_paused=(status == "active" and rng.random() < 0.02),
                discount=loyalty if use_loyalty else None,
                quotation_valid_until=today + timedelta(days=30)))
            plans.append((starts, result, lines))
        Subscription.objects.bulk_create(sub_objs, batch_size=BATCH)

        SubscriptionLine.objects.bulk_create([
            SubscriptionLine(subscription=s, product=p, quantity=q, unit_price=p.sales_price,
                             tax_rate_bps=rate[p.pk])
            for s, (_, _, lines) in zip(sub_objs, plans) for p, q in lines], batch_size=BATCH)
        Event.objects.bulk_create([
            Event(subscription=s, from_status="", to_status=s.status, note=SEED_TAG) for s in sub_objs],
            batch_size=BATCH)

        inv_objs, pay_specs, usage = [], [], {}
        for s, (starts, result, _) in zip(sub_objs, plans):
            for ps in starts:
                age = (today - ps).days
                r = rng.random()
                if age > 45:
                    st = "paid" if r < 0.66 else "partial" if r < 0.71 else "cancelled" if r < 0.76 else "confirmed"
                else:
                    st = "paid" if r < 0.35 else "partial" if r < 0.40 else "confirmed" if r < 0.75 else "draft"
                if st == "partial" and result.total < 2:
                    st = "paid"
                status = "confirmed" if st == "partial" else st
                terms = TERMS_DAYS[s.payment_terms]
                used = [] if status == "cancelled" else result.discount_ids
                for d in used:
                    usage[d] = usage.get(d, 0) + 1
                inv_objs.append(Invoice(
                    number=f"SEED-I-{len(inv_objs)}", subscription=s, customer=s.customer, status=status,
                    period_start=ps, period_end=next_period_start(ps, s.plan.billing_period, s.anchor_day),
                    issue_date=ps, due_date=ps + timedelta(days=terms), subtotal=result.subtotal,
                    discount_total=result.discount_total, tax_total=result.tax_total, total=result.total,
                    discount_applied_id=result.primary_discount_id,
                    calc_trace={"seeded": True, "chosen": result.trace["chosen"],
                                "discounts_used": used, "total": result.total}))
                if st in ("paid", "partial") and result.total > 0:
                    amount = result.total if st == "paid" else max(1, result.total // 2)
                    pay_specs.append((len(inv_objs) - 1, amount,
                                      min(today, ps + timedelta(days=rng.randint(0, terms + 10))),
                                      rng.choice(["upi", "bank", "card", "cash", "cheque"])))
        Invoice.objects.bulk_create(inv_objs, batch_size=BATCH)

        InvoiceLine.objects.bulk_create([
            InvoiceLine(invoice=inv, product_id=o.product_id, description=o.description,
                        quantity=o.quantity, unit_price=o.unit_price, discount_amount=o.discount_amount,
                        tax_rate_bps=o.tax_rate_bps, tax_amount=o.tax_amount, line_total=o.line_total)
            for inv, result in self._invoice_results(inv_objs, sub_objs, plans) for o in result.lines],
            batch_size=BATCH)
        Payment.objects.bulk_create([
            Payment(invoice=inv_objs[idx], method=method, amount=amount, paid_on=paid_on, note="Seeded")
            for idx, amount, paid_on, method in pay_specs], batch_size=BATCH)

        for pk, count in usage.items():
            Discount.objects.filter(pk=pk).update(used_count=F("used_count") + count)

        # Real numbers: SUB-000123 / INV-000456, same convention as the app (number = prefix + pk).
        with connection.cursor() as cur:
            for model, prefix in ((Subscription, "SUB"), (Invoice, "INV")):
                t = model._meta.db_table
                cur.execute(f"UPDATE {t} SET number = '{prefix}-' || LPAD(id::text, 6, '0') "
                            f"WHERE number LIKE 'SEED-%'")
        return {"customers": len(customers), "subscriptions": len(sub_objs), "invoices": len(inv_objs),
                "payments": len(pay_specs)}

    @staticmethod
    def _invoice_results(inv_objs, sub_objs, plans):
        """Pair every invoice with its subscription's computed result (same order as creation)."""
        it = iter(inv_objs)
        for _, (starts, result, _) in zip(sub_objs, plans):
            for _ in starts:
                yield next(it), result

    def _reset(self):
        q = (Payment, Invoice, InvoiceLine, Event, SubscriptionLine, Subscription, Customer)
        t = {m.__name__: m._meta.db_table for m in q}
        seeded_cust = f"(SELECT id FROM {t['Customer']} WHERE address = %s)"
        seeded_inv = f"(SELECT id FROM {t['Invoice']} WHERE customer_id IN {seeded_cust})"
        seeded_sub = f"(SELECT id FROM {t['Subscription']} WHERE customer_id IN {seeded_cust})"
        steps = [
            f"DELETE FROM {t['Payment']} WHERE invoice_id IN {seeded_inv}",
            f"DELETE FROM {t['Event']} WHERE invoice_id IN {seeded_inv} OR subscription_id IN {seeded_sub}",
            f"DELETE FROM {t['InvoiceLine']} WHERE invoice_id IN {seeded_inv}",
            f"DELETE FROM {t['Invoice']} WHERE customer_id IN {seeded_cust}",
            f"DELETE FROM {t['SubscriptionLine']} WHERE subscription_id IN {seeded_sub}",
            f"DELETE FROM {t['Subscription']} WHERE customer_id IN {seeded_cust}",
            f"DELETE FROM {t['Customer']} WHERE address = %s",
        ]
        with transaction.atomic(), connection.cursor() as cur:
            for sql in steps:
                cur.execute(sql, [SEED_TAG] * sql.count("%s"))
        Discount.objects.filter(name__startswith="Demo ").update(used_count=0)
