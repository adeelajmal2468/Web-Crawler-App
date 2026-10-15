"""Additional offline tests proving the v4 default is open-web discovery."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

from crawler import build_discovery_queries, discover_urls, scan, RobotsAwareFetcher
from database import add_watch, get_watch
from engine import PlotQuery
from test_crawler import FakeResponse, FakeSession, HTML

Q = PlotQuery("301", "Rawalpindi", "Bahria Town Phase 8", "Block J")
PLOT_HTML = """<html><head><title>Plot No 301 for sale in Bahria Town Phase 8, Block J, Rawalpindi</title></head>
<main><h1>Plot No 301 for Sale</h1><p>Bahria Town Phase 8, Block J, Rawalpindi for sale.</p></main></html>"""


class ApiResponse:
    def raise_for_status(self):
        pass
    def json(self):
        return {"organic": [
            {"link": "https://localestate.pk/property/301", "title": "Plot No 301 Bahria Town Phase 8 Block J Rawalpindi sale", "snippet": "Property for sale"},
            {"link": "https://anotheragency.com.pk/listing/301", "title": "Plot 301 Bahria Town Phase 8 Rawalpindi", "snippet": "Block J property for sale"},
            {"link": "https://zameen.com/property/301", "title": "Plot 301 Bahria Town Phase 8 Rawalpindi", "snippet": "Block J for sale"},
            {"link": "http://127.0.0.1/admin", "title": "Plot 301 Rawalpindi", "snippet": "do not fetch"},
        ]}

class ApiSession:
    def __init__(self): self.queries = []
    def post(self, url, json, **kwargs):
        self.queries.append(json["q"])
        return ApiResponse()


class TestOpenWeb(unittest.TestCase):
    def test_default_queries_have_no_site_filters(self):
        queries = build_discovery_queries(Q)
        self.assertGreaterEqual(len(queries), 3)
        self.assertFalse(any("site:" in q for q in queries))
        self.assertTrue(any("Rawalpindi" in q for q in queries))

    def test_optional_user_domain_limit_still_possible(self):
        queries = build_discovery_queries(Q, ("localestate.pk",))
        self.assertTrue(all("site:localestate.pk" in q for q in queries))

    def test_results_across_all_domains_are_accepted(self):
        api = ApiSession()
        urls, errs = discover_urls(Q, "mock-token", session=api)
        self.assertEqual(errs, [])
        self.assertIn("https://localestate.pk/property/301", urls)
        self.assertIn("https://anotheragency.com.pk/listing/301", urls)
        self.assertIn("https://zameen.com/property/301", urls)
        self.assertFalse(any("127.0.0.1" in u for u in urls))
        self.assertTrue(all("site:" not in s for s in api.queries))

    def test_any_public_website_html_can_verify(self):
        api = ApiSession()
        site = "https://localestate.pk"
        routes = {
            site + "/robots.txt": FakeResponse("User-agent: *\nAllow: /", ctype="text/plain"),
            site + "/property/301": FakeResponse(PLOT_HTML),
            "https://anotheragency.com.pk/robots.txt": FakeResponse("User-agent: *\nDisallow: /", ctype="text/plain"),
            "https://zameen.com/robots.txt": FakeResponse("User-agent: *\nDisallow: /", ctype="text/plain"),
        }
        out = scan(Q, "hybrid", "", "mock-token", max_pages=3,
                   discovery_session=api,
                   fetcher=RobotsAwareFetcher(FakeSession(routes), min_delay=0))
        self.assertTrue(any(i["source"] == "localestate.pk" for i in out.results), out.stats.report())
        self.assertGreaterEqual(out.stats.blocked, 1)
        self.assertTrue(all(i["source"] == "localestate.pk" for i in out.results))

    def test_page_budget_is_reported_without_forcing_daily_retry(self):
        roots = ["https://localestate.pk/property/301", "https://anotheragency.com.pk/listing/301"]
        routes = {
            "https://localestate.pk/robots.txt": FakeResponse("User-agent: *\nAllow: /", ctype="text/plain"),
            "https://localestate.pk/property/301": FakeResponse(PLOT_HTML),
            "https://anotheragency.com.pk/robots.txt": FakeResponse("User-agent: *\nAllow: /", ctype="text/plain"),
            "https://anotheragency.com.pk/listing/301": FakeResponse(PLOT_HTML),
        }
        from crawler import crawl
        out = crawl(Q, roots, max_pages=1, fetcher=RobotsAwareFetcher(FakeSession(routes), min_delay=0))
        self.assertTrue(out.stats.budget_limited)
        self.assertTrue(out.stats.complete)
        self.assertIn("budget", out.stats.report().lower())

    def test_new_and_migrated_saved_watches_search_all_sites(self):
        with tempfile.TemporaryDirectory() as d:
            with patch("database.ROOT", Path(d)):
                wid = add_watch(Q, mode="hybrid")
                self.assertEqual(get_watch(wid)["source_domains"], "")
                import database
                with database.connect() as conn:
                    conn.execute("UPDATE watches SET source_domains='zameen.com,graana.com,pakmls.com' WHERE id=?", (wid,))
                    conn.execute("PRAGMA user_version=3")
                self.assertEqual(get_watch(wid)["source_domains"], "")
                # A user-chosen scope must not be erased after the first migration.
                with database.connect() as conn:
                    conn.execute("UPDATE watches SET source_domains='agency.pk' WHERE id=?", (wid,))
                self.assertEqual(get_watch(wid)["source_domains"], "agency.pk")


if __name__ == "__main__":
    unittest.main()
