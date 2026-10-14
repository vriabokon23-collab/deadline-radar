"""Claude weekly planning: current state in, a prioritised list of hours out.

A suggestion, not a record. This module deliberately does not import
``radar.db``: a plan is advice about work the student already has, so there is
nothing here that could write a row, and no code path that needs one. Keep it
that way — if planning ever needs to persist something, that belongs in a
separate module with its own review gate.

The call follows the same contract as ``radar.extract``: one request, exactly
one retry on an unusable reply, no third attempt, and the pinned model imported
from there so it is spelled in one place. When Claude cannot be reached the
plan is replayed from ``tests/fixtures/_plan_week.json``, and the result says so
through :attr:`WeekPlan.source` — a fabricated plan must never be presented as
Claude's reading of the student's actual workload.

Claude names assessments the way it saw them, by module code and title, and
:func:`plan_week` resolves those against the real rows. Anything it names that
the student does not have (or has already submitted) is dropped into
:attr:`WeekPlan.unmatched` rather than failing the whole plan, which is also how
the offline fixture degrades when it meets a database it was not written for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from radar import analytics

# The model is pinned once, in radar.extract. Importing it rather than
# re-declaring it means a stretch feature can never drift onto a different
# model than the documented one. _strip_code_fence is reused for the same
# reason: both callers need the identical tolerance for ```json fencing.
from radar.extract import (
    FIXTURES_ENV,
    FIXTURES_DIR,
    MAX_ATTEMPTS,
    MAX_TOKENS,
    MODEL,
    _strip_code_fence as strip_code_fence,
)

HORIZON_DAYS = 7
DEFAULT_WEEKLY_HOURS = 20.0

# A plan that asks for more than this against one assessment in one week is not
# a plan, it is a panic. Reject it and spend the retry.
MAX_ITEM_HOURS = 40.0

# Its own subdirectory, not alongside the handbook fixtures: extract.fixture_path
# globs tests/fixtures/*.json non-recursively, so a plan can never be handed back
# as the extraction of an uploaded PDF, and the extraction suite's "every fixture
# satisfies the assessment schema" check stays true of every file it can see.
PLAN_FIXTURE = FIXTURES_DIR / 'plan' / 'week_plan.json'

REQUIRED_KEYS = ('module_code', 'title', 'hours', 'priority', 'reason')

PROMPT = (
    'You are a study planner for a UCL student. Below is the current state of '
    'every assessment they have not yet submitted, and the remaining workload in '
    'each upcoming term week.\n\n'
    'Propose what they should work on over the next {horizon} days, given a budget '
    'of {budget:g} study hours in total.\n\n'
    'Rules for a good plan:\n'
    '- Prioritise work that is flagged AT RISK, and work whose deadline falls '
    'inside the horizon.\n'
    '- Large assessments due in a crunch week must be started early even when the '
    'deadline is far off, because the crunch week has no room left.\n'
    '- Do not allocate more hours than the assessment has remaining.\n'
    '- Keep the total at or under the budget. Fewer assessments done properly beats '
    'an hour sprinkled on everything.\n'
    '- Only name assessments from the list below, exactly as written.\n\n'
    'Return ONLY a valid JSON array of objects, highest priority first. Each object '
    'must have these exact keys:\n'
    '- "module_code": string, copied exactly from the list\n'
    '- "title": string, copied exactly from the list\n'
    '- "hours": number, hours to spend on it during the horizon, greater than 0\n'
    '- "priority": integer, 1 for the most important, then 2, 3 and so on\n'
    '- "reason": string, one sentence on why it is ranked there, citing the '
    'deadline, the risk flag or the crunch week\n\n'
    'Do not include markdown formatting or commentary. Output raw JSON only.\n\n'
    '{state}'
)


class PlanError(ValueError):
    """The model's response could not be used: bad JSON, or an unusable item."""


@dataclass(frozen=True)
class PlanItem:
    """One assessment and the hours the plan allocates to it."""

    module_code: str
    title: str
    hours: float
    priority: int
    reason: str
    assessment_id: int | None = None


@dataclass
class WeekPlan:
    """A proposed allocation of study hours, and how it was produced."""

    items: list[PlanItem]
    starts_on: date
    hours_available: float
    source: str  # "api" or "fixture"
    reason: str | None = None
    unmatched: list[str] = field(default_factory=list)

    @property
    def used_fixtures(self) -> bool:
        return self.source == 'fixture'

    @property
    def total_hours(self) -> float:
        return round(sum(item.hours for item in self.items), 1)

    @property
    def over_budget_hours(self) -> float:
        """Hours the plan asks for beyond the stated budget, 0.0 if it fits."""
        return round(max(0.0, self.total_hours - self.hours_available), 1)


def _risk_labels(assessment: Mapping[str, Any], today: date) -> list[str]:
    result = analytics.assess_risk(assessment, today=today)
    return list(result.reasons)


def summarise_state(
    assessments: Sequence[Mapping[str, Any]],
    week_loads: Sequence[analytics.WeekLoad],
    today: date,
    hours_available: float = DEFAULT_WEEKLY_HOURS,
) -> str:
    """Render the planning context Claude is asked to reason over.

    Pure text assembly, so the hard part of the feature — deciding what the
    model is even allowed to see — is testable without a network call.
    Submitted work is omitted entirely: there is nothing left to plan for it.
    """
    lines = [
        f'TODAY: {today.isoformat()}',
        f'HORIZON: the {HORIZON_DAYS} days up to and including '
        f'{(today + timedelta(days=HORIZON_DAYS - 1)).isoformat()}',
        f'STUDY BUDGET: {hours_available:g} hours in total',
        '',
        'OUTSTANDING ASSESSMENTS:',
    ]

    outstanding = [a for a in assessments if a.get('status') != 'submitted']
    if not outstanding:
        lines.append('  (none - everything is submitted)')
    for assessment in sorted(outstanding, key=lambda a: a['due_date']):
        due = assessment['due_date']
        days_left = (due - today).days
        remaining = analytics.remaining_hours(assessment)
        weighting = assessment.get('weighting_pct')
        risks = _risk_labels(assessment, today)
        lines.append(
            f'- {assessment["module_code"]} | {assessment["title"]}\n'
            f'    type={assessment.get("type", "other")} due={due.isoformat()} '
            f'({days_left} days) weighting='
            f'{"n/a" if weighting is None else f"{float(weighting):g}%"}\n'
            f'    estimated={float(assessment["estimated_hours"]):g}h '
            f'progress={float(assessment["progress_pct"]):g}% '
            f'remaining={remaining:.1f}h\n'
            f'    risk={", ".join(risks) if risks else "none"}'
        )

    lines += ['', 'REMAINING HOURS BY TERM WEEK:']
    upcoming = [load for load in week_loads if load.week.start_date >= today - timedelta(days=7)]
    if not upcoming:
        lines.append('  (no term weeks ahead)')
    for load in upcoming[:12]:
        week = load.week
        marks = ' READING WEEK' if week.is_reading_week else ''
        lines.append(
            f'- {week.term} week {week.week_number}, commencing '
            f'{week.start_date.isoformat()}: {load.remaining_hours:.1f}h across '
            f'{load.assessment_count} deadline(s){marks}'
        )

    return '\n'.join(lines)


def build_prompt(state: str, hours_available: float) -> str:
    return PROMPT.format(horizon=HORIZON_DAYS, budget=hours_available, state=state)


def validate_item(item: Any, index: int = 0) -> PlanItem:
    """Validate one proposed allocation, or raise PlanError."""
    label = f'plan item {index}'
    if not isinstance(item, Mapping):
        raise PlanError(f'{label} is {type(item).__name__}, expected an object')

    missing = [k for k in REQUIRED_KEYS if item.get(k) in (None, '')]
    if missing:
        raise PlanError(f'{label} is missing required field(s): {", ".join(missing)}')

    try:
        hours = float(item['hours'])
    except (TypeError, ValueError):
        raise PlanError(f'{label} has non-numeric hours: {item["hours"]!r}') from None
    if not 0 < hours <= MAX_ITEM_HOURS:
        raise PlanError(f'{label} allocates {hours}h, outside 0-{MAX_ITEM_HOURS:g}')

    try:
        priority = int(item['priority'])
    except (TypeError, ValueError):
        raise PlanError(f'{label} has non-integer priority: {item["priority"]!r}') from None
    if priority < 1:
        raise PlanError(f'{label} has priority {priority}, expected 1 or greater')

    return PlanItem(
        module_code=str(item['module_code']).strip(),
        title=str(item['title']).strip(),
        hours=round(hours, 1),
        priority=priority,
        reason=str(item['reason']).strip(),
    )


def validate_batch(payload: Any) -> list[PlanItem]:
    """Validate a whole proposed plan, or raise PlanError."""
    if isinstance(payload, Mapping):
        payload = payload.get('plan')
    if not isinstance(payload, list):
        raise PlanError(
            f'expected a JSON array of plan items, got {type(payload).__name__}')

    items = [validate_item(item, i) for i, item in enumerate(payload)]

    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (item.module_code.upper(), item.title.casefold())
        if key in seen:
            raise PlanError(
                f'plan allocates hours to {item.module_code} {item.title!r} twice')
        seen.add(key)

    return sorted(items, key=lambda item: item.priority)


def parse_response(text: str) -> list[PlanItem]:
    """Turn raw model text into validated plan items, or raise PlanError."""
    try:
        payload = json.loads(strip_code_fence(text))
    except json.JSONDecodeError as err:
        raise PlanError(f'response was not valid JSON: {err}') from err
    return validate_batch(payload)


def match_to_assessments(
    items: Sequence[PlanItem],
    assessments: Sequence[Mapping[str, Any]],
) -> tuple[list[PlanItem], list[str]]:
    """Attach real assessment ids to plan items, separating out the strays.

    Submitted work is not in the index, so a plan that plucks hours out of
    something already handed in is reported as a stray rather than shown.
    Capping each item at the assessment's remaining hours keeps a plan from
    asking for 20 hours of work on a row with 4 hours left in it.
    """
    index = {
        (str(a['module_code']).upper(), str(a['title']).casefold()): a
        for a in assessments
        if a.get('status') != 'submitted'
    }

    matched: list[PlanItem] = []
    unmatched: list[str] = []
    for item in items:
        assessment = index.get((item.module_code.upper(), item.title.casefold()))
        if assessment is None:
            unmatched.append(f'{item.module_code} {item.title}')
            continue
        remaining = analytics.remaining_hours(assessment)
        matched.append(
            PlanItem(
                module_code=str(assessment['module_code']),
                title=str(assessment['title']),
                hours=round(min(item.hours, remaining), 1) if remaining > 0 else item.hours,
                priority=item.priority,
                reason=item.reason,
                assessment_id=assessment.get('id'),
            )
        )
    return matched, unmatched


def load_fixture() -> list[PlanItem]:
    """Replay the saved plan through the real validation path.

    Validated exactly like a live reply, so a malformed fixture fails in the
    test suite rather than quietly feeding the demo a plan the schema would
    have rejected.
    """
    try:
        raw = json.loads(PLAN_FIXTURE.read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise PlanError(f'no plan fixture available at {PLAN_FIXTURE}') from None
    except json.JSONDecodeError as err:
        raise PlanError(f'fixture {PLAN_FIXTURE.name} is not valid JSON: {err}') from err
    return validate_batch(raw)


def _call_model(client: Any, prompt: str) -> str:
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{'role': 'user', 'content': [{'type': 'text', 'text': prompt}]}],
    )
    return response.content[0].text


def plan_week(
    assessments: Sequence[Mapping[str, Any]],
    week_loads: Sequence[analytics.WeekLoad],
    today: date,
    *,
    hours_available: float = DEFAULT_WEEKLY_HOURS,
    client: Any = None,
    fixtures_mode: bool | None = None,
    api_key: str | None = None,
) -> WeekPlan:
    """Propose how to spend the next week's study hours.

    `assessments` are analytics-shaped rows (``due_date`` as a ``date``) and
    `week_loads` come from ``analytics.weekly_workload``. Falls back to the saved
    fixture when there is no API key, when ``DEADLINE_RADAR_FIXTURES=1`` is set,
    or when both attempts fail, and reports which happened on the result.

    Nothing here writes to the database.
    """
    if fixtures_mode is None:
        fixtures_mode = os.environ.get(FIXTURES_ENV, '0').strip() == '1'
    if api_key is None:
        api_key = os.environ.get('ANTHROPIC_API_KEY', '').strip()

    def replay(reason: str) -> WeekPlan:
        matched, unmatched = match_to_assessments(load_fixture(), assessments)
        return WeekPlan(matched, today, hours_available, 'fixture', reason, unmatched)

    if fixtures_mode:
        return replay(f'{FIXTURES_ENV}=1 forces offline fixture mode')

    if client is None and not api_key:
        return replay('ANTHROPIC_API_KEY is not set, so Claude was never called')

    if client is None:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

    prompt = build_prompt(
        summarise_state(assessments, week_loads, today, hours_available), hours_available)

    last_error: Exception | None = None
    for _attempt in range(MAX_ATTEMPTS):
        try:
            items = parse_response(_call_model(client, prompt))
        except Exception as err:  # noqa: BLE001 - any failure costs us one attempt
            last_error = err
            continue
        matched, unmatched = match_to_assessments(items, assessments)
        return WeekPlan(matched, today, hours_available, 'api', None, unmatched)

    return replay(f'every Claude attempt failed ({last_error})')
