"""Pure helpers for the p95 measurement (no Django imports, unit-testable)."""
import math


def percentile(samples, p):
    """Nearest-rank percentile: the smallest sample with at least p% of samples <= it.
    No interpolation, so the reported number is a request that really happened."""
    if not samples:
        raise ValueError("no samples")
    if not 0 < p <= 100:
        raise ValueError("p must be in (0, 100]")
    ordered = sorted(samples)
    rank = math.ceil(p / 100 * len(ordered))
    return ordered[rank - 1]


def summarize(samples):
    return {
        "n": len(samples),
        "p50": percentile(samples, 50),
        "p95": percentile(samples, 95),
        "max": max(samples),
    }


def markdown_report(rows, *, runs, warmup, counts, budget_ms, stamp):
    """rows: list of dicts with name, url, status, p50, p95, max, queries."""
    worst = max(r["p95"] for r in rows)
    verdict = "PASS" if worst <= budget_ms else "FAIL"
    out = [
        "# Performance: server-side p95",
        "",
        f"Measured {stamp}. {runs} timed requests per page after {warmup} warm-up requests, "
        "logged in as an admin, `DEBUG=False`.",
        f"Data: {counts}.",
        "",
        f"Budget: every page p95 <= {budget_ms} ms. Worst p95: {worst:.1f} ms. **{verdict}**",
        "",
        "| Page | URL | Status | p50 ms | p95 ms | max ms | SQL queries |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for r in rows:
        out.append(f"| {r['name']} | `{r['url']}` | {r['status']} | {r['p50']:.1f} | "
                   f"{r['p95']:.1f} | {r['max']:.1f} | {r['queries']} |")
    out += [
        "",
        "## What this measures, and what it doesn't",
        "",
        "- Time inside Django: middleware, auth, SQL against PostgreSQL, template rendering.",
        "- It runs in-process through Django's test client, so it excludes network latency, "
        "the browser's own rendering, and the Tailwind/HTMX/Chart.js CDN downloads.",
        "- Queries per page are counted on a separate request, so counting doesn't skew timing.",
        "- Reproduce: `docker compose run --rm web python manage.py measure_p95`",
        "",
    ]
    return "\n".join(out), verdict
