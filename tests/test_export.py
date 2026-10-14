from datetime import date, timedelta, timezone
import sqlite3

from icalendar import Calendar
import pytest

from radar import db
from radar.export import export_calendar, split_fabricated


def events(payload):
    return Calendar.from_ical(payload).walk('VEVENT')


def test_empty_export():
    parsed = Calendar.from_ical(export_calendar([]))
    assert parsed['version'] == '2.0'
    assert parsed.walk('VEVENT') == []


def test_round_trip_all_day_and_timed_events(conn, assessment):
    db.insert_assessments(conn, [assessment, dict(assessment, due_date='2026-10-15',
                                                due_time='16:00', status='submitted')])
    payload = export_calendar(db.list_assessments(conn))
    calendar = Calendar.from_ical(payload)
    timed, all_day = calendar.walk('VEVENT')
    assert len(calendar.walk('VEVENT')) == 2
    assert len(calendar.walk('VTIMEZONE')) == 1
    assert all_day.decoded('dtstart') == date(2026, 12, 4)
    assert all_day.decoded('dtend') == date(2026, 12, 5)
    assert all_day['dtstart'].params['VALUE'] == 'DATE'
    deadline = timed.decoded('dtstart')
    assert deadline.hour == 16
    assert deadline.utcoffset() == timedelta(hours=1)
    assert deadline.astimezone(timezone.utc).hour == 15
    assert timed['dtstart'].params['TZID'] == 'Europe/London'
    assert str(timed['summary']) == 'COMP0001: Essay'
    description = str(timed['description'])
    assert 'Weighting: 40.0%' in description
    assert 'Estimated hours: 20.0' in description
    assert assessment['source_quote'] in description
    assert timed.decoded('dtstamp').tzinfo is not None


def test_stable_uids_and_updates(conn, assessment):
    aid = db.insert_assessment(conn, assessment)
    first = events(export_calendar(db.list_assessments(conn)))[0]
    db.update_assessment(conn, aid, {'title': 'Updated', 'due_date': '2026-12-05'})
    second = events(export_calendar(db.list_assessments(conn)))[0]
    assert first['uid'] == second['uid']
    assert second.decoded('last-modified') >= first.decoded('last-modified')
    assert 'Updated' in str(second['summary'])
    assert second.decoded('dtstart') == date(2026, 12, 5)


@pytest.mark.parametrize('day,offset', [('2026-12-04', 0), ('2027-05-10', 1)])
def test_winter_and_summer_time(assessment, day, offset):
    event = events(export_calendar([dict(assessment, id=1, due_date=day, due_time='16:00')]))[0]
    assert event.decoded('dtstart').utcoffset() == timedelta(hours=offset)


def test_unicode_escaping_and_line_folding(assessment):
    title = 'Résumé; presentation, group\\work\nsecond line ' + 'long title ' * 30
    quote = 'Line one; 50%, review\\revise\nLine two — café'
    payload = export_calendar([dict(assessment, id=1, title=title, source_quote=quote)])
    event = events(payload)[0]
    assert str(event['summary']) == f'COMP0001: {title}'
    assert quote in str(event['description'])
    assert all(len(line) <= 75 for line in payload.split(b'\r\n'))


@pytest.mark.parametrize('day', ['2027-03-28', '2026-10-25'])
def test_dst_missing_or_ambiguous_hour_raises(assessment, day):
    with pytest.raises(ValueError):
        export_calendar([dict(assessment, id=1, due_date=day, due_time='01:30')])


def test_duplicate_or_missing_ids_rejected(assessment):
    row = dict(assessment, id=1)
    with pytest.raises(ValueError):
        export_calendar([row, row])
    with pytest.raises(KeyError):
        export_calendar([assessment])
    with pytest.raises(ValueError):
        export_calendar([dict(assessment, id=0)])


def test_split_fabricated_partitions_on_source(assessment):
    extracted = dict(assessment, id=1, source='extracted')
    manual = dict(assessment, id=2, source='manual')
    unlabelled = dict(assessment, id=3)
    invented = dict(assessment, id=4, source='fixture')
    genuine, fabricated = split_fabricated([extracted, manual, unlabelled, invented])
    assert [row['id'] for row in genuine] == [1, 2, 3]
    assert [row['id'] for row in fabricated] == [4]


def test_fabricated_rows_are_not_exported(assessment):
    real = dict(assessment, id=1, title='Real Essay', source='extracted')
    invented = dict(assessment, id=2, title='Invented Essay', source='fixture')
    payload = export_calendar([real, invented])
    exported = events(payload)
    assert len(exported) == 1
    assert str(exported[0]['summary']) == 'COMP0001: Real Essay'
    assert b'Invented Essay' not in payload
    assert b'assessment-2@deadline-radar' not in payload


def test_export_of_only_fabricated_rows_is_valid_but_empty(assessment):
    payload = export_calendar([dict(assessment, id=1, source='fixture')])
    parsed = Calendar.from_ical(payload)
    assert parsed['version'] == '2.0'
    assert parsed.walk('VEVENT') == []


def test_fixture_row_from_database_is_excluded(conn, assessment):
    real_id = db.insert_assessment(conn, dict(assessment, source='extracted'))
    db.insert_assessment(conn, dict(assessment, title='Fabricated', source='fixture',
                                    due_date='2026-12-11'))
    rows = db.list_assessments(conn)
    assert {row['source'] for row in rows} == {'extracted', 'fixture'}
    exported = events(export_calendar(rows))
    assert len(exported) == 1
    assert str(exported[0]['uid']) == f'assessment-{real_id}@deadline-radar'


def test_database_progress_to_export_integration(conn, assessment):
    aid = db.insert_assessment(conn, dict(assessment, source='extracted', confidence='low'))
    db.log_progress(conn, aid, 3, 30, 'First draft')
    event = events(export_calendar(db.list_assessments(conn)))[0]
    assert assessment['source_quote'] in str(event['description'])
    assert db.get_assessment(conn, aid)['progress_pct'] == 30


def test_editing_fixture_cannot_make_it_exportable(conn, assessment):
    aid = db.insert_assessment(conn, dict(assessment, source='fixture'))
    with pytest.raises(sqlite3.IntegrityError, match='source is immutable'):
        db.update_assessment(conn, aid, {'source': 'manual', 'title': 'Real Essay'})
    db.update_assessment(conn, aid, {'title': 'Corrected Essay', 'due_date': '2026-12-05'})
    assert events(export_calendar(db.list_assessments(conn))) == []
