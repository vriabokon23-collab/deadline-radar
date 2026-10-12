"""Weekly workload bucketing and risk scoring.

This module is intentionally decoupled from ``radar.db`` and ``radar.calendar``:
neither exists yet on this branch, and analytics logic is pure and easiest to test
when it only deals in plain data. Callers (``app.py``) are expected to read rows from
``radar.db`` and term weeks from ``radar.calendar`` and pass them in. See
``INTEGRATION_NOTES.md`` for the exact interface the other two modules need to expose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Mapping, Sequence

DEFAULT_HOURS_PER_WEEK = 10.0
RISK_MARGIN_PCT = 20.0

REASON_PROGRESS_BEHIND = "progress_behind_schedule"
REASON_INSUFFICIENT_TIME = "insufficient_remaining_time"

Assessment = Mapping[str, Any]


@dataclass(frozen=True)
class TermWeek:
    """One week of the UCL term calendar.

    This is the shape `radar.calendar` is expected to produce (see
    INTEGRATION_NOTES.md). Any object exposing these four attributes works —
    a dataclass, namedtuple, or plain object all satisfy the functions below.
    """

    term: str
    week_number: int
    start_date: date
    is_reading_week: bool = False


@dataclass(frozen=True)
class WeekLoad:
    """Remaining estimated hours due in a given term week."""

    week: TermWeek
    remaining_hours: float
    assessment_count: int


@dataclass(frozen=True)
class RiskResult:
    at_risk: bool
    reasons: list[str] = field(default_factory=list)


def remaining_hours(assessment: Assessment) -> float:
    """Estimated hours not yet accounted for by logged progress."""
    estimated = float(assessment["estimated_hours"] or 0.0)
    progress = float(assessment["progress_pct"] or 0.0)
    return estimated * (1 - progress / 100)


def expected_progress_pct(today: date, start_date: date, due_date: date) -> float:
    """Linear expected progress: 0% at start_date, 100% at due_date, clamped."""
    if due_date <= start_date:
        return 100.0 if today >= due_date else 0.0
    if today <= start_date:
        return 0.0
    if today >= due_date:
        return 100.0
    total_days = (due_date - start_date).days
    elapsed_days = (today - start_date).days
    return 100.0 * elapsed_days / total_days


def default_start_date(
    due_date: date,
    estimated_hours: float,
    hours_per_week: float = DEFAULT_HOURS_PER_WEEK,
) -> date:
    """Fallback suggested start date until A14's backward-planner exists.

    Naively backs off from the due date by however many weeks the estimated
    hours would take at `hours_per_week`. Callers should prefer passing an
    explicit `start_date` to `assess_risk` once a real suggestion is available.
    """
    if hours_per_week <= 0:
        return due_date
    weeks_needed = estimated_hours / hours_per_week
    return due_date - timedelta(days=round(weeks_needed * 7))


def assess_risk(
    assessment: Assessment,
    today: date,
    start_date: date | None = None,
    hours_per_week: float = DEFAULT_HOURS_PER_WEEK,
) -> RiskResult:
    """Flag an assessment at risk per the CLAUDE.md rule.

    At risk when either holds:
    - actual progress trails expected progress (linear from `start_date` to the
      due date) by strictly more than `RISK_MARGIN_PCT` percentage points;
    - remaining hours exceed the hours available between today and the due
      date at `hours_per_week` per assessment.

    A submitted assessment is never at risk.
    """
    if assessment["status"] == "submitted":
        return RiskResult(at_risk=False, reasons=[])

    due_date = assessment["due_date"]
    estimated_hours = float(assessment["estimated_hours"] or 0.0)
    progress_pct = float(assessment["progress_pct"] or 0.0)

    effective_start = start_date or default_start_date(due_date, estimated_hours, hours_per_week)

    reasons: list[str] = []

    expected = expected_progress_pct(today, effective_start, due_date)
    if expected - progress_pct > RISK_MARGIN_PCT:
        reasons.append(REASON_PROGRESS_BEHIND)

    weeks_left = max(0.0, (due_date - today).days / 7)
    available_hours = weeks_left * hours_per_week
    if remaining_hours(assessment) > available_hours:
        reasons.append(REASON_INSUFFICIENT_TIME)

    return RiskResult(at_risk=bool(reasons), reasons=reasons)


def week_containing(target: date, term_weeks: Sequence[TermWeek]) -> TermWeek | None:
    """The TermWeek whose [start_date, next_start_date) span contains `target`.

    Returns None if `term_weeks` is empty or `target` falls before the first
    week. A date after the last week's start is attributed to that last week
    (see INTEGRATION_NOTES.md for the inter-term-gap caveat this implies).
    """
    weeks_sorted = sorted(term_weeks, key=lambda w: w.start_date)
    if not weeks_sorted or target < weeks_sorted[0].start_date:
        return None
    for current, nxt in zip(weeks_sorted, weeks_sorted[1:]):
        if current.start_date <= target < nxt.start_date:
            return current
    return weeks_sorted[-1]


def weekly_workload(
    assessments: Sequence[Assessment],
    term_weeks: Sequence[TermWeek],
) -> list[WeekLoad]:
    """Bucket remaining hours by the term week each assessment is due in.

    Submitted assessments are excluded entirely (see INTEGRATION_NOTES.md).
    Returns one WeekLoad per input term week, in week order, even when a week
    has no work due.
    """
    weeks_sorted = sorted(term_weeks, key=lambda w: w.start_date)
    hours_by_week = {week: 0.0 for week in weeks_sorted}
    count_by_week = {week: 0 for week in weeks_sorted}

    for assessment in assessments:
        if assessment["status"] == "submitted":
            continue
        week = week_containing(assessment["due_date"], weeks_sorted)
        if week is None:
            continue
        hours_by_week[week] += remaining_hours(assessment)
        count_by_week[week] += 1

    return [
        WeekLoad(
            week=week,
            remaining_hours=hours_by_week[week],
            assessment_count=count_by_week[week],
        )
        for week in weeks_sorted
    ]
