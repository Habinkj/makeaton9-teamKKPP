"""The app must work with no internet: no page may load anything from an external host.

Run:  docker compose run --rm web python manage.py test core.tests.test_offline -v 2
"""
from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from core.tests.test_auth import make_user

VENDORED = ("tailwind.js", "daisyui.css", "htmx.js", "chart.js")
EXTERNAL = ("cdn.", "unpkg.com", "jsdelivr", "googleapis", "cloudflare")


class OfflineTests(TestCase):
    def test_vendored_files_exist_and_are_not_empty(self):
        for name in VENDORED:
            path = settings.BASE_DIR / "static" / "vendor" / name
            self.assertTrue(path.exists(), f"{name} is missing from static/vendor/")
            self.assertGreater(path.stat().st_size, 10_000, f"{name} looks truncated")

    def assert_local_only(self, response):
        html = response.content.decode()
        for host in EXTERNAL:
            self.assertNotIn(host, html, f"page loads from external host: {host}")

    def test_public_pages_use_local_assets(self):
        r = self.client.get(reverse("login"))
        self.assertContains(r, "/static/vendor/tailwind.js")
        self.assertContains(r, "/static/vendor/daisyui.css")
        self.assertContains(r, "/static/vendor/htmx.js")
        self.assert_local_only(r)

    def test_dashboard_uses_local_chart_library(self):
        self.client.force_login(make_user("admin@brightloop.test", "admin"))
        r = self.client.get(reverse("dashboard"))
        self.assertContains(r, "/static/vendor/chart.js")
        self.assert_local_only(r)
