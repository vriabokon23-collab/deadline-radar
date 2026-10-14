from datetime import date
import sqlite3

import pytest

from radar import db


def test_schema_is_idempotent_and_has_exact_columns(conn):
    db.init_db(conn)
    db.init_db(conn)
    assert conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1
    assert {r['name'] for r in conn.execute('PRAGMA table_info(assessments)')} == (
        db.FIELDS | {'id', 'created_at', 'updated_at'})
    assert {r['name'] for r in conn.execute('PRAGMA table_info(progress_log)')} == {
        'id', 'assessment_id', 'logged_at', 'hours_spent', 'progress_pct_after', 'note'}


def test_crud_and_persistence(tmp_path, assessment):
    path = tmp_path / 'db.sqlite'
    with db.connect(path) as conn:
        ids = db.insert_assessments(conn, [assessment, dict(assessment, due_date='2026-10-01')])
        row = db.get_assessment(conn, ids[0])
        assert row['status'] == 'not_started'
        assert row['progress_pct'] == 0
        assert row['confidence'] == 'high'
        assert row['source'] == 'manual'
        db.update_assessment(conn, ids[0], {'title': 'Revised', 'due_date': date(2026, 12, 5)})
        changed = db.get_assessment(conn, ids[0])
        assert changed['updated_at'] > row['updated_at']
        assert changed['created_at'] == row['created_at']
        assert changed['due_date'] == '2026-12-05'
        assert [r['id'] for r in db.list_assessments(conn)] == ids[::-1]
    conn.close()
    reopened = db.connect(path)
    try:
        assert db.get_assessment(reopened, ids[0])['title'] == 'Revised'
        assert db.delete_assessment(reopened, ids[0])
        assert not db.delete_assessment(reopened, ids[0])
        assert db.get_assessment(reopened, ids[0]) is None
    finally:
        reopened.close()


@pytest.mark.parametrize('field,value', [('type', 'invalid'), ('confidence', 'certain'),
    ('status', 'done'), ('source', 'unknown')])
def test_enum_constraints(conn, assessment, field, value):
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_assessment(conn, dict(assessment, **{field: value}))
    assert db.list_assessments(conn) == []


@pytest.mark.parametrize('field,value', [('weighting_pct', -1), ('weighting_pct', 101),
    ('progress_pct', 101), ('estimated_hours', -1), ('estimated_hours', float('nan')),
    ('estimated_hours', float('inf')), ('word_count', 1.5), ('word_count', -1),
    ('due_date', '2026-02-30'), ('due_date', '20261204'), ('due_time', '25:00'),
    ('due_time', '16:00+00:00'), ('title', ''), ('id', 2), ('updated_at', 'yesterday')])
def test_invalid_fields_rejected(conn, assessment, field, value):
    with pytest.raises(ValueError):
        db.insert_assessment(conn, dict(assessment, **{field: value}))
    assert db.list_assessments(conn) == []


def test_batch_is_atomic(conn, assessment):
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_assessments(conn, [assessment, dict(assessment, confidence='invalid')])
    assert db.list_assessments(conn) == []
    with pytest.raises(ValueError):
        db.insert_assessments(conn, [assessment, {'title': 'incomplete'}])
    assert db.list_assessments(conn) == []


def test_extracted_rows_require_source_quote(conn, assessment):
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_assessment(conn, dict(assessment, source='extracted', source_quote=' '))
    assert db.list_assessments(conn) == []


@pytest.mark.parametrize('source', db.MACHINE_SOURCES)
def test_machine_sources_require_source_quote(conn, assessment, source):
    """Rows the app generated must cite where the date came from."""
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_assessment(conn, dict(assessment, source=source, source_quote=' '))
    assert db.list_assessments(conn) == []


def test_fixture_source_is_storable_and_distinct(conn, assessment):
    """Fabricated rows need their own provenance value, not 'extracted'."""
    assert 'fixture' in db.SOURCES
    fixture_id = db.insert_assessment(conn, dict(assessment, source='fixture'))
    extracted_id = db.insert_assessment(conn, dict(assessment, source='extracted'))

    assert db.get_assessment(conn, fixture_id)['source'] == 'fixture'
    assert db.get_assessment(conn, extracted_id)['source'] == 'extracted'
    assert [r['id'] for r in db.list_assessments(conn)
            if r['source'] == 'fixture'] == [fixture_id]


@pytest.mark.parametrize('source', db.SOURCES)
@pytest.mark.parametrize('replacement', db.SOURCES)
def test_edits_preserve_original_provenance(conn, assessment, source, replacement):
    aid = db.insert_assessment(conn, dict(assessment, source=source))
    before = db.get_assessment(conn, aid)
    if source == replacement:
        db.update_assessment(conn, aid, {'source': replacement, 'title': 'Edited'})
        assert db.get_assessment(conn, aid)['title'] == 'Edited'
    else:
        with pytest.raises(sqlite3.IntegrityError, match='source is immutable'):
            db.update_assessment(conn, aid, {'source': replacement, 'title': 'Edited'})
        assert db.get_assessment(conn, aid) == before
        with pytest.raises(sqlite3.IntegrityError, match='source is immutable'), conn:
            conn.execute('UPDATE assessments SET source = ? WHERE id = ?',
                         (replacement, aid))
    assert db.get_assessment(conn, aid)['source'] == source


# Pre-fixture-provenance schema: CREATE TABLE IF NOT EXISTS never revisits a CHECK.
_LEGACY_ASSESSMENTS = """
CREATE TABLE assessments (
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
CREATE TABLE progress_log (
    id INTEGER PRIMARY KEY,
    assessment_id INTEGER NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
    logged_at TEXT NOT NULL,
    hours_spent REAL NOT NULL CHECK(hours_spent >= 0),
    progress_pct_after REAL NOT NULL CHECK(progress_pct_after BETWEEN 0 AND 100),
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX assessments_due_date ON assessments(due_date, id);
"""


def _legacy_database(path):
    legacy = sqlite3.connect(str(path))
    legacy.executescript(_LEGACY_ASSESSMENTS)
    legacy.execute(
        'INSERT INTO assessments (module_code, title, type, due_date, estimated_hours, '
        'status, progress_pct, source, confidence, source_quote, notes, created_at, '
        "updated_at) VALUES ('COMP0001', 'Legacy Essay', 'essay', '2026-12-04', 20, "
        "'in_progress', 40, 'manual', 'high', '', 'keep me', 'then', 'then')")
    legacy.execute(
        'INSERT INTO progress_log (assessment_id, logged_at, hours_spent, '
        "progress_pct_after, note) VALUES (1, 'then', 8, 40, 'drafted intro')")
    legacy.commit()
    return legacy


def test_legacy_database_rejects_fixture_before_migration(tmp_path, assessment):
    """Guards the premise: without a rebuild the new value would raise, not persist."""
    path = tmp_path / 'legacy.db'
    legacy = _legacy_database(path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            legacy.execute(
                'INSERT INTO assessments (module_code, title, type, due_date, '
                'estimated_hours, status, progress_pct, source, confidence, source_quote, '
                "notes, created_at, updated_at) VALUES ('COMP0004', 'PS1', 'problem_set', "
                "'2026-10-30', 15, 'not_started', 0, 'fixture', 'high', 'q', '', 'n', 'n')")
    finally:
        legacy.close()


def test_connect_migrates_legacy_source_check(tmp_path, assessment):
    """Opening an old database must widen the CHECK without losing rows."""
    path = tmp_path / 'legacy.db'
    _legacy_database(path).close()

    conn = db.connect(path)
    try:
        # Existing data survived the table rebuild, including the FK-linked log.
        rows = db.list_assessments(conn)
        assert len(rows) == 1
        assert rows[0]['title'] == 'Legacy Essay'
        assert rows[0]['progress_pct'] == 40
        assert rows[0]['source'] == 'manual'
        history = db.get_progress_history(conn, rows[0]['id'])
        assert [h['note'] for h in history] == ['drafted intro']

        # ...and the migrated table now admits fixture provenance.
        fixture_id = db.insert_assessment(conn, dict(assessment, source='fixture'))
        assert db.get_assessment(conn, fixture_id)['source'] == 'fixture'

        with pytest.raises(sqlite3.IntegrityError, match='source is immutable'):
            db.update_assessment(conn, fixture_id, {'source': 'extracted'})
        db.init_db(conn)
        with pytest.raises(sqlite3.IntegrityError, match='source is immutable'):
            db.update_assessment(conn, rows[0]['id'], {'source': 'fixture',
                                                     'source_quote': 'invented'})

        # Foreign keys are back on and the enum is still enforced.
        assert conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        with pytest.raises(sqlite3.IntegrityError):
            db.insert_assessment(conn, dict(assessment, source='nonsense'))

        # Migration is a no-op once applied.
        assert db._migrate_source_check(conn) is False
    finally:
        conn.close()


def test_progress_log_survives_migration_without_cascade(tmp_path):
    """The rebuild drops the assessments table; the cascade must not fire."""
    path = tmp_path / 'legacy.db'
    _legacy_database(path).close()
    conn = db.connect(path)
    try:
        assert conn.execute('SELECT COUNT(*) FROM progress_log').fetchone()[0] == 1
    finally:
        conn.close()


def test_failed_migration_restores_legacy_schema_and_data(tmp_path):
    conn = _legacy_database(tmp_path / 'legacy.db')
    try:
        # Old connections could write orphan logs with foreign keys disabled.
        conn.execute('UPDATE progress_log SET assessment_id = 999')
        conn.commit()
        schema = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'assessments'").fetchone()[0]
        assessments = conn.execute('SELECT * FROM assessments').fetchall()
        history = conn.execute('SELECT * FROM progress_log').fetchall()
        with pytest.raises(sqlite3.IntegrityError, match='referential integrity'):
            db.init_db(conn)
        assert conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'assessments'").fetchone()[0] == schema
        assert [tuple(row) for row in conn.execute('SELECT * FROM assessments')] == assessments
        assert [tuple(row) for row in conn.execute('SELECT * FROM progress_log')] == history
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'assessments_migrated'").fetchone() is None
        assert conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        assert not conn.in_transaction
        # Repairing the orphan allows the same database to migrate on retry.
        with conn:
            conn.execute('UPDATE progress_log SET assessment_id = 1')
        db.init_db(conn)
        assert db._migrate_source_check(conn) is False
    finally:
        conn.close()


def test_interrupted_migration_can_be_retried(tmp_path):
    path = tmp_path / 'legacy.db'
    conn = _legacy_database(path)
    # Simulate a failure after the replacement table has been populated.
    conn.set_authorizer(
        lambda action, table, *args: sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_DROP_TABLE and table == 'assessments'
        else sqlite3.SQLITE_OK
    )
    try:
        with pytest.raises(sqlite3.DatabaseError, match='not authorized'):
            db.init_db(conn)
        assert not conn.in_transaction
        assert conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'assessments_migrated'"
        ).fetchone() is None
    finally:
        conn.close()

    # Retry through a fresh connection, as after restarting the application.
    conn = db.connect(path)
    try:
        assert db.get_assessment(conn, 1)['title'] == 'Legacy Essay'
        assert db.get_progress_history(conn, 1)[0]['note'] == 'drafted intro'
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        assert db._migrate_source_check(conn) is False
    finally:
        conn.close()


def test_progress_logging_and_cascade(conn, assessment):
    aid = db.insert_assessment(conn, assessment)
    before = db.get_assessment(conn, aid)
    first = db.log_progress(conn, aid, 2.5, 25, 'Outline complete')
    second = db.log_progress(conn, aid, 1, 100)
    row = db.get_assessment(conn, aid)
    assert row['progress_pct'] == 100
    assert row['updated_at'] > before['updated_at']
    assert row['status'] == 'not_started'  # Finished does not mean submitted.
    history = db.get_progress_history(conn, aid)
    assert [r['id'] for r in history] == [second, first]
    assert history[1]['note'] == 'Outline complete'
    assert db.total_hours_spent(conn, aid) == 3.5
    db.delete_assessment(conn, aid)
    assert db.get_progress_history(conn, aid) == []
    assert db.total_hours_spent(conn, aid) == 0


@pytest.mark.parametrize('hours,progress', [(-1, 0), (1, -1), (1, 101), (float('inf'), 2)])
def test_invalid_progress_is_atomic(conn, assessment, hours, progress):
    aid = db.insert_assessment(conn, assessment)
    before = db.get_assessment(conn, aid)
    with pytest.raises(ValueError):
        db.log_progress(conn, aid, hours, progress)
    assert db.get_assessment(conn, aid) == before
    assert db.get_progress_history(conn, aid) == []


def test_progress_failure_rolls_back_parent(conn, assessment):
    aid = db.insert_assessment(conn, assessment)
    before = db.get_assessment(conn, aid)
    conn.execute("CREATE TRIGGER reject_log BEFORE INSERT ON progress_log "
                 "BEGIN SELECT RAISE(ABORT, 'forced failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        db.log_progress(conn, aid, 1, 50)
    assert db.get_assessment(conn, aid) == before
    assert db.get_progress_history(conn, aid) == []


def test_missing_assessment_and_direct_sql_constraints(conn, assessment):
    with pytest.raises(KeyError):
        db.log_progress(conn, 123, 1, 10)
    with pytest.raises(KeyError):
        db.update_assessment(conn, 123, {'notes': 'hello'})
    aid = db.insert_assessment(conn, assessment)
    with pytest.raises(sqlite3.IntegrityError), conn:
        conn.execute('UPDATE assessments SET progress_pct = 101 WHERE id = ?', (aid,))
    with pytest.raises(sqlite3.IntegrityError), conn:
        conn.execute('INSERT INTO progress_log VALUES (NULL, 999, ?, 1, 10, ?)', ('now', ''))


def test_nullable_fields_and_memory_database(assessment):
    conn = db.connect(':memory:')
    try:
        aid = db.insert_assessment(conn, dict(assessment, weighting_pct=None, word_count=None,
                                              due_time='16:00'))
        row = db.get_assessment(conn, aid)
        assert row['weighting_pct'] is None
        assert row['word_count'] is None
        assert row['due_time'] == '16:00:00'
    finally:
        conn.close()


def test_deleted_assessment_ids_are_not_reused_after_reopening(tmp_path, assessment):
    path = tmp_path / 'identities.db'
    conn = db.connect(path)
    first_id = db.insert_assessment(conn, assessment)
    db.delete_assessment(conn, first_id)
    conn.close()
    conn = db.connect(path)
    try:
        second_id = db.insert_assessment(conn, assessment)
        assert second_id > first_id
        db.delete_assessment(conn, second_id)
        db.init_db(conn)
        assert db.insert_assessment(conn, assessment) > second_id
    finally:
        conn.close()


def test_fixture_capable_database_migrates_non_reusable_ids(tmp_path, assessment):
    path = tmp_path / 'fixture-era.db'
    legacy = sqlite3.connect(path)
    legacy.executescript(db.SCHEMA.replace('PRIMARY KEY AUTOINCREMENT', 'PRIMARY KEY'))
    legacy.row_factory = sqlite3.Row
    aid = db.insert_assessment(legacy, dict(assessment, source='fixture'))
    db.log_progress(legacy, aid, 2, 10, 'Preserve this history')
    legacy.close()
    conn = db.connect(path)
    try:
        assert db.get_assessment(conn, aid)['source'] == 'fixture'
        assert db.get_progress_history(conn, aid)[0]['note'] == 'Preserve this history'
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        db.delete_assessment(conn, aid)
        assert db.insert_assessment(conn, assessment) > aid
        assert db._migrate_source_check(conn) is False
    finally:
        conn.close()
