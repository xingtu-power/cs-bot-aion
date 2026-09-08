import os
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from csapp import db


# 独立保留升级前的表定义，避免跟随生产 SCHEMA 改动掩盖迁移回归。
LEGACY_SCHEMA = """
CREATE TABLE leads (
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL, market TEXT NOT NULL,
  channel TEXT NOT NULL DEFAULT 'web', intent TEXT NOT NULL,
  name TEXT, phone TEXT, email TEXT, consent_at TEXT, consent_version TEXT,
  status TEXT DEFAULT 'new', raw_json TEXT, dedupe_key TEXT,
  created_at TEXT NOT NULL, UNIQUE(dedupe_key, market)
);
"""


class DatabaseMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = os.path.join(self.temp.name, 'kd.db')
        self.path_patch = patch.object(db, 'DB_PATH', self.path)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)

    def create_legacy_db(self):
        with sqlite3.connect(self.path) as conn:
            conn.executescript(LEGACY_SCHEMA)
            conn.execute("""INSERT INTO leads
                (id, session_id, market, intent, phone, dedupe_key, created_at)
                VALUES ('old', 'session-old', 'AU', 'buy', '0412345678',
                        'old-key', '2026-01-01T00:00:00Z')""")
            conn.row_factory = sqlite3.Row
            return dict(conn.execute('SELECT * FROM leads').fetchone())

    def assert_lead_round_trip(self):
        record = dict(leadId='new', sessionId='session-new', market='AU',
                      intent='buy', phone='0498765432', userId='visitor-1',
                      dedupeKey='new-key')
        self.assertTrue(db.insert_lead(record))
        self.assertFalse(db.insert_lead(dict(record, leadId='duplicate')))
        found = db.find_recent_lead_by_user('visitor-1', 'AU')
        self.assertEqual(found['id'], 'new')
        self.assertEqual(found['user_id'], 'visitor-1')

    def test_new_database_and_repeated_initialization(self):
        db.init_db()
        self.assert_lead_round_trip()
        db.init_db()
        self.assertEqual(db.count('leads'), 1)
        self.assertEqual(db.count('escalations'), 0)
        self.assertEqual(db.count('rescue_tickets'), 0)
        self.assertEqual(db.find_recent_lead_by_user('visitor-1')['id'], 'new')

    def test_legacy_database_preserves_data_and_deduplication(self):
        original = self.create_legacy_db()
        db.init_db()
        db.init_db()
        migrated = db.list_leads()[0]
        self.assertIsNone(migrated.pop('user_id'))
        self.assertEqual(migrated, original)
        self.assertFalse(db.insert_lead(dict(
            leadId='old-duplicate', sessionId='another', market='AU',
            intent='buy', dedupeKey='old-key')))
        self.assert_lead_round_trip()
        self.assertEqual(db.count('leads'), 2)

    def test_concurrent_legacy_initialization(self):
        self.create_legacy_db()
        barrier = threading.Barrier(8)

        def initialize():
            barrier.wait(timeout=5)
            db.init_db()

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(initialize) for _ in range(8)]
            for future in futures:
                future.result(timeout=10)
        self.assertEqual(db.count('leads'), 1)
        self.assert_lead_round_trip()


if __name__ == '__main__':
    unittest.main()
