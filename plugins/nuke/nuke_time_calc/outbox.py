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

from . import storage


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False)


def _sync_directory(path):
    return storage.sync_directory(path)


def _private_directory(path):
    return storage.private_directory(path.absolute())


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
        options = '?mode=rw' + ('&vfs=win32-longpath' if os.name == 'nt' else '')
        db = sqlite3.connect(self.path.as_uri() + options, uri=True, timeout=0.05)
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

    def put(self, batch):
        """Commit a snapshot, or raise without losing preceding records."""
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
        if batch['collector_version'] != '2.0' or batch['policy_version'] != 'comp-v1':
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
        payload = _encode(batch)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT digest FROM batches WHERE session=? AND sequence=?',
                             (batch['session_id'], batch['sequence'])).fetchone()
            if old:
                if old[0] != digest:
                    raise BatchConflict('immutable batch key conflict')
                return
            if db.execute('SELECT COUNT(*) FROM batches WHERE pending=1').fetchone()[0] >= self.max_count:
                raise QueueFull('outbox record capacity reached')
            try:
                db.execute('INSERT INTO batches VALUES (?, ?, ?, ?, 1)',
                           (batch['session_id'], batch['sequence'], payload, digest))
            except sqlite3.OperationalError as exc:
                if getattr(exc, 'sqlite_errorcode', None) == sqlite3.SQLITE_FULL:
                    raise QueueFull('outbox byte capacity reached') from None
                raise
            if db.execute('PRAGMA page_count').fetchone()[0] * db.execute('PRAGMA page_size').fetchone()[0] > self.max_bytes:
                raise QueueFull('outbox byte capacity reached')

    def acknowledge(self, session_id, sequence):
        """Trusted caller only: remove payload after exact accepted/duplicate ack."""
        with self._connect() as db:
            db.execute('UPDATE batches SET pending=0, payload=NULL WHERE session=? AND sequence=?',
                       (session_id, sequence))

    def status(self):
        with self._connect() as db:
            count = db.execute('SELECT COUNT(*) FROM batches WHERE pending=1').fetchone()[0]
            size = db.execute('PRAGMA page_count').fetchone()[0] * db.execute('PRAGMA page_size').fetchone()[0]
            return dict(pending_records=count, storage_bytes=size, max_records=self.max_count, max_bytes=self.max_bytes)

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
