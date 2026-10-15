"""Offline integration tests: API auth, queue, crawler->SQLite, scheduling."""
import os
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from crawler import CrawlOutput, CrawlStats
from api import app
from jobs import process_next_job


class ApiIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch_env = patch.dict(os.environ, {
            'PLOTWATCH_DATA_DIR': self.temp.name,
            'PLOTWATCH_APP_TOKEN': 'local-only-demo-very-long-random-token',
            'SERPER_API_KEY': 'fake-test-key',
            'DISABLE_SCHEDULER': '1',
        })
        self.patch_env.start()
        self.client_ctx = TestClient(app)
        self.client = self.client_ctx.__enter__()
        self.auth = {'Authorization': 'Bearer local-only-demo-very-long-random-token'}

    def tearDown(self):
        self.client_ctx.__exit__(None,None,None)
        self.patch_env.stop()
        self.temp.cleanup()

    def test_auth_queue_scan_and_15_day_monitoring(self):
        self.assertEqual(self.client.get('/health').status_code, 200)
        self.assertEqual(self.client.get('/api/watches').status_code, 401)
        create = self.client.post('/api/watches', json={
            'plot_number': '301', 'city': 'Rawalpindi',
            'society': 'Bahria Town Phase 8', 'block': 'Block J'
        }, headers=self.auth)
        self.assertEqual(create.status_code, 202, create.text)
        ids = create.json()
        self.assertEqual(self.client.get('/api/jobs/'+str(ids['job_id']), headers=self.auth).json()['status'], 'queued')
        result = {
            'title':'Plot no 301 for sale', 'url':'https://example.com/listing301',
            'source':'example.com', 'snippet':'Rawalpindi Bahria Town Phase 8 Block J',
            'confidence':'HTML verified match', 'score':95
        }
        with patch('jobs.scan', return_value=CrawlOutput([result], CrawlStats(fetched=1,candidates=1))):
            process_next_job()
        finished = self.client.get('/api/jobs/'+str(ids['job_id']), headers=self.auth).json()
        self.assertEqual(finished['status'], 'completed')
        details = self.client.get('/api/watches/'+str(ids['watch_id']), headers=self.auth).json()
        self.assertEqual(len(details['findings']),1)
        self.assertIn('15',str(__import__('database').INTERVAL_DAYS))
        self.assertTrue(details['watch']['next_check_at'] > details['watch']['last_checked_at'])
        self.assertEqual(len(self.client.get('/api/history',headers=self.auth).json()['items']),1)
        new = self.client.post('/api/watches/'+str(ids['watch_id'])+'/refresh', headers=self.auth).json()
        self.assertNotEqual(new['job_id'], ids['job_id'])

    def test_invalid_input_and_disabled_server_auth(self):
        bad = self.client.post('/api/watches', json={
            'plot_number':' ', 'city':'Rawalpindi','society':'DHA', 'block':''}, headers=self.auth)
        self.assertEqual(bad.status_code,422)
        self.assertEqual(self.client.get('/api/history',headers={'Authorization':'Bearer wrong'}).status_code,401)


if __name__=='__main__': unittest.main()
