"""Explicitly configured durable state; never starts threads or selects a live path.

SQLite EXTRA synchronous transactions provide atomic crash recovery and process
locking on a local filesystem validated by the private-storage backend. The
caller chooses a persistent, private root; no live location is selected here.

Defaults: 10,000 pending records; 16 MiB database including immutable digest
tombstones (minimum cap 16 KiB). The rollback journal can temporarily consume
roughly another database-sized allocation, plus fixed directory/marker overhead.
Tombstones are never pruned: eventually exhausting the byte budget fails closed
until an operator reviews capacity. No unacknowledged data is evicted.

Sink success means committed locally, not delivered. Disk/lock errors propagate
(the SQLite busy timeout is 50 ms). The producer must retain/retry on exceptions.
Durability starts at successful put; unsealed producer intervals remain outside
this guarantee. Physical power-cut behavior is not certified. Windows requires
validated private ACLs on a local ACL-capable volume; unsupported backends fail
before writes. POSIX directory fsync does not imply Windows directory durability.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid
from urllib.parse import quote

from . import storage
from .core import POLICY_GRACE_SECONDS


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False)


def _sync_directory(path):
    return storage.sync_directory(path)


def _private_directory(path):
    return storage.private_directory(path.absolute())


def _database_uri(path):
    if os.name == 'nt':
        # win32-longpath increases SQLite's pathname buffer but does not add
        # the Win32 extended-path prefix. Supply it explicitly, escaping the
        # question mark so SQLite cannot mistake it for URI query options.
        return ('file:' + quote(storage._windows_api_path(path), safe='')
                + '?mode=rw&vfs=win32-longpath')
    return path.as_uri() + '?mode=rw'


class CorruptOutbox(RuntimeError):
    """Stored state cannot be trusted; operator recovery required."""


class QueueFull(RuntimeError):
    """Capacity reached; existing records remain untouched."""


class BatchConflict(ValueError):
    """An immutable key was reused with different content."""


class Outbox:
    """Queue partitioned by the exact immutable (server, artist, device) binding.

    Use ``emit=outbox.put`` with the exact schema-2 batch, not its envelope.
    Keys retain canonical JSON digests even after acknowledgement, so repeats
    are idempotent and changed content raises BatchConflict. Payload shape,
    primitive types and profile are checked here; the backend is authoritative
    for temporal, reference, authorization and observation-limit validation.
    """

    def __init__(self, root, *, server, artist_id, device_id, max_records=10000,
                 max_bytes=16 * 1024 * 1024):
        if not storage.supported_platform():
            raise OSError('private storage backend is unavailable')
        if artist_id is None or device_id is None:
            raise ValueError('explicit binding required')
        self._binding = (server, str(artist_id), str(device_id))
        if not all(isinstance(v, str) and v for v in self.binding):
            raise ValueError('explicit binding required')
        if type(max_records) is not int or max_records < 1 or type(max_bytes) is not int or max_bytes < 16384:
            raise ValueError('capacity requires positive records and at least 16384 bytes')
        self.max_count, self.max_bytes = max_records, max_bytes
        root = _private_directory(Path(root))
        partition = hashlib.sha256(_encode(self.binding).encode()).hexdigest()
        self._fingerprint = partition
        directory = _private_directory(root / partition)
        self.path = directory / 'outbox.sqlite3'
        initialized = directory / 'initialized-v1'
        self._initialized = initialized
        database_file = storage.filesystem_path(self.path)
        if storage.filesystem_path(initialized).exists() and (not database_file.exists() or database_file.stat().st_size == 0):
            raise CorruptOutbox('initialized outbox database is missing or truncated')
        storage.create_private_file(self.path)
        self._check_database()
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS binding (fingerprint TEXT NOT NULL)')
            if not db.execute('SELECT fingerprint FROM binding').fetchone():
                db.execute('INSERT INTO binding VALUES (?)', (self._fingerprint,))
            db.execute('CREATE TABLE IF NOT EXISTS batches (session TEXT, sequence INTEGER, payload TEXT, digest TEXT, pending INTEGER NOT NULL, PRIMARY KEY(session, sequence))')
        storage.create_private_file(initialized)

    @property
    def binding(self):
        return self._binding

    def _check_database(self):
        storage.check_private_file(self.path)

    @contextmanager
    def _connect(self):
        self._check_database()
        # The longpath VFS retains normal Windows locking and supports deep
        # installation folders; never select a "-none" (no locking) VFS.
        db = sqlite3.connect(_database_uri(self.path), uri=True, timeout=0.05)
        try:
            db.execute('PRAGMA synchronous=EXTRA')
            pages = max(3, self.max_bytes // db.execute('PRAGMA page_size').fetchone()[0])
            db.execute('PRAGMA max_page_count=%d' % pages)
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='binding' AND type='table'").fetchone():
                rows = db.execute('SELECT fingerprint FROM binding').fetchall()
                if rows != [(self._fingerprint,)]:
                    raise CorruptOutbox('outbox binding mismatch')
            elif storage.filesystem_path(self._initialized).exists():
                raise CorruptOutbox('outbox binding metadata missing')
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _validate(batch):
        required = {'session_id', 'sequence', 'machine', 'collector_version', 'policy_version',
                    'job_id', 'shot_id', 'script_path', 'reason', 'started_at', 'ended_at',
                    'comp_microseconds', 'excluded_gap_microseconds', 'active_spans'}
        if not isinstance(batch, dict) or set(batch) != required:
            raise ValueError('exact schema-2 batch required')
        try:
            if str(uuid.UUID(batch['session_id'])) != batch['session_id']:
                raise ValueError()
            if type(batch['sequence']) is not int or not 0 < batch['sequence'] < 2**63:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ValueError('invalid immutable batch key') from None
        if batch['collector_version'] != '2.0' or (not isinstance(batch['policy_version'], str) or
                                                   batch['policy_version'] not in POLICY_GRACE_SECONDS):
            raise ValueError('unsupported schema-2 profile')
        numbers = {'sequence', 'job_id', 'shot_id', 'comp_microseconds', 'excluded_gap_microseconds'}
        for key in numbers:
            minimum = 1 if key in ('sequence', 'job_id', 'shot_id') else 0
            if type(batch[key]) is not int or not minimum <= batch[key] < 2**63:
                raise ValueError('invalid schema-2 integer')
        for key in required - numbers - {'active_spans'}:
            if not isinstance(batch[key], str):
                raise ValueError('schema-2 strings required')
        if not isinstance(batch['active_spans'], list):
            raise ValueError('schema-2 spans list required')
        for span in batch['active_spans']:
            if (not isinstance(span, dict) or set(span) != {'start', 'end', 'duration_microseconds'}
                    or not isinstance(span['start'], str) or not isinstance(span['end'], str)
                    or type(span['duration_microseconds']) is not int or span['duration_microseconds'] < 0):
                raise ValueError('exact schema-2 spans required')

    def put(self, batch):
        """Accept an immutable upload, including legacy queues and replays."""
        self._validate(batch)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._insert(db, batch)

    def _require_slot(self, db):
        count, = db.execute('SELECT COUNT(*) FROM batches WHERE pending=1').fetchone()
        if count + self._draft_count(db) >= self.max_count:
            raise QueueFull('outbox record capacity reached')

    def _insert(self, db, batch):
        payload = _encode(batch)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        old = db.execute('SELECT digest FROM batches WHERE session=? AND sequence=?',
                         (batch['session_id'], batch['sequence'])).fetchone()
        if old:
            if old[0] != digest:
                raise BatchConflict('immutable batch key conflict')
            return
        self._require_slot(db)
        try:
            db.execute('INSERT INTO batches VALUES (?, ?, ?, ?, 1)',
                       (batch['session_id'], batch['sequence'], payload, digest))
        except sqlite3.OperationalError as exc:
            if getattr(exc, 'sqlite_errorcode', None) == sqlite3.SQLITE_FULL:
                raise QueueFull('outbox byte capacity reached') from None
            raise
        if db.execute('PRAGMA page_count').fetchone()[0] * db.execute('PRAGMA page_size').fetchone()[0] > self.max_bytes:
            raise QueueFull('outbox byte capacity reached')

    def enable_drafts(self):
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS checkpoints (session TEXT PRIMARY KEY, '
                       'ordinal INTEGER NOT NULL, digest TEXT NOT NULL, observed TEXT, draft TEXT)')

            # A concurrent live collector may continue after startup seals its
            # draft: its local ordinal remains intact and its next delta starts
            # a new wire batch. No PID liveness guesses or identity rebinding.
            for session, payload in db.execute(
                    'SELECT session,draft FROM checkpoints WHERE draft IS NOT NULL').fetchall():
                draft = self._read_draft(payload, session)
                self._publish(db, draft)
                db.execute('UPDATE checkpoints SET draft=NULL, observed=? WHERE session=?',
                           (self._context_key(draft), session))

    @staticmethod
    def _pack_draft(draft):
        payload = _encode(draft)
        return hashlib.sha256(payload.encode()).hexdigest() + ':' + payload

    @staticmethod
    def _read_draft(stored, session):
        digest, _, payload = stored.partition(':')
        if hashlib.sha256(payload.encode()).hexdigest() != digest:
            raise CorruptOutbox('draft integrity failure')
        draft = json.loads(payload)
        if draft['session_id'] != session:
            raise CorruptOutbox('draft session mismatch')
        return draft

    def _publish(self, db, draft):
        # Includes acknowledged and quarantined keys: never reuse one.
        last, = db.execute('SELECT MAX(sequence) FROM batches WHERE session=?',
                           (draft['session_id'],)).fetchone()
        db.execute('UPDATE checkpoints SET draft=NULL WHERE session=?', (draft['session_id'],))
        draft['sequence'] = (last or 0) + 1
        self._insert(db, draft)

    @staticmethod
    def _context_key(fragment):
        return _encode([fragment[k] for k in ('machine', 'collector_version', 'policy_version',
                                              'job_id', 'shot_id', 'script_path')])

    @staticmethod
    def _window_seconds(first, last):
        from datetime import datetime
        start = datetime.fromisoformat(first['started_at'].replace('Z', '+00:00'))
        end = datetime.fromisoformat(last['ended_at'].replace('Z', '+00:00'))
        return (end - start).total_seconds()

    def checkpoint(self, fragment):
        """Accept an ordered local delta. Its ordinal is NOT a wire sequence."""
        self._validate(fragment)
        session, ordinal = fragment['session_id'], fragment['sequence']
        digest = hashlib.sha256(_encode(fragment).encode()).hexdigest()
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT ordinal,digest,observed,draft FROM checkpoints WHERE session=?',
                             (session,)).fetchone() or (0, None, None, None)
            previous, fingerprint, observed, payload = row
            if ordinal == previous and digest == fingerprint:
                return
            if ordinal != previous + 1:
                raise BatchConflict('out-of-order local checkpoint')
            draft = self._read_draft(payload, session) if payload else None
            context = self._context_key(fragment)
            if draft and (draft['ended_at'] != fragment['started_at'] or
                          self._context_key(draft) != context or
                          len(draft['active_spans']) + len(fragment['active_spans']) > 256 or
                          self._window_seconds(draft, fragment) > 86400 or
                          fragment['reason'] == 'excluded_gap'):
                self._publish(db, draft)
                draft = None
            if draft is None:
                draft = dict(fragment, active_spans=list(fragment['active_spans']))
            else:
                spans = draft['active_spans']
                for span in fragment['active_spans']:
                    if spans and spans[-1]['end'] == span['start']:
                        spans[-1]['end'] = span['end']
                        spans[-1]['duration_microseconds'] += span['duration_microseconds']
                    else:
                        spans.append(dict(span))
                draft['comp_microseconds'] += fragment['comp_microseconds']
                draft['excluded_gap_microseconds'] += fragment['excluded_gap_microseconds']
                draft.update(ended_at=fragment['ended_at'], reason=fragment['reason'])
            boundary = fragment['reason'] not in ('checkpoint', 'script_save')
            elapsed = self._window_seconds(draft, fragment)
            if (not draft['comp_microseconds'] and not draft['excluded_gap_microseconds']
                    and observed == context and fragment['reason'] != 'clock_epoch_end'):
                draft = None
            elif boundary or elapsed >= 300:
                self._publish(db, draft)
                observed, draft = context, None
            if draft and not db.execute('SELECT 1 FROM checkpoints WHERE session=? AND draft IS NOT NULL',
                                        (session,)).fetchone():
                self._require_slot(db)
            if boundary:
                observed = None
            db.execute('INSERT OR REPLACE INTO checkpoints VALUES (?,?,?,?,?)',
                       (session, ordinal, digest, observed, self._pack_draft(draft) if draft else None))

    def acknowledge(self, session_id, sequence):
        """Trusted caller only: remove payload after exact accepted/duplicate ack."""
        with self._connect() as db:
            db.execute('UPDATE batches SET pending=0, payload=NULL WHERE session=? AND sequence=?',
                       (session_id, sequence))

    def quarantine(self, session_id, sequence):
        """Keep rejected payloads locally, outside the delivery queue."""
        with self._connect() as db:
            db.execute('UPDATE batches SET pending=-1 WHERE session=? AND sequence=? AND pending=1',
                       (session_id, sequence))

    @staticmethod
    def _draft_count(db):
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='checkpoints'").fetchone():
            return 0
        return db.execute('SELECT COUNT(*) FROM checkpoints WHERE draft IS NOT NULL').fetchone()[0]

    def status(self):
        with self._connect() as db:
            count = db.execute('SELECT COUNT(*) FROM batches WHERE pending=1').fetchone()[0]
            rejected = db.execute('SELECT COUNT(*) FROM batches WHERE pending=-1').fetchone()[0]
            size = db.execute('PRAGMA page_count').fetchone()[0] * db.execute('PRAGMA page_size').fetchone()[0]
            drafts = self._draft_count(db)
            acknowledged, = db.execute('SELECT COUNT(*) FROM batches WHERE pending=0').fetchone()
            return dict(pending_records=count, quarantined_records=rejected, draft_records=drafts,
                        acknowledged_fingerprints=acknowledged, storage_bytes=size,
                        max_records=self.max_count, max_bytes=self.max_bytes)

    def pending(self):
        with self._connect() as db:
            result = []
            for session, sequence, payload, digest in db.execute('SELECT session, sequence, payload, digest FROM batches WHERE pending=1 ORDER BY rowid'):
                try:
                    value = json.loads(payload)
                    if hashlib.sha256(payload.encode()).hexdigest() != digest or value['session_id'] != session or value['sequence'] != sequence:
                        raise ValueError()
                except (ValueError, TypeError, KeyError):
                    raise CorruptOutbox('outbox record integrity failure') from None
                result.append(value)
            return result
