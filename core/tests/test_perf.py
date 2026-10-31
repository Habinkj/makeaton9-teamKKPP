"""p95 helper maths and the measure_p95 command end to end (small data).

Run:  docker compose run --rm web python manage.py test core.tests.test_perf -v 2
"""
import tempfile
from io import StringIO
from pathlib import Path
from unittest import TestCase as PlainTestCase

from django.core.management import call_command
from django.core.management.base import CommandError

from core import billing, perf
from core.tests.test_auth import make_user
from core.tests.test_billing import JAN31, BillingBase


class PercentileTests(PlainTestCase):
    def test_nearest_rank(self):
        data = list(range(1, 101))                       # 1..100
        self.assertEqual(perf.percentile(data, 95), 95)
        self.assertEqual(perf.percentile(data, 50), 50)
        self.assertEqual(perf.percentile(data, 100), 100)

    def test_order_does_not_matter(self):
        self.assertEqual(perf.percentile([30, 10, 20], 50), 20)

    def test_small_sample_p95_is_the_max(self):
        self.assertEqual(perf.percentile([5, 1, 9], 95), 9)

    def test_single_sample(self):
        self.assertEqual(perf.summarize([4.2]), {"n": 1, "p50": 4.2, "p95": 4.2, "max": 4.2})

    def test_bad_input(self):
        with self.assertRaises(ValueError):
            perf.percentile([], 95)
        with self.assertRaises(ValueError):
            perf.percentile([1], 0)

    def test_report_verdict(self):
        row = {"name": "X", "url": "/x/", "status": 200, "p50": 1.0, "p95": 3.0, "max": 4.0, "queries": 5}
        _, ok = perf.markdown_report([row], runs=1, warmup=0, counts="", budget_ms=10, stamp="now")
        _, bad = perf.markdown_report([row], runs=1, warmup=0, counts="", budget_ms=2, stamp="now")
        self.assertEqual((ok, bad), ("PASS", "FAIL"))


class MeasureCommandTests(BillingBase):
    def setUp(self):
        super().setUp()
        self.admin = make_user("admin@brightloop.test", "admin")
        self.active_sub()
        billing.run_billing(JAN31)

    def test_writes_report_with_every_page(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "perf.md"
            buf = StringIO()
            call_command("measure_p95", runs=2, warmup=0, out=str(out), stdout=buf)
            text = out.read_text(encoding="utf-8")
        for name in ("Dashboard", "Invoices list", "Billing preview", "Invoice detail", "Subscription detail"):
            self.assertIn(name, text)
        self.assertIn("p95", text)
        self.assertIn("PASS", buf.getvalue())

    def test_needs_an_admin(self):
        self.admin.delete()
        with self.assertRaises(CommandError):
            call_command("measure_p95", runs=1, warmup=0, out="/tmp/x.md",
                         email="nobody@brightloop.test", stdout=StringIO())
