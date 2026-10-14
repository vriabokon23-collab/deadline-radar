"""Weekly workload bucketing and risk scoring.

This module deals only in plain data: callers (``app.py``) read rows from ``radar.db``
and term weeks from ``radar.calendar`` and pass them in, which keeps the logic pure
and easy to test. The one import from ``radar.db`` is the ``source`` enum value for
fabricated rows, so the provenance string is spelled in a single place. See
``INTEGRATION_NOTES.md`` for the exact interface the other two modules need to expose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Mapping, Sequence

from radar.db import FABRICATED_SOURCE

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
    """Remaining estimated hours due in a given term week.

    `fabricated_hours` and `fabricated_count` are the share of the two totals that
    came from rows the offline fallback invented (`source = fixture`). They are a
    breakdown, not a deduction: the totals still cover every unsubmitted row, so
    the heatmap can show a crunch week and say how much of it is not real.
    """

    week: TermWeek
    remaining_hours: float
    assessment_count: int
    fabricated_hours: float = 0.0
    fabricated_count: int = 0


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
    """Flat backward estimate, ignoring everything else that is due.

    Naively backs off from the due date by however many weeks the estimated
    hours would take at `hours_per_week`. Kept as the fallback for dates
    outside the term calendar; `suggested_start_date` is what callers should
    pass to `assess_risk` when term weeks are available.
    """
    if hours_per_week <= 0:
        return due_date
    weeks_needed = estimated_hours / hours_per_week
    return due_date - timedelta(days=round(weeks_needed * 7))


def suggested_start_date(
    assessment: Assessment,
    others: Sequence[Assessment],
    term_weeks: Sequence[TermWeek],
    hours_per_week: float = DEFAULT_HOURS_PER_WEEK,
) -> date:
    """Backward-plan the week work should start, given what else is due around it.

    Walks back from the due week, taking whatever capacity each week has left
    after the other assessments due in it, until the estimated hours are
    covered. Returns the Monday of the week the work has to begin.

    Three things make this better than `default_start_date`, which only divides
    hours by a flat weekly rate:

    - A week already carrying other deadlines offers less than
      `hours_per_week`, so a crunch week pushes the start date earlier.
    - Reading weeks are skipped on the first pass and only used if the work
      cannot fit without them.
    - The full `estimated_hours` are planned, not the remaining ones. The
      answer to "when should this have started" must not improve just because
      some of it is done, or a half-finished assessment would look healthier
      than an untouched one with the same deadline.

    Competing work is attributed to the week it is *due* in, matching the
    heatmap, rather than being spread back over the weeks it will really be
    done in — scheduling every assessment jointly is a different problem. The
    returned Monday is also the start of the week capacity ran out in, not the
    exact hour, so the suggestion errs early. Both biases point the same way:
    warn sooner rather than later.

    Falls back to `default_start_date` when the due date sits outside the term
    calendar entirely.
    """
    due_date = assessment["due_date"]
    needed = float(assessment["estimated_hours"] or 0.0)
    if needed <= 0 or hours_per_week <= 0:
        return due_date

    weeks_sorted = sorted(term_weeks, key=lambda w: w.start_date)
    due_week = week_containing(due_date, weeks_sorted)
    if due_week is None:
        return default_start_date(due_date, needed, hours_per_week)

    competing = {week: 0.0 for week in weeks_sorted}
    for other in others:
        if other["status"] == "submitted":
            continue
        week = week_containing(other["due_date"], weeks_sorted)
        if week is not None:
            competing[week] += remaining_hours(other)

    candidates = weeks_sorted[: weeks_sorted.index(due_week) + 1]

    for avoid_reading_weeks in (True, False):
        accumulated = 0.0
        for week in reversed(candidates):
            if avoid_reading_weeks and week.is_reading_week:
                continue
            accumulated += max(0.0, hours_per_week - competing[week])
            if accumulated >= needed:
                return week.start_date

    # Not enough capacity anywhere before the deadline, even using reading
    # weeks. The work needed more term than exists, so say so by pointing at
    # the earliest week we have rather than inventing a date outside it.
    return candidates[0].start_date


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
    has no work due. Fabricated rows are counted in the totals and reported
    separately on each WeekLoad.
    """
    weeks_sorted = sorted(term_weeks, key=lambda w: w.start_date)
    hours_by_week = {week: 0.0 for week in weeks_sorted}
    count_by_week = {week: 0 for week in weeks_sorted}
    fabricated_hours_by_week = {week: 0.0 for week in weeks_sorted}
    fabricated_count_by_week = {week: 0 for week in weeks_sorted}

    for assessment in assessments:
        if assessment["status"] == "submitted":
            continue
        week = week_containing(assessment["due_date"], weeks_sorted)
        if week is None:
            continue
        hours = remaining_hours(assessment)
        hours_by_week[week] += hours
        count_by_week[week] += 1
        if assessment.get("source") == FABRICATED_SOURCE:
            fabricated_hours_by_week[week] += hours
            fabricated_count_by_week[week] += 1

    return [
        WeekLoad(
            week=week,
            remaining_hours=hours_by_week[week],
            assessment_count=count_by_week[week],
            fabricated_hours=fabricated_hours_by_week[week],
            fabricated_count=fabricated_count_by_week[week],
        )
        for week in weeks_sorted
    ]
