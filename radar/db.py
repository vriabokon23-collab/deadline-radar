"""SQLite persistence. Pass an open connection to every CRUD operation.

Use ``closing(connect(path))`` to release connections. Mutating functions commit
their transaction on success and roll it back on failure; callers should not
mix these operations with pending, independently managed transactions.
"""

from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, timezone
import math
from pathlib import Path
import sqlite3
from typing import Any

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / 'data' / 'radar.db'
ASSESSMENT_TYPES = ('essay', 'exam', 'coursework', 'presentation', 'problem_set',
                    'group_project', 'other')
STATUSES = ('not_started', 'in_progress', 'submitted')
SOURCES = ('extracted', 'manual')
CONFIDENCES = ('high', 'medium', 'low')
FIELDS = frozenset(('module_code', 'module_name', 'title', 'type', 'due_date',
                    'due_time', 'weighting_pct', 'word_count', 'estimated_hours',
                    'status', 'progress_pct', 'source', 'confidence',
                    'source_quote', 'notes'))

SCHEMA = """
CREATE TABLE IF NOT EXISTS assessments (
    id INTEGER PRIMARY KEY,
    module_code TEXT NOT NULL CHECK(length(trim(module_code)) > 0),
    module_name TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL CHECK(length(trim(title)) > 0),
    type TEXT NOT NULL CHECK(type IN
        ('essay','exam','coursework','presentation','problem_set','group_project','other')),
    due_date TEXT NOT NULL,
    due_time TEXT,
    weighting_pct REAL CHECK(weighting_pct BETWEEN 0 AND 100),
    word_count INTEGER CHECK(word_count IS NULL OR
        (typeof(word_count) = 'integer' AND word_count >= 0)),
    estimated_hours REAL NOT NULL DEFAULT 0 CHECK(estimated_hours >= 0),
    status TEXT NOT NULL DEFAULT 'not_started'
        CHECK(status IN ('not_started','in_progress','submitted')),
    progress_pct REAL NOT NULL DEFAULT 0 CHECK(progress_pct BETWEEN 0 AND 100),
    source TEXT NOT NULL DEFAULT 'manual' CHECK(source IN ('extracted','manual')),
    confidence TEXT NOT NULL DEFAULT 'high' CHECK(confidence IN ('high','medium','low')),
    source_quote TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK(source != 'extracted' OR length(trim(source_quote)) > 0)
);
CREATE TABLE IF NOT EXISTS progress_log (
    id INTEGER PRIMARY KEY,
    assessment_id INTEGER NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
    logged_at TEXT NOT NULL,
    hours_spent REAL NOT NULL CHECK(hours_spent >= 0),
    progress_pct_after REAL NOT NULL CHECK(progress_pct_after BETWEEN 0 AND 100),
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS assessments_due_date ON assessments(due_date, id);
CREATE INDEX IF NOT EXISTS progress_log_assessment ON progress_log(assessment_id, logged_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def init_db(conn: sqlite3.Connection) -> None:
    """Apply the schema idempotently to a connection without a pending transaction."""
    if conn.in_transaction:
        raise ValueError('initialize the database before starting a transaction')
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.executescript(SCHEMA)


def connect(path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Create parent directories and initialize a database (also supports :memory:)."""
    if str(path) != ':memory:':
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        init_db(conn)
    except Exception:
        conn.close()
        raise
    return conn


def _finite_number(value: Any, name: str, maximum: float | None = None) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} must be a number')
    if not math.isfinite(value) or value < 0 or (maximum is not None and value > maximum):
        raise ValueError(f'{name} is out of range')


def _validate(data: Mapping[str, Any], *, inserting: bool = False) -> dict[str, Any]:
    values = dict(data)
    unknown = values.keys() - FIELDS
    if unknown:
        raise ValueError(f'unknown or immutable fields: {sorted(unknown)}')
    if inserting:
        missing = {'module_code', 'title', 'type', 'due_date'} - values.keys()
        if missing:
            raise ValueError(f'missing required fields: {sorted(missing)}')
    for field in ('module_code', 'title'):
        if field in values and (not isinstance(values[field], str) or not values[field].strip()):
            raise ValueError(f'{field} must be nonempty text')
    for field in ('module_name', 'source_quote', 'notes'):
        if field in values and not isinstance(values[field], str):
            raise ValueError(f'{field} must be text')
    if 'due_date' in values:
        value = values['due_date']
        if type(value) is date:
            value = value.isoformat()
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError('due_date must be an ISO date (YYYY-MM-DD)')
        values['due_date'] = value
    if values.get('due_time') is not None:
        value = values['due_time']
        if isinstance(value, time):
            value = value.isoformat()
        if not isinstance(value, str):
            raise ValueError('due_time must be an ISO local time')
        parsed = time.fromisoformat(value)
        if parsed.tzinfo is not None or parsed.microsecond:
            raise ValueError('due_time must be local time without fractional seconds')
        values['due_time'] = parsed.isoformat(timespec='seconds')
    for field, maximum in (('weighting_pct', 100), ('progress_pct', 100),
                           ('estimated_hours', None)):
        if field in values:
            if field == 'weighting_pct' and values[field] is None:
                continue
            _finite_number(values[field], field, maximum)
    if values.get('word_count') is not None:
        if type(values['word_count']) is not int or values['word_count'] < 0:
            raise ValueError('word_count must be a nonnegative integer')
    # Enum values are also enforced at the SQLite boundary, including direct SQL.
    return values


def _insert(conn: sqlite3.Connection, data: Mapping[str, Any]) -> int:
    values = _validate(data, inserting=True)
    timestamp = _now()
    values.update(created_at=timestamp, updated_at=timestamp)
    columns = ', '.join(values)
    placeholders = ', '.join('?' for _ in values)
    cursor = conn.execute(f'INSERT INTO assessments ({columns}) VALUES ({placeholders})',
                          tuple(values.values()))
    return int(cursor.lastrowid)


def insert_assessment(conn: sqlite3.Connection, data: Mapping[str, Any]) -> int:
    with conn:
        return _insert(conn, data)


def insert_assessments(conn: sqlite3.Connection, rows: Iterable[Mapping[str, Any]]) -> list[int]:
    """Persist a confirmed batch atomically; an invalid row rolls back the whole batch."""
    with conn:
        return [_insert(conn, row) for row in rows]


def get_assessment(conn: sqlite3.Connection, assessment_id: int) -> dict[str, Any] | None:
    row = conn.execute('SELECT * FROM assessments WHERE id = ?', (assessment_id,)).fetchone()
    return dict(row) if row is not None else None


def list_assessments(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(
        'SELECT * FROM assessments ORDER BY due_date, COALESCE(due_time, ?), id', ('00:00:00',))]


def update_assessment(conn: sqlite3.Connection, assessment_id: int,
                      changes: Mapping[str, Any]) -> None:
    values = _validate(changes)
    values['updated_at'] = _now()
    assignments = ', '.join(f'{field} = ?' for field in values)
    with conn:
        cursor = conn.execute(f'UPDATE assessments SET {assignments} WHERE id = ?',
                              (*values.values(), assessment_id))
        if cursor.rowcount == 0:
            raise KeyError(f'assessment {assessment_id} does not exist')


def delete_assessment(conn: sqlite3.Connection, assessment_id: int) -> bool:
    with conn:
        return conn.execute('DELETE FROM assessments WHERE id = ?',
                            (assessment_id,)).rowcount > 0


def log_progress(conn: sqlite3.Connection, assessment_id: int, hours_spent: float,
                 progress_pct_after: float, note: str = '') -> int:
    """Record work and update progress atomically; never infer submitted status."""
    _finite_number(hours_spent, 'hours_spent')
    _finite_number(progress_pct_after, 'progress_pct_after', 100)
    if not isinstance(note, str):
        raise ValueError('note must be text')
    timestamp = _now()
    with conn:
        cursor = conn.execute(
            'UPDATE assessments SET progress_pct = ?, updated_at = ? WHERE id = ?',
            (progress_pct_after, timestamp, assessment_id))
        if cursor.rowcount == 0:
            raise KeyError(f'assessment {assessment_id} does not exist')
        cursor = conn.execute(
            'INSERT INTO progress_log (assessment_id, logged_at, hours_spent, '
            'progress_pct_after, note) VALUES (?, ?, ?, ?, ?)',
            (assessment_id, timestamp, hours_spent, progress_pct_after, note))
        return int(cursor.lastrowid)


def get_progress_history(conn: sqlite3.Connection, assessment_id: int) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(
        'SELECT * FROM progress_log WHERE assessment_id = ? ORDER BY logged_at DESC, id DESC',
        (assessment_id,))]


def total_hours_spent(conn: sqlite3.Connection, assessment_id: int) -> float:
    return float(conn.execute(
        'SELECT COALESCE(SUM(hours_spent), 0) FROM progress_log WHERE assessment_id = ?',
        (assessment_id,)).fetchone()[0])
