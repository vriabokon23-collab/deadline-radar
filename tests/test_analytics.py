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
    suggested_start_date,
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
    source: str | None = None,
) -> dict:
    assessment = {
        "due_date": due_date,
        "estimated_hours": estimated_hours,
        "progress_pct": progress_pct,
        "status": status,
    }
    if source is not None:
        assessment["source"] = source
    return assessment


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


def test_default_start_date_with_zero_hours_per_week_is_the_due_date():
    """A zero weekly budget can't back-plan anything; don't divide by zero."""
    due = date(2026, 11, 20)
    assert default_start_date(due, estimated_hours=20.0, hours_per_week=0.0) == due


# --- suggested_start_date ----------------------------------------------------

# Term 1 weeks used below, for reading the expected dates off quickly:
# W1 09-28  W2 10-05  W3 10-12  W4 10-19  W5 10-26  W6 11-02
# W7 11-09 (reading)  W8 11-16  W9 11-23  W10 11-30  W11 12-07  W12 12-14

DUE_IN_WEEK_10 = date(2026, 12, 4)


def test_an_uncontested_assessment_takes_the_weeks_its_hours_need():
    """20 hours at 10h/week needs the due week plus one before it."""
    start = suggested_start_date(
        make_assessment(due_date=DUE_IN_WEEK_10, estimated_hours=20.0),
        others=[],
        term_weeks=term1_weeks(),
    )
    assert start == date(2026, 11, 23)


def test_the_suggestion_is_always_the_monday_of_a_term_week():
    weeks = term1_weeks()
    mondays = {week.start_date for week in weeks}
    for hours in (5.0, 20.0, 55.0, 500.0):
        start = suggested_start_date(
            make_assessment(due_date=DUE_IN_WEEK_10, estimated_hours=hours),
            others=[],
            term_weeks=weeks,
        )
        assert start in mondays


def test_other_work_due_in_the_same_week_pushes_the_start_earlier():
    """A week carrying another deadline offers less than the full weekly budget."""
    target = make_assessment(due_date=DUE_IN_WEEK_10, estimated_hours=20.0)
    rival = make_assessment(due_date=date(2026, 12, 2), estimated_hours=20.0)

    alone = suggested_start_date(target, others=[], term_weeks=term1_weeks())
    crowded = suggested_start_date(target, others=[rival], term_weeks=term1_weeks())

    assert alone == date(2026, 11, 23)
    assert crowded == date(2026, 11, 16)


def test_submitted_work_does_not_crowd_the_week_it_was_due_in():
    rival = make_assessment(
        due_date=date(2026, 12, 2), estimated_hours=20.0, status="submitted"
    )
    start = suggested_start_date(
        make_assessment(due_date=DUE_IN_WEEK_10, estimated_hours=20.0),
        others=[rival],
        term_weeks=term1_weeks(),
    )
    assert start == date(2026, 11, 23)


def test_reading_week_is_planned_around_when_the_work_fits_without_it():
    """40 hours reaches back past the reading week rather than through it."""
    start = suggested_start_date(
        make_assessment(due_date=DUE_IN_WEEK_10, estimated_hours=40.0),
        others=[],
        term_weeks=term1_weeks(),
    )
    # W10, W9, W8 then W6 — four working weeks, skipping W7 (09-11).
    assert start == date(2026, 11, 2)


def test_reading_week_is_used_when_nothing_else_has_capacity():
    """Last resort only: a start date inside the term beats one invented outside it."""
    weeks = [
        TermWeek("T1", n, TERM1_START + timedelta(weeks=n - 1), is_reading_week=(n == 2))
        for n in range(1, 5)
    ]
    saturated = [
        make_assessment(due_date=date(2026, 9, 30), estimated_hours=10.0),
        make_assessment(due_date=date(2026, 10, 14), estimated_hours=10.0),
        make_assessment(due_date=date(2026, 10, 21), estimated_hours=10.0),
    ]
    start = suggested_start_date(
        make_assessment(due_date=date(2026, 10, 23), estimated_hours=10.0),
        others=saturated,
        term_weeks=weeks,
    )
    assert start == date(2026, 10, 5)


def test_logged_progress_does_not_improve_the_suggested_start():
    """Otherwise a half-finished assessment would look healthier than an untouched one."""
    untouched = make_assessment(due_date=DUE_IN_WEEK_10, estimated_hours=40.0)
    nearly_done = make_assessment(
        due_date=DUE_IN_WEEK_10, estimated_hours=40.0, progress_pct=80.0
    )
    weeks = term1_weeks()
    assert suggested_start_date(untouched, [], weeks) == suggested_start_date(
        nearly_done, [], weeks
    )


def test_work_that_cannot_fit_before_the_deadline_points_at_the_first_week():
    """500 hours needed more term than exists; say so inside the calendar."""
    start = suggested_start_date(
        make_assessment(due_date=date(2026, 10, 16), estimated_hours=500.0),
        others=[],
        term_weeks=term1_weeks(),
    )
    assert start == TERM1_START


def test_a_due_date_outside_the_term_calendar_falls_back_to_the_flat_estimate():
    due = date(2026, 9, 1)
    start = suggested_start_date(
        make_assessment(due_date=due, estimated_hours=20.0),
        others=[],
        term_weeks=term1_weeks(),
    )
    assert start == default_start_date(due, 20.0)


def test_an_assessment_with_no_estimated_hours_needs_no_run_up():
    start = suggested_start_date(
        make_assessment(due_date=DUE_IN_WEEK_10, estimated_hours=0.0),
        others=[],
        term_weeks=term1_weeks(),
    )
    assert start == DUE_IN_WEEK_10


def test_suggested_start_date_with_zero_hours_per_week_is_the_due_date():
    start = suggested_start_date(
        make_assessment(due_date=DUE_IN_WEEK_10, estimated_hours=20.0),
        others=[],
        term_weeks=term1_weeks(),
        hours_per_week=0.0,
    )
    assert start == DUE_IN_WEEK_10


def test_a_crowded_term_can_flag_work_the_flat_estimate_calls_healthy():
    """The point of feeding this into assess_risk: 10 hours is not 10 hours.

    30% done on a 20-hour essay due in a fortnight passes the flat estimate,
    which assumes the fortnight is empty. It is not — the two weeks before the
    deadline each carry another deadline, so the work had to start in October.
    """
    weeks = term1_weeks()
    target = make_assessment(
        due_date=DUE_IN_WEEK_10, estimated_hours=20.0, progress_pct=30.0
    )
    rivals = [
        make_assessment(due_date=date(2026, 12, 2), estimated_hours=10.0),
        make_assessment(due_date=date(2026, 11, 25), estimated_hours=10.0),
    ]
    today = date(2026, 11, 20)

    flat = assess_risk(target, today=today)
    planned = assess_risk(
        target, today=today, start_date=suggested_start_date(target, rivals, weeks)
    )

    assert flat.at_risk is False
    assert planned.reasons == [REASON_PROGRESS_BEHIND]


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


def test_week_containing_attributes_inter_term_gap_to_the_earlier_term():
    """Documented gap behaviour (INTEGRATION_NOTES.md #4): a due date that falls

    in a break between terms has no week of its own, so it lands on the last
    modeled week of the term before it rather than being reported as unbucketed.
    """
    t1_last = TermWeek(term="T1", week_number=12, start_date=date(2026, 12, 14))
    t2_first = TermWeek(term="T2", week_number=1, start_date=date(2027, 1, 11))
    weeks = [t1_last, t2_first]

    during_winter_break = date(2026, 12, 25)
    found = week_containing(during_winter_break, weeks)

    assert found is t1_last


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


# --- fabricated fixture rows in the workload -------------------------------


def test_weekly_workload_reports_fabricated_share_of_a_week():
    weeks = term1_weeks()
    assessments = [
        make_assessment(due_date=date(2026, 10, 15), estimated_hours=10.0, source="extracted"),
        make_assessment(due_date=date(2026, 10, 16), estimated_hours=20.0, source="fixture"),
    ]
    week3 = next(l for l in weekly_workload(assessments, weeks) if l.week.week_number == 3)
    # Totals still cover both rows; the fabricated share is reported alongside.
    assert week3.remaining_hours == pytest.approx(30.0)
    assert week3.assessment_count == 2
    assert week3.fabricated_hours == pytest.approx(20.0)
    assert week3.fabricated_count == 1


def test_fabricated_hours_discount_logged_progress():
    weeks = term1_weeks()
    assessments = [
        make_assessment(due_date=date(2026, 10, 15), estimated_hours=20.0,
                        progress_pct=75, source="fixture"),
    ]
    week3 = next(l for l in weekly_workload(assessments, weeks) if l.week.week_number == 3)
    assert week3.fabricated_hours == pytest.approx(5.0)
    assert week3.remaining_hours == pytest.approx(5.0)


def test_genuine_and_unlabelled_rows_report_no_fabricated_hours():
    weeks = term1_weeks()
    assessments = [
        make_assessment(due_date=date(2026, 10, 15), estimated_hours=10.0, source="extracted"),
        make_assessment(due_date=date(2026, 10, 15), estimated_hours=10.0, source="manual"),
        make_assessment(due_date=date(2026, 10, 15), estimated_hours=10.0),
    ]
    loads = weekly_workload(assessments, weeks)
    assert all(load.fabricated_hours == 0.0 for load in loads)
    assert all(load.fabricated_count == 0 for load in loads)
    assert next(l for l in loads if l.week.week_number == 3).remaining_hours == pytest.approx(30.0)


def test_submitted_fabricated_row_leaves_no_trace():
    weeks = term1_weeks()
    assessments = [
        make_assessment(due_date=date(2026, 10, 15), estimated_hours=12.0,
                        status="submitted", source="fixture"),
    ]
    week3 = next(l for l in weekly_workload(assessments, weeks) if l.week.week_number == 3)
    assert week3.remaining_hours == 0.0
    assert week3.fabricated_hours == 0.0
    assert week3.fabricated_count == 0


def test_weeks_without_fabricated_work_default_to_zero():
    weeks = term1_weeks()
    assessments = [
        make_assessment(due_date=date(2026, 10, 15), estimated_hours=10.0, source="fixture"),
    ]
    loads = weekly_workload(assessments, weeks)
    other_weeks = [load for load in loads if load.week.week_number != 3]
    assert all(load.fabricated_hours == 0.0 for load in other_weeks)
    assert sum(load.fabricated_count for load in loads) == 1
