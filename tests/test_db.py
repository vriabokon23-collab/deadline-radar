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
