import unittest

from core.formatting import format_bps, format_rupees, indian_group


class IndianFormatTests(unittest.TestCase):
    def test_grouping(self):
        cases = {0: "0", 999: "999", 1000: "1,000", 99999: "99,999", 100000: "1,00,000",
                 1234567: "12,34,567", 858779400: "85,87,79,400"}
        for n, want in cases.items():
            with self.subTest(n=n):
                self.assertEqual(indian_group(n), want)

    def test_rupees(self):
        self.assertEqual(format_rupees(8587794000), "₹8,58,77,940.00")
        self.assertEqual(format_rupees(112000), "₹1,120.00")
        self.assertEqual(format_rupees(-5), "-₹0.05")
        self.assertEqual(format_rupees(None), "")
        self.assertEqual(format_bps(1800), "18.00%")


if __name__ == "__main__":
    unittest.main()
