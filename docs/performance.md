# Performance: server-side p95

Measured 2026-10-31 15:07 IST. 50 timed requests per page after 3 warm-up requests, logged in as an admin, `DEBUG=False`.
Data: 10,004 subscriptions, 44,544 invoices, 28,438 payments.

Budget: every page p95 <= 2000.0 ms. Worst p95: 173.3 ms. **PASS**

| Page | URL | Status | p50 ms | p95 ms | max ms | SQL queries |
|---|---|---|---:|---:|---:|---:|
| Dashboard | `/dashboard/` | 200 | 93.3 | 129.4 | 145.3 | 16 |
| Dashboard overdue filter (HTMX) | `/dashboard/?q=a&min_days=30` | 200 | 40.8 | 44.9 | 47.1 | 6 |
| Subscriptions list | `/subscriptions/` | 200 | 6.8 | 8.4 | 9.4 | 5 |
| Subscriptions: active | `/subscriptions/?status=active` | 200 | 8.0 | 18.7 | 22.2 | 5 |
| Subscriptions: search | `/subscriptions/?q=SUB-0001` | 200 | 22.7 | 26.1 | 27.1 | 5 |
| Subscriptions: deep page | `/subscriptions/?page=300` | 200 | 18.5 | 21.8 | 39.0 | 5 |
| Invoices list | `/invoices/` | 200 | 145.7 | 173.3 | 195.3 | 6 |
| Invoices: overdue | `/invoices/?status=overdue` | 200 | 63.9 | 80.4 | 87.9 | 7 |
| Invoices: search | `/invoices/?q=INV-0004` | 200 | 60.3 | 69.3 | 71.2 | 6 |
| Billing preview | `/billing/` | 200 | 7.9 | 10.7 | 11.0 | 8 |
| Catalog: products | `/catalog/products/` | 200 | 5.5 | 7.2 | 9.3 | 5 |
| Subscription detail | `/subscriptions/10004/` | 200 | 7.9 | 11.1 | 42.0 | 6 |
| Invoice detail | `/invoices/44544/` | 200 | 11.5 | 14.3 | 15.4 | 10 |

## What this measures, and what it doesn't

- Time inside Django: middleware, auth, SQL against PostgreSQL, template rendering.
- It runs in-process through Django's test client, so it excludes network latency, the browser's own rendering, and the Tailwind/HTMX/Chart.js CDN downloads.
- Queries per page are counted on a separate request, so counting doesn't skew timing.
- Reproduce: `docker compose run --rm web python manage.py measure_p95`
