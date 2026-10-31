"""Pure-function tests: no database. Run in milliseconds."""
import unittest
from datetime import date

from core.billing_calc import DiscountIn, LineIn, compute, rhu
from core.periods import next_period_start, periods_due


def line(key=1, pid=1, qty=1, price=10000, tax=0):
    return LineIn(key, pid, f"item{key}", qty, price, tax)


def pct(id, bps, pids=(1,), **kw):
    return DiscountIn(id, f"d{id}", "percent", bps, frozenset(pids), **kw)


def fixed(id, paise, pids=(1,), **kw):
    return DiscountIn(id, f"d{id}", "fixed", paise, frozenset(pids), **kw)


class PeriodTests(unittest.TestCase):
    def test_month_end_anchor_jan31_feb28_mar31(self):
        d1 = date(2026, 1, 31)
        d2 = next_period_start(d1, "monthly", 31)
        d3 = next_period_start(d2, "monthly", 31)
        d4 = next_period_start(d3, "monthly", 31)
        self.assertEqual((d2, d3, d4), (date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)))

    def test_leap_year_february(self):
        self.assertEqual(next_period_start(date(2028, 1, 31), "monthly", 31), date(2028, 2, 29))

    def test_yearly_feb29_anchor(self):
        d = date(2028, 2, 29)
        d = next_period_start(d, "yearly", 29)
        self.assertEqual(d, date(2029, 2, 28))
        for _ in range(3):
            d = next_period_start(d, "yearly", 29)
        self.assertEqual(d, date(2032, 2, 29))

    def test_daily_weekly_and_december_rollover(self):
        self.assertEqual(next_period_start(date(2026, 12, 31), "daily"), date(2027, 1, 1))
        self.assertEqual(next_period_start(date(2026, 12, 28), "weekly"), date(2027, 1, 4))
        self.assertEqual(next_period_start(date(2026, 12, 15), "monthly", 15), date(2027, 1, 15))

    def test_periods_due_catches_up_and_respects_last_start(self):
        got = list(periods_due(date(2026, 1, 31), date(2026, 4, 30), "monthly", 31))
        self.assertEqual(got, [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)])
        got = list(periods_due(date(2026, 1, 31), date(2026, 4, 30), "monthly", 31, last_start=date(2026, 3, 1)))
        self.assertEqual(got, [date(2026, 1, 31), date(2026, 2, 28)])


class RoundingTests(unittest.TestCase):
    def test_half_up(self):
        self.assertEqual([rhu(n, 10) for n in (4, 5, 6, 14, 15)], [0, 1, 1, 1, 2])

    def test_tax_rounds_half_up_once_per_line(self):
        r = compute([line(price=25, tax=1800)])           # 25 * 18% = 4.5 -> 5
        self.assertEqual(r.lines[0].tax_amount, 5)
        r = compute([line(price=24, tax=1800)])           # 4.32 -> 4
        self.assertEqual(r.lines[0].tax_amount, 4)

    def test_tax_is_per_line_not_on_total(self):
        # two lines of 25p @18%: per-line = 5 + 5 = 10 (tax on the total would give 9)
        r = compute([line(1, price=25, tax=1800), line(2, pid=2, price=25, tax=1800)])
        self.assertEqual(r.tax_total, 10)


class DiscountTests(unittest.TestCase):
    def test_tax_computed_after_discount(self):
        r = compute([line(qty=2, price=56000, tax=1800)], [], pct(9, 1000, ()))   # sub-level 10%
        l = r.lines[0]
        self.assertEqual((l.gross, l.discount_amount, l.taxable), (112000, 11200, 100800))
        self.assertEqual((l.tax_amount, l.line_total), (18144, 118944))
        self.assertEqual(r.total, 118944)

    def test_no_discount(self):
        r = compute([line(price=1000, tax=500)])
        self.assertEqual((r.discount_total, r.total, r.trace["chosen"]), (0, 1050, "none"))

    def test_best_single_product_discount_per_line(self):
        r = compute([line(price=10000)], [pct(1, 1000), fixed(2, 1500), fixed(3, 500)])
        self.assertEqual((r.lines[0].discount_amount, r.lines[0].discount_id), (1500, 2))
        self.assertEqual(r.discount_ids, [2])         # never stacked

    def test_product_discount_only_on_its_products(self):
        r = compute([line(1, pid=1, price=10000), line(2, pid=2, price=10000)], [pct(1, 1000, (1,))])
        self.assertEqual([o.discount_amount for o in r.lines], [1000, 0])

    def test_subscription_discount_vs_product_discount_larger_wins_no_stacking(self):
        lines = [line(1, pid=1, price=10000), line(2, pid=2, price=10000)]
        r = compute(lines, [pct(1, 500, (1,))], pct(9, 1000, ()))     # A=500, B=2000
        self.assertEqual((r.discount_total, r.trace["chosen"], r.discount_ids), (2000, "subscription", [9]))
        r = compute(lines, [pct(1, 5000, (1,))], pct(9, 1000, ()))    # A=5000, B=2000
        self.assertEqual((r.discount_total, r.trace["chosen"], r.discount_ids), (5000, "product", [1]))

    def test_fixed_subscription_discount_splits_exactly(self):
        lines = [line(1, pid=1, price=3333), line(2, pid=2, price=3333), line(3, pid=3, price=3334)]
        r = compute(lines, [], fixed(9, 1000, ()))
        self.assertEqual(r.discount_total, 1000)                      # not 999 or 1001
        self.assertEqual(sum(o.discount_amount for o in r.lines), 1000)

    def test_discount_never_exceeds_line(self):
        r = compute([line(price=300)], [fixed(1, 99999)])
        self.assertEqual((r.lines[0].discount_amount, r.total), (300, 0))

    def test_minimums_must_be_met(self):
        r = compute([line(qty=2, price=1000)], [pct(1, 1000, min_quantity=3)])
        self.assertEqual(r.discount_total, 0)
        r = compute([line(qty=3, price=1000)], [pct(1, 1000, min_quantity=3)])
        self.assertEqual(r.discount_total, 300)
        r = compute([line(qty=1, price=1000)], [], pct(9, 1000, (), min_purchase=2000))
        self.assertEqual(r.discount_total, 0)

    def test_percent_discount_rounds_half_up(self):
        r = compute([line(price=25)], [pct(1, 1000)])                 # 2.5 -> 3
        self.assertEqual(r.lines[0].discount_amount, 3)

    def test_totals_are_internally_consistent(self):
        r = compute([line(1, price=9999, qty=3, tax=1800), line(2, pid=2, price=1234, qty=7, tax=500)],
                    [pct(1, 750)], pct(9, 400, ()))
        self.assertEqual(r.total, sum(o.line_total for o in r.lines))
        self.assertEqual(r.total, r.subtotal - r.discount_total + r.tax_total)
        self.assertEqual(r.trace["total"], r.total)


if __name__ == "__main__":
    unittest.main()
