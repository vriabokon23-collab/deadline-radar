from datetime import date

import pytest

from radar.calendar import (
    date_to_week, is_reading_week, resolve_week_reference, term_weeks, week_to_date,
)


@pytest.mark.parametrize('term,week,expected', [
    (1, 1, date(2026, 9, 28)), (1, 7, date(2026, 11, 9)),
    (1, 10, date(2026, 11, 30)), (2, 6, date(2027, 2, 15)),
    (3, 7, date(2027, 6, 7)),
])
def test_calendar_weeks(term, week, expected):
    assert week_to_date(term, week, 'calendar') == expected
    assert date_to_week(expected) == (term, week)


def test_teaching_weeks_skip_reading_week():
    assert week_to_date(1, 10, 'teaching') == date(2026, 12, 7)
    assert week_to_date(2, 6, 'teaching') == date(2027, 2, 22)


@pytest.mark.parametrize('term,week,scheme', [(1, 13, 'calendar'), (1, 12, 'teaching'),
    (3, 8, 'calendar'), (1, 0, 'calendar'), (4, 1, 'calendar'), (1, 1, 'unknown')])
def test_invalid_weeks_raise(term, week, scheme):
    with pytest.raises(ValueError):
        week_to_date(term, week, scheme)


def test_reading_and_ordered_weeks():
    assert is_reading_week(date(2026, 11, 13))
    assert is_reading_week(date(2027, 2, 21))
    assert not is_reading_week(date(2027, 4, 26))
    assert len(term_weeks()) == 30
    assert len(term_weeks(2)) == 11
    assert date_to_week(date(2027, 3, 24)) == (2, 11)
    assert date_to_week(date(2027, 3, 25)) is None


def test_ambiguity_preserves_quote():
    result = resolve_week_reference('Term 1 Week 10')
    assert result.date == date(2026, 11, 30)
    assert result.confidence == 'low'
    assert result.source_quote == 'Term 1 Week 10'
    assert date(2026, 12, 7) in result.alternatives
    assert result.reason
    assert resolve_week_reference('Week 7', term=1).confidence == 'low'
    assert resolve_week_reference('Week 7').confidence == 'low'


def test_explicit_schemes_and_timetable_offset():
    assert week_to_date(1, 5, 'timetable') == date(2026, 9, 28)
    assert week_to_date(2, 20, 'timetable') == date(2027, 1, 11)
    assert week_to_date(3, 35, 'timetable') == date(2027, 4, 26)
    result = resolve_week_reference('UCL timetable week 14')
    assert result.date == date(2026, 11, 30)
    assert result.confidence == 'high'
    assert resolve_week_reference('Term 1 teaching week 10').date == date(2026, 12, 7)
    with pytest.raises(ValueError):
        resolve_week_reference('Term 2 timetable week 5')


def test_explicit_dates_and_unresolvable_input():
    result = resolve_week_reference('week commencing 9 November')
    assert result.date == date(2026, 11, 9)
    assert result.confidence == 'high'
    assert resolve_week_reference('week commencing 15 February 2027').date == date(2027, 2, 15)
    for text in ['Week 99', 'sometime soon', 'week commencing 10 November',
                 'week commencing 31 February', 'Term 1 Week 2 and Week 3']:
        with pytest.raises(ValueError):
            resolve_week_reference(text)
