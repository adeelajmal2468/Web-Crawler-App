"""Offline regression checks for Serper failures reported by the user."""
import unittest
from crawler import discover_urls
from engine import PlotQuery
from serper_client import SerperAPIError, search_once

Q = PlotQuery("301", "Rawalpindi", "Bahria Town Phase 8", "Block J")

class FakeResp:
    def __init__(self, code, obj):
        self.status_code = code
        self.obj = obj
        self.text = str(obj)
    def json(self):
        return self.obj

class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.posts = []
    def post(self, url, json, headers, timeout):
        self.posts.append((url, json, headers, timeout))
        return self.responses.pop(0)

class TestSerperDiagnostics(unittest.TestCase):
    def test_q_only_payload_and_success(self):
        session = FakeSession([FakeResp(200, {'organic':[{'link':'https://estate.pk/a'}]})])
        payload = search_once('plots for sale in Pakistan', 'dummy-key', session=session)
        self.assertEqual(len(payload['organic']), 1)
        self.assertEqual(session.posts[0][1], {'q':'plots for sale in Pakistan'})
        self.assertEqual(session.posts[0][2]['X-API-KEY'], 'dummy-key')

    def test_http_400_includes_provider_message(self):
        session = FakeSession([FakeResp(400, {'message':'Not enough credits'})])
        with self.assertRaises(SerperAPIError) as ctx:
            search_once('plot 301 Rawalpindi', 'dummy-key', session=session)
        self.assertIn('HTTP 400', str(ctx.exception))
        self.assertIn('Not enough credits', str(ctx.exception))

    def test_fatal_400_does_not_make_four_failures(self):
        session = FakeSession([FakeResp(400, {'message':'Bad API key'})])
        urls, issues = discover_urls(Q,'dummy-key',session=session)
        self.assertEqual(urls, [])
        self.assertEqual(len(session.posts), 1)
        self.assertEqual(len(issues), 1)
        self.assertIn('Bad API key', issues[0])

    def test_do_not_display_api_key_in_provider_message(self):
        secret = 'fake-highly-sensitive-secret'
        session = FakeSession([FakeResp(400, {'error':secret+' invalid'})])
        with self.assertRaises(SerperAPIError) as ctx:
            search_once('property',secret,session=session)
        self.assertNotIn(secret, str(ctx.exception))
        self.assertIn('[REDACTED]', str(ctx.exception))

    def test_prevent_bearer_prefix(self):
        with self.assertRaisesRegex(SerperAPIError, 'without the'):
            search_once('plot', 'Bearer not-an-api-key')

if __name__ == '__main__': unittest.main()
