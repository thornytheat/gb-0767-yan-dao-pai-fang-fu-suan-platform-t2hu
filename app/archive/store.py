import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from app.archive.manifest import canonical
from app.errors import InputError


class Archive:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS monitoring (id TEXT PRIMARY KEY, packet TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS rule_change ('
                       'id TEXT PRIMARY KEY, stack TEXT NOT NULL, threshold INTEGER NOT NULL, '
                       'threshold_old INTEGER NOT NULL, base_run TEXT NOT NULL, base_epoch INTEGER NOT NULL, '
                       "trial TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', "
                       'created_utc TEXT NOT NULL, approved_utc TEXT)')

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.path, timeout=15)) as db, db:
            yield db

    def lookup(self, run_id):
        with self.connection() as db:
            row = db.execute('SELECT packet FROM monitoring WHERE id = ?', (run_id,)).fetchone()
        return None if row is None else json.loads(row[0])

    def require(self, run_id):
        packet = self.lookup(run_id)
        if packet is None:
            raise InputError('RUN_UNKNOWN', '找不到监测留档', 404)
        return packet

    def commit(self, run_id, packet):
        with self.connection() as db:
            cursor = db.execute('INSERT INTO monitoring VALUES (?, ?) ON CONFLICT(id) DO NOTHING',
                                (run_id, canonical(packet)))
            reused = cursor.rowcount == 0
            record = db.execute('SELECT packet FROM monitoring WHERE id = ?', (run_id,)).fetchone()[0]
        return json.loads(record), reused

    def history(self):
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT json_extract(packet, '$.manifest') FROM monitoring ORDER BY rowid DESC LIMIT 100")]

    @staticmethod
    def _change(row):
        record = dict(zip(('id', 'stack', 'threshold', 'threshold_old', 'base_run', 'base_epoch',
                           'trial', 'status', 'created_utc', 'approved_utc'), row))
        record['trial'] = json.loads(record['trial'])
        return record

    def latest_source_sha(self, stack):
        with self.connection() as db:
            row = db.execute("SELECT json_extract(packet, '$.manifest.sha256') FROM monitoring "
                             "WHERE json_extract(packet, '$.manifest.parameters.stack') = ? "
                             'ORDER BY rowid DESC LIMIT 1', (stack,)).fetchone()
        return None if row is None else json.loads(row[0])

    def approved_epoch(self, stack):
        with self.connection() as db:
            return db.execute("SELECT COUNT(*) FROM rule_change WHERE stack = ? AND status = 'approved'",
                              (stack,)).fetchone()[0]

    def effective_rule(self, stack):
        with self.connection() as db:
            row = db.execute("SELECT id, threshold, approved_utc FROM rule_change "
                             "WHERE stack = ? AND status = 'approved' ORDER BY rowid DESC LIMIT 1", (stack,)).fetchone()
        return None if row is None else {'id': row[0], 'threshold': row[1], 'approved_utc': row[2]}

    def effective_rules(self):
        with self.connection() as db:
            rows = db.execute("SELECT stack, id, threshold, approved_utc FROM rule_change "
                              "WHERE status = 'approved' ORDER BY rowid").fetchall()
        return {stack: {'id': change_id, 'threshold': threshold, 'approved_utc': approved_utc}
                for stack, change_id, threshold, approved_utc in rows}

    def create_change(self, record):
        values = (record['id'], record['stack'], record['threshold'], record['threshold_old'],
                  record['base_run'], record['base_epoch'], canonical(record['trial']),
                  record['status'], record['created_utc'], record['approved_utc'])
        with self.connection() as db:
            cursor = db.execute('INSERT INTO rule_change VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) '
                                'ON CONFLICT(id) DO NOTHING', values)
            reused = cursor.rowcount == 0
            row = db.execute('SELECT * FROM rule_change WHERE id = ?', (record['id'],)).fetchone()
        return self._change(row), reused

    def list_changes(self):
        with self.connection() as db:
            return [self._change(row) for row in db.execute(
                'SELECT * FROM rule_change ORDER BY rowid DESC LIMIT 100')]

    def approve_change(self, change_id, approved_utc):
        with self.connection() as db:
            cursor = db.execute(
                "UPDATE rule_change SET status = 'approved', approved_utc = ? "
                "WHERE id = ? AND status = 'pending' "
                'AND base_epoch = (SELECT COUNT(*) FROM rule_change AS prior '
                "                 WHERE prior.stack = rule_change.stack AND prior.status = 'approved') "
                'AND (SELECT json_extract(base.packet, \'$.manifest.sha256\') FROM monitoring AS base '
                '     WHERE base.id = rule_change.base_run) '
                '  = (SELECT json_extract(recent.packet, \'$.manifest.sha256\') FROM monitoring AS recent '
                "     WHERE json_extract(recent.packet, '$.manifest.parameters.stack') = rule_change.stack "
                '     ORDER BY recent.rowid DESC LIMIT 1)', (approved_utc, change_id))
            row = db.execute('SELECT * FROM rule_change WHERE id = ?', (change_id,)).fetchone()
            if row is None:
                raise InputError('CHANGE_UNKNOWN', '找不到换规试算', 404)
            record = self._change(row)
            if cursor.rowcount:
                return record, False
            if record['status'] == 'approved':
                return record, True
            if record['base_epoch'] != self._epoch(db, record['stack']):
                raise InputError('RULE_CONFLICT', '已有其他门槛核准，本套试算作废，请按新门槛重新试算', 409)
            raise InputError('TRIAL_STALE', '试算源数据已更新，旧试算失效，请按最新留档重新试算', 409)

    @staticmethod
    def _epoch(db, stack):
        return db.execute("SELECT COUNT(*) FROM rule_change WHERE stack = ? AND status = 'approved'",
                          (stack,)).fetchone()[0]
