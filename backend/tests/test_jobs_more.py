import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crawler import CrawlOutput, CrawlStats
from engine import PlotQuery
import database
import jobs

class QueueTests(TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'PLOTWATCH_DATA_DIR':self.t.name,'SERPER_API_KEY':'fake'})
        self.env.start()
        jobs.init_jobs()
    def tearDown(self):
        self.env.stop(); self.t.cleanup()
    def test_idempotent_queue_and_complete_schedule(self):
        wid=database.add_watch(PlotQuery('20','Islamabad','DHA Phase 2','Sector A'),mode='hybrid')
        a=jobs.queue_watch(wid)
        self.assertEqual(a,jobs.queue_watch(wid))
        with patch('jobs.scan',return_value=CrawlOutput([],CrawlStats(fetched=1,candidates=1))):
            jobs.process_next_job()
        j=jobs.read_job(a)
        self.assertEqual(j['status'],'completed')
        w=database.get_watch(wid)
        delta=datetime.fromisoformat(w['next_check_at'])-datetime.fromisoformat(w['last_checked_at'])
        self.assertEqual(delta.days,15)
        self.assertEqual(database.due_watches(),[])
    def test_partial_scan_retries_tomorrow(self):
        wid=database.add_watch(PlotQuery('88','Rawalpindi','Bahria Town Phase 8','Block J'),mode='hybrid')
        job=jobs.queue_watch(wid)
        with patch('jobs.scan',return_value=CrawlOutput([],CrawlStats(fetched=1,blocked=1,candidates=1))):
            jobs.process_next_job()
        w=database.get_watch(wid)
        delta=datetime.fromisoformat(w['next_check_at'])-datetime.fromisoformat(w['last_checked_at'])
        self.assertEqual(delta.days,1)
        self.assertEqual(jobs.read_job(job)['status'],'completed')
        self.assertIn('Partial',w['last_status'])
