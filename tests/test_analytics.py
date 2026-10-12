"""Tests for radar.analytics: weekly workload bucketing and risk scoring.

Term-week fixtures below mirror the UCL 2026/27 calendar from CLAUDE.md
(Term 1: 2026-09-28 to 2026-12-18, reading week commencing 2026-11-09) but are
built locally rather than imported from radar.calendar, since analytics.py has
no hard dependency on that module. See INTEGRATION_NOTES.md.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from radar.analytics import (
    REASON_INSUFFICIENT_TIME,
    REASON_PROGRESS_BEHIND,
    TermWeek,
    assess_risk,
    default_start_date,
    expected_progress_pct,
    remaining_hours,
    week_containing,
    weekly_workload,
)

TERM1_START = date(2026, 9, 28)
TERM1_READING_WEEK_NUMBER = 7


def term1_weeks(count: int = 12) -> list[TermWeek]:
    return [
        TermWeek(
            term="T1",
            week_number=n,
            start_date=TERM1_START + timedelta(weeks=n - 1),
            is_reading_week=(n == TERM1_READING_WEEK_NUMBER),
        )
        for n in range(1, count + 1)
    ]


def make_assessment(
    *,
    due_date: date,
    estimated_hours: float = 20.0,
    progress_pct: float = 0.0,
    status: str = "in_progress",
) -> dict:
    return {
        "due_date": due_date,
        "estimated_hours": estimated_hours,
        "progress_pct": progress_pct,
        "status": status,
    }


# --- remaining_hours -------------------------------------------------------


def test_remaining_hours_scales_with_progress():
    a = make_assessment(due_date=date(2026, 12, 1), estimated_hours=20.0, progress_pct=25)
    assert remaining_hours(a) == 15.0


# --- expected_progress_pct --------------------------------------------------


def test_expected_progress_is_linear_between_start_and_due():
    start = date(2026, 11, 1)
    due = date(2026, 11, 11)  # 10 days later
    assert expected_progress_pct(date(2026, 11, 6), start, due) == 50.0


def test_expected_progress_clamped_before_start_and_after_due():
    start = date(2026, 11, 1)
    due = date(2026, 11, 11)
    assert expected_progress_pct(date(2026, 10, 1), start, due) == 0.0
    assert expected_progress_pct(date(2026, 12, 1), start, due) == 100.0


# --- assess_risk: progress rule --------------------------------------------


def test_on_track_work_is_not_flagged():
    today = date(2026, 11, 6)
    start = date(2026, 11, 1)
    due = date(2026, 11, 11)
    a = make_assessment(due_date=due, estimated_hours=10.0, progress_pct=50)
    result = assess_risk(a, today=today, start_date=start, hours_per_week=10.0)
    assert result.at_risk is False
    assert result.reasons == []


def test_work_25_points_behind_is_flagged_on_progress_rule():
    today = date(2026, 11, 6)
    start = date(2026, 11, 1)
    due = date(2026, 11, 11)
    # expected at today is 50%; actual 25% -> 25 points behind
    a = make_assessment(due_date=due, estimated_hours=10.0, progress_pct=25)
    result = assess_risk(a, today=today, start_date=start, hours_per_week=10.0)
    assert result.at_risk is True
    assert REASON_PROGRESS_BEHIND in result.reasons


def test_exactly_20_points_behind_is_not_flagged():
    today = date(2026, 11, 6)
    start = date(2026, 11, 1)
    due = date(2026, 11, 11)
    # expected at today is 50%; actual 30% -> exactly 20 points behind
    a = make_assessment(due_date=due, estimated_hours=10.0, progress_pct=30)
    result = assess_risk(a, today=today, start_date=start, hours_per_week=10.0)
    assert result.at_risk is False
    assert result.reasons == []


# --- assess_risk: hours rule -------------------------------------------------


def test_40_remaining_hours_and_2_weeks_left_is_flagged_on_hours_rule():
    today = date(2026, 11, 2)
    due = today + timedelta(weeks=2)
    start = today  # keep progress rule from tripping: expected==actual==0
    a = make_assessment(due_date=due, estimated_hours=40.0, progress_pct=0)
    result = assess_risk(a, today=today, start_date=start, hours_per_week=10.0)
    assert result.at_risk is True
    assert REASON_INSUFFICIENT_TIME in result.reasons


def test_overdue_incomplete_assessment_is_flagged():
    today = date(2026, 11, 20)
    due = date(2026, 11, 10)  # 10 days overdue
    a = make_assessment(due_date=due, estimated_hours=10.0, progress_pct=50, status="in_progress")
    result = assess_risk(a, today=today, hours_per_week=10.0)
    assert result.at_risk is True


# --- assess_risk: submitted -------------------------------------------------


def test_submitted_assessment_is_never_flagged():
    today = date(2026, 11, 20)
    due = date(2026, 11, 10)  # overdue and far behind, but submitted
    a = make_assessment(due_date=due, estimated_hours=40.0, progress_pct=10, status="submitted")
    result = assess_risk(a, today=today, hours_per_week=10.0)
    assert result.at_risk is False
    assert result.reasons == []


# --- default_start_date ------------------------------------------------------


def test_default_start_date_backs_off_by_estimated_weeks():
    due = date(2026, 11, 20)
    start = default_start_date(due, estimated_hours=20.0, hours_per_week=10.0)
    assert start == due - timedelta(weeks=2)


# --- week_containing / weekly_workload --------------------------------------


def test_week_containing_finds_the_right_week():
    weeks = term1_weeks()
    # Week 3 starts 2026-10-12; a due date mid-week should land in week 3.
    found = week_containing(date(2026, 10, 15), weeks)
    assert found is not None
    assert found.week_number == 3


def test_week_containing_identifies_reading_week():
    weeks = term1_weeks()
    found = week_containing(date(2026, 11, 10), weeks)
    assert found is not None
    assert found.is_reading_week is True
    assert found.week_number == TERM1_READING_WEEK_NUMBER


def test_week_containing_returns_none_before_term_start():
    weeks = term1_weeks()
    assert week_containing(date(2026, 9, 1), weeks) is None


def test_weekly_workload_buckets_remaining_hours_per_week():
    weeks = term1_weeks()
    assessments = [
        make_assessment(due_date=date(2026, 10, 15), estimated_hours=10.0, progress_pct=0),  # week 3
        make_assessment(due_date=date(2026, 10, 16), estimated_hours=6.0, progress_pct=50),  # week 3
        make_assessment(due_date=date(2026, 11, 10), estimated_hours=8.0, progress_pct=0),  # reading week
    ]
    loads = weekly_workload(assessments, weeks)

    by_week_number = {load.week.week_number: load for load in loads}
    week3 = by_week_number[3]
    assert week3.assessment_count == 2
    assert week3.remaining_hours == pytest.approx(10.0 + 3.0)

    reading_week_load = by_week_number[TERM1_READING_WEEK_NUMBER]
    assert reading_week_load.week.is_reading_week is True
    assert reading_week_load.remaining_hours == pytest.approx(8.0)

    # every input week is represented, even with zero load
    assert len(loads) == len(weeks)


def test_weekly_workload_excludes_submitted_assessments():
    weeks = term1_weeks()
    assessments = [
        make_assessment(due_date=date(2026, 10, 15), estimated_hours=10.0, progress_pct=40, status="submitted"),
    ]
    loads = weekly_workload(assessments, weeks)
    week3 = next(load for load in loads if load.week.week_number == 3)
    assert week3.remaining_hours == 0.0
    assert week3.assessment_count == 0
