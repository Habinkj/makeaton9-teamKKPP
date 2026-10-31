# Brightloop Subscription Management System

Odoo x Make-A-Ton 9.0 (virtual round). Team KKPP: Habin K J, Karan Reju.

One system for Brightloop Software to run products, plans, subscriptions, recurring billing, payments and revenue reporting, replacing spreadsheets and hand-typed invoices.

**Stack:** Django 5.1, PostgreSQL 16, Docker Compose, server-rendered templates with Tailwind/daisyUI and HTMX, Chart.js, Mailpit for email.

## Quick start

Requires Docker Desktop.

```bash
git clone https://github.com/Habinkj/makeaton9-teamKKPP.git
cd makeaton9-teamKKPP
docker compose up -d
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py createsuperuser
```

- App: http://localhost:8000
- Mailpit (password resets, invoice emails): http://localhost:8025

Optional demo data, generated through the real billing calculator (about 20 s):

```bash
docker compose run --rm web python manage.py seed_demo --subs 10000 --seed 42
```

Run the tests:

```bash
docker compose run --rm web python manage.py test core
```

## What it does

| Scene from the brief | Where it lives |
|---|---|
| Sign-up with unique email, strong password, reset by email | `/signup/`, `/password-reset/` (mail arrives in Mailpit) |
| Admin, staff and customer roles; only admin adds staff | Django Groups; `core/roles.py` (`role_required` returns 403, `scoped_*` querysets return 404 for other customers' rows) |
| Products, variants, plans, taxes, discounts, quotation templates | `/catalog/...` (admin edits, staff reads) |
| Quotation to subscription lifecycle | `/subscriptions/` with a timeline of every state change |
| Recurring invoices | `/billing/`: preview, run, run again safely; also `manage.py run_billing` |
| Payments, partial payments, overdue | `/invoices/`: record payment, balance, overdue filter |
| Revenue reporting | `/` dashboard: active subscriptions, MRR, collected, outstanding, overdue, charts |
| Testing future months without waiting | Admin simulated clock (+1/+7/+30 days or pick a date); a yellow banner shows when it is active |

## Demo walkthrough

1. Log in as the superuser. Create a tax, a product and a plan under Catalog.
2. Create a subscription, add a line, move it quotation → confirmed → active.
3. Open Billing: preview shows what is due, Run creates the invoice, Run again creates nothing.
4. Confirm and send the invoice (the email appears in Mailpit), then record a partial payment.
5. Advance the simulated clock past the due date: the invoice becomes overdue and appears on the dashboard.

## Design decisions

- **Money is integers.** Amounts are stored in paise and tax rates in basis points (1800 = 18%). No floating point anywhere in billing.
- **Billing is idempotent.** A partial unique constraint (`invoice_one_per_period`) allows one live invoice per subscription per period. Billing locks subscriptions with `select_for_update(skip_locked=True)`, so two concurrent runs never bill the same subscription twice. Running billing again is always safe.
- **Billing logic is pure.** `core/billing_calc.py` and `core/periods.py` have no database access, so the money rules are unit-tested directly. Every invoice stores a calculation trace.
- **Overdue is derived, not stored.** An invoice is overdue when it is confirmed, unpaid and `due_date < today`. No nightly job is needed to keep it correct.
- **State machines are data.** Allowed transitions are tables checked inside a row lock, and every change writes an Event for the timeline.
- **Invariant checks.** `core/invariants.py` checks that totals add up, invoices equal the sum of their lines, payment status is consistent, there are no duplicate periods, and schedules stay ahead of invoices. The seed is verified against them.

## State machines

### Subscription

| From | Allowed next |
|---|---|
| draft | quotation, closed |
| quotation | confirmed, draft, closed |
| confirmed | active, closed |
| active | closed |
| closed | none (renewal creates a new subscription) |

### Invoice

| From | Allowed next |
|---|---|
| draft | confirmed, cancelled |
| confirmed | paid, cancelled |
| paid | none |
| cancelled | none |

## Assumptions

- Money is stored as integer paise and tax rates as basis points; no floating point.
- Proration, dunning and grace periods are not implemented.
- Plan minimum quantity is the total quantity across subscription lines.
- Customers can close early only if the plan allows it; staff can always close.
- A renewal is a new subscription at current prices.
- There is no Paused state; pausing is a flag on an active subscription.
- Billing is in advance. A period runs from its start date up to (not including) the next start date.
- Month-end start dates keep their anchor day: 31 Jan → 28 Feb → 31 Mar.
- Discount is applied before tax. Tax is calculated per line and rounded half-up once.
- Discounts never stack. The system compares the best product discount on each line with one subscription-level discount on the whole invoice, and uses whichever saves the customer more (tie: per-line).
- A fixed subscription discount is split across lines by the largest-remainder method, so the lines add up to the exact total.
- Payment terms are Immediate, Net 15 or Net 30.
- An invoice becomes Paid when payments reach its total. Overpayments, zero amounts and future-dated payments are refused.
- Only unpaid draft or confirmed invoices can be cancelled; cancelling releases the discount use. Cancelled invoices are not regenerated automatically.
- Catch-up billing creates at most 60 periods per subscription per run.
- Product-level discounts are found and applied automatically by the billing run. A subscription-level discount is attached to the subscription by staff at quote time; once attached, it applies on every invoice until it expires or runs out.

## Performance

`docker compose run --rm web python manage.py measure_p95` logs in as an admin, warms each page up, times 50 requests per page with `DEBUG=False`, and writes `docs/performance.md`.

Measured on 10,000 subscriptions and about 44,500 invoices (seed 42):

| Page | p50 ms | p95 ms | SQL queries |
|---|---:|---:|---:|
| Dashboard | 93.3 | 129.4 | 16 |
| Dashboard overdue filter (HTMX) | 40.8 | 44.9 | 6 |
| Subscriptions list | 6.8 | 8.4 | 5 |
| Subscriptions: search | 22.7 | 26.1 | 5 |
| Invoices list | 145.7 | 173.3 | 6 |
| Invoices: overdue | 63.9 | 80.4 | 7 |
| Billing preview | 7.9 | 10.7 | 8 |
| Subscription detail | 7.9 | 11.1 | 6 |
| Invoice detail | 11.5 | 14.3 | 10 |

Worst p95: 173 ms against a 2,000 ms budget.

Limits of this measurement: it is server time only (Django, SQL, templates), run in-process. It excludes network latency, browser rendering and static-file downloads. Billing preview is fast here because nothing was due at measurement time. The invoices list is slowest because it sums payments per invoice; at much larger scale we would store a running paid amount on the invoice, updated in the same transaction as each payment.

## Offline and caching

- **Works with no internet.** Tailwind, daisyUI, HTMX and Chart.js are vendored in `static/vendor/` (versions in `VERSIONS.txt`) and served by Django. Email goes to a local Mailpit inbox. A test (`core/tests/test_offline.py`) fails if any page references an external host.
- **Browser caching.** Static files are served with `Last-Modified`, so browsers revalidate and receive `304 Not Modified` instead of downloading the libraries again.
- **Offline planning.** Billing is planned before it is executed: `/billing/` preview and `run_billing --dry-run` compute what is due, including catch-up periods and exceptions, without writing anything. Each subscription stores its next invoice date, so finding what is due is one indexed query (`sub_due_idx`), and the run is idempotent, so a planned run can be repeated safely.
- **What we deliberately do not cache.** Dashboard and invoice figures are computed live. They are fast enough (see Performance), and a cache could show a stale total right after a payment or a simulated-clock change, which is worse than a slower page on a finance screen. If load required it, the next step would be a short-lived cache keyed on a version number that every payment and invoice write increments.

## Known limitations

- Email is delivered to a local Mailpit inbox, not a real mail server.
- The `ollama` service in `docker-compose.yml` is for a planned "Ask Finance" feature that was not built. It sits behind a Compose profile, so it doesn't start unless you run `docker compose --profile ai up -d`.
- No payment gateway: payments are recorded by staff.

## Logging and operations

- Every container uses Docker's `json-file` driver capped at 3 files of 10 MB. The default driver never rotates, so a long billing run could otherwise fill the disk and stop Postgres.
- `PYTHONUNBUFFERED=1` makes Python write logs immediately, so a crash doesn't lose its last lines.
- Django logs one line per event to stdout. SQL logging is kept at WARNING so customer data doesn't end up in logs.
- No remote log driver (fluentd, syslog, gelf) on purpose. In their default blocking mode a slow log server stalls the app, and fluentd can stop a container from starting. If we add central logging, we will use `mode: non-blocking` and accept dropped log lines rather than a frozen billing run.
- The web service waits for a Postgres health check before starting, so a fresh `docker compose up` doesn't race the database.

## Project layout

```
core/
  billing_calc.py, periods.py   pure billing maths (no database)
  billing.py, payments.py       billing and payment services (transactions, locks)
  subscriptions.py              subscription lifecycle
  statemachine.py               transition tables and logged transitions
  invariants.py                 data-integrity checks
  clock.py                      simulated date for testing
  management/commands/          run_billing, seed_demo, measure_p95
  tests/                        unit and integration tests
seed_data/                      name lists for the demo data generator
docs/performance.md             latest p95 report
```
