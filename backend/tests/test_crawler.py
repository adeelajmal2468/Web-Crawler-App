"""Offline unit tests. These do NOT certify live portal compatibility."""
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlparse
from unittest.mock import patch

from crawler import (RobotsAwareFetcher, crawl, evaluate_page, normalize_seeds,
                     parse_page, safe_url, scan, discover_urls)
from engine import PlotQuery
from database import add_watch, get_watch, store_scan, due_watches

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / 'fixtures' / 'example_listing.html').read_text(encoding='utf-8')
CATEGORY = (ROOT / 'fixtures' / 'example_category.html').read_text(encoding='utf-8')
Q = PlotQuery('173', 'Lahore', 'DHA Phase 6', 'Block F')


class FakeResponse:
    def __init__(self, text, status=200, ctype='text/html', headers=None):
        self.status_code = status
        self._content = text.encode('utf-8')
        self.headers = {'content-type': ctype, **(headers or {})}
        self.encoding = 'utf-8'
    @property
    def content(self):
        return self._content
    def iter_content(self, n):
        yield self._content
    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(str(self.status_code))
    def close(self):
        pass


class FakeSession:
    def __init__(self, routes):
        self.routes = routes
        self.headers = {}
        self.requested = []
    def get(self, url, **kwargs):
        self.requested.append(url)
        return self.routes.get(url, FakeResponse('', 404))


class TestCrawler(unittest.TestCase):
    def test_strict_identifier_and_location(self):
        parsed = parse_page(HTML, 'https://example.pk/property/173')
        result = evaluate_page(parsed, 'https://example.pk/property/173', Q)
        self.assertEqual(result['score'], 95)
        self.assertIn('Plot', result['title'])
        self.assertIsNone(evaluate_page(parsed, 'https://example.pk/property/173', PlotQuery('17', 'Lahore', 'DHA Phase 6', 'Block F')))
        self.assertIsNone(evaluate_page(parsed, 'https://example.pk/property/173', PlotQuery('173', 'Lahore', 'DHA Phase 7', 'Block F')))
        self.assertIsNone(evaluate_page(parsed, 'https://example.pk/property/173', PlotQuery('173', 'Islamabad', 'DHA Phase 6', 'Block F')))

    def test_explicit_expired_listing_not_marked_available(self):
        html = HTML.replace('</main>', '<p>This property is no longer available</p></main>')
        parsed = parse_page(html, 'https://example.pk/property/173')
        result = evaluate_page(parsed, 'https://example.pk/property/173', Q)
        self.assertIn('sold/expired', result['confidence'])

    def test_unlabeled_street_number_not_plot(self):
        html = '<main><h1>Residential land 173 Street</h1><p>DHA Phase 6 Block F Lahore for sale</p></main>'
        self.assertIsNone(evaluate_page(parse_page(html, 'https://example.pk/property/sample'),
                                        'https://example.pk/property/sample', Q))

    def test_safe_urls(self):
        for bad in ('http://127.0.0.1/a', 'http://localhost/private', 'file:///etc/passwd',
                    'https://x.local/', 'https://test.pk:8100/abc', 'https://user:pass@xyz.pk/'):
            self.assertIsNone(safe_url(bad), bad)
        self.assertEqual(normalize_seeds('https://example.pk/a\nhttps://example.pk/a'), ['https://example.pk/a'])

    def test_direct_crawl_html_and_follow_links(self):
        base = 'https://example.pk'
        routes = {base + '/robots.txt': FakeResponse('User-agent: *\nAllow: /', ctype='text/plain'),
                  base + '/plots-for-sale': FakeResponse(CATEGORY),
                  base + '/property/173': FakeResponse(HTML),
                  base + '/property/another': FakeResponse('<main><h1>Residential plot no 999</h1></main>')}
        session = FakeSession(routes)
        out = crawl(Q, [base + '/plots-for-sale'], max_pages=10,
                    fetcher=RobotsAwareFetcher(session, min_delay=0))
        self.assertTrue(out.stats.complete, out.stats.report())
        self.assertEqual(out.stats.fetched, 3)
        self.assertEqual(len(out.results), 1)
        self.assertEqual(out.results[0]['url'], base + '/property/173')
        self.assertIn(base + '/robots.txt', session.requested)

    def test_robots_blocked_and_no_bypass(self):
        base = 'https://example.pk'
        session = FakeSession({base + '/robots.txt': FakeResponse('User-agent: *\nDisallow: /property/', ctype='text/plain')})
        out = crawl(Q, [base + '/property/173'], fetcher=RobotsAwareFetcher(session, min_delay=0))
        self.assertFalse(out.stats.complete)
        self.assertEqual(out.stats.blocked, 1)
        self.assertNotIn(base + '/property/173', session.requested)

    def test_missing_robots_means_no_rules(self):
        base = 'https://example.pk'
        session = FakeSession({base + '/property/173': FakeResponse(HTML)})
        out = crawl(Q, [base + '/property/173'], fetcher=RobotsAwareFetcher(session, min_delay=0))
        self.assertEqual(len(out.results), 1)
        self.assertTrue(out.stats.complete)

    def test_no_cross_host_crawl(self):
        html = '<main><a href="https://other.pk/property/173">Plot 173</a></main>'
        base = 'https://example.pk'
        session = FakeSession({base + '/start': FakeResponse(html)})
        crawl(Q, [base + '/start'], fetcher=RobotsAwareFetcher(session, min_delay=0))
        self.assertNotIn('https://other.pk/property/173', session.requested)

    def test_partial_results_dont_disappear(self):
        with tempfile.TemporaryDirectory() as d:
            # No modifications to user's actual data when tests run.
            with patch('database.ROOT', Path(d)):
                wid = add_watch(Q, mode='direct', seed_urls='https://example.pk/property/173')
                item = evaluate_page(parse_page(HTML, 'https://example.pk/property/173'),
                                     'https://example.pk/property/173', Q)
                store_scan(wid, [item], complete=True, fetched=1)
                self.assertIn('advertisement', get_watch(wid)['last_status'])
                from database import findings
                self.assertEqual(len(findings(wid)), 1)
                store_scan(wid, [], complete=False, fetched=1, blocked=1)
                self.assertEqual(len(findings(wid)), 1)
                self.assertIn('Partial', get_watch(wid)['last_status'])
                self.assertEqual(due_watches(), [])

    def test_automatic_url_discovery_and_html_verification(self):
        class ApiResponse:
            def raise_for_status(self): pass
            def json(self):
                return {"organic": [
                    {"link": "https://example.pk/property/173", "title": "misleading search title"},
                    {"link": "http://127.0.0.1/private", "title": "unsafe"},
                ]}

        class ApiSession:
            def __init__(self): self.queries = []
            def post(self, url, json, **kwargs):
                self.queries.append(json["q"])
                return ApiResponse()

        session = FakeSession({
            "https://example.pk/robots.txt": FakeResponse("User-agent: *\nAllow: /"),
            "https://example.pk/property/173": FakeResponse(HTML),
        })
        discovery_session = ApiSession()
        out = scan(Q, "hybrid", "", api_key="test-api-key", domains=("example.pk",),
                   fetcher=RobotsAwareFetcher(session, min_delay=0),
                   discovery_session=discovery_session)
        self.assertEqual(out.stats.discovered_urls, ["https://example.pk/property/173"])
        self.assertEqual(len(out.results), 1)
        self.assertEqual(out.results[0]["score"], 95)
        self.assertIn("site:example.pk", discovery_session.queries[0])
        with tempfile.TemporaryDirectory() as d:
            with patch('database.ROOT', Path(d)):
                from database import discovered_links
                wid = add_watch(Q, mode="hybrid")
                store_scan(wid, out.results, fetched=out.stats.fetched,
                           discovered_urls=out.stats.discovered_urls)
                self.assertEqual(discovered_links(wid)[0]["url"], "https://example.pk/property/173")

    def test_discovered_url_is_not_a_verified_match_if_blocked(self):
        class ApiResponse:
            def raise_for_status(self): pass
            def json(self):
                return {"organic": [{"link": "https://example.pk/property/173"}]}
        class ApiSession:
            def post(self, *args, **kwargs): return ApiResponse()
        session = FakeSession({
            "https://example.pk/robots.txt": FakeResponse("User-agent: *\nDisallow: /property/")
        })
        out = scan(Q, "hybrid", "", api_key="test-api-key", domains=("example.pk",),
                   fetcher=RobotsAwareFetcher(session, min_delay=0),
                   discovery_session=ApiSession())
        self.assertEqual(len(out.results), 0)
        self.assertEqual(len(out.stats.discovered_urls), 1)
        self.assertEqual(out.stats.blocked, 1)
        self.assertFalse(out.stats.complete)

    def test_demo_offline_matching_only(self):
        # A fixture must never count as a real crawl automatically.
        result = evaluate_page(parse_page(HTML, 'https://example.pk/property/173'),
                               'https://example.pk/property/173', Q)
        self.assertIsNotNone(result)


if __name__ == '__main__':
    unittest.main()
