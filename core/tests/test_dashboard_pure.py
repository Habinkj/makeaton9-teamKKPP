import unittest

from core.dashboard_calc import mrr, to_monthly


class MrrTests(unittest.TestCase):
    def test_normalisation(self):
        self.assertEqual(to_monthly("monthly", 112000), 112000)
        self.assertEqual(to_monthly("yearly", 120000), 10000)
        self.assertEqual(to_monthly("weekly", 1000), 4333)        # 1000 * 52 / 12 = 4333.33
        self.assertEqual(to_monthly("daily", 100), 3042)          # 100 * 365 / 12 = 3041.67

    def test_mrr_sums_periods(self):
        self.assertEqual(mrr([("monthly", 112000), ("yearly", 120000), ("monthly", None)]), 122000)


if __name__ == "__main__":
    unittest.main()
