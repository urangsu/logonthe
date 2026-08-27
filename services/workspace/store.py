"""Durable outbox. No tokens, cookies or comment bodies belong in this database."""
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


class WorkspaceStore:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS workspace_connections (
                    account TEXT PRIMARY KEY, metadata TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workspace_artifacts (
                    account TEXT NOT NULL, artifact_key TEXT NOT NULL,
                    file_id TEXT, PRIMARY KEY(account, artifact_key));
                CREATE TABLE IF NOT EXISTS workspace_outbox (
                    account TEXT NOT NULL, run_id TEXT NOT NULL, digest TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT, PRIMARY KEY(account, run_id));
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def set_connection(self, account, metadata):
        metadata = dict(metadata, connection_id=metadata.get('connection_id') or str(uuid4()))
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO workspace_connections VALUES (?,?)",
                       (account, json.dumps(metadata, ensure_ascii=False)))

    def update_connection(self, account, metadata):
        with self.connect() as db:
            row = db.execute('SELECT metadata FROM workspace_connections WHERE account=?', (account,)).fetchone()
            if not row or json.loads(row['metadata']).get('connection_id') != metadata.get('connection_id'):
                return False
            cursor = db.execute('UPDATE workspace_connections SET metadata=? WHERE account=? AND metadata=?',
                (json.dumps(metadata, ensure_ascii=False), account, row['metadata']))
            return cursor.rowcount == 1

    def connections(self):
        with self.connect() as db:
            return {r['account']: json.loads(r['metadata']) for r in
                    db.execute("SELECT * FROM workspace_connections")}

    def disconnect(self, account):
        with self.connect() as db:
            db.execute("DELETE FROM workspace_connections WHERE account=?", (account,))
            db.execute("UPDATE workspace_outbox SET state='cancelled' WHERE account=? AND state!='synced'", (account,))

    def artifact(self, account, key):
        with self.connect() as db:
            row = db.execute("SELECT file_id FROM workspace_artifacts WHERE account=? AND artifact_key=?", (account, key)).fetchone()
            return (row is not None, row['file_id'] if row else None)

    def remember_artifact(self, account, key, file_id=None):
        with self.connect() as db:
            db.execute("INSERT INTO workspace_artifacts VALUES (?,?,?) ON CONFLICT(account,artifact_key) DO UPDATE SET file_id=excluded.file_id",
                       (account, key, file_id))

    def forget_rejected_intent(self, account, key):
        with self.connect() as db:
            db.execute('DELETE FROM workspace_artifacts WHERE account=? AND artifact_key=? AND file_id IS NULL', (account, key))

    def enqueue(self, account, report):
        payload = json.dumps(report, sort_keys=True, ensure_ascii=False).encode()
        digest = hashlib.sha256(payload).hexdigest()
        with self.connect() as db:
            old = db.execute("SELECT digest FROM workspace_outbox WHERE account=? AND run_id=?", (account, report['run_id'])).fetchone()
            if old and old['digest'] != digest:
                raise ValueError('immutable_run_changed')
            db.execute("INSERT OR IGNORE INTO workspace_outbox(account,run_id,digest) VALUES (?,?,?)", (account, report['run_id'], digest))
            db.execute("UPDATE workspace_outbox SET state='pending' WHERE account=? AND run_id=? AND state='cancelled'", (account, report['run_id']))

    def jobs(self, account):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM workspace_outbox WHERE account=? AND state IN ('pending','retry','auth_required','blocked') ORDER BY rowid", (account,))]

    def mark(self, account, run_id, state, error=None):
        with self.connect() as db:
            db.execute("UPDATE workspace_outbox SET state=?,error=?,attempts=attempts+1 WHERE account=? AND run_id=? AND state!='cancelled'", (state, error, account, run_id))
