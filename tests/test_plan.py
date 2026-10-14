"""Tests for radar/plan.py. Everything here runs offline.

No test may reach the network: the client is always injected, and the
fixture-replay tests pass a client that fails the test if it is touched at all.
The planning feature is advice only, so the test that matters most is
`test_planning_never_writes_to_the_database`.
"""

import ast
from datetime import date
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import app
from radar import analytics, db, extract, plan
from radar.plan import PlanError, PlanItem
from sample_data import seed


TODAY = date(2026, 10, 16)

VALID_ITEM = {
    'module_code': 'COMP0004',
    'title': 'Problem Set 1 (Graph Proofs)',
    'hours': 10.0,
    'priority': 1,
    'reason': 'Flagged at risk and due in a week.',
}

_DROP = object()


def item(**overrides):
    """A valid plan item with fields replaced or removed (pass _DROP to drop)."""
    out = dict(VALID_ITEM)
    for key, value in overrides.items():
        if value is _DROP:
            out.pop(key, None)
        else:
            out[key] = value
    return out


class FakeClient:
    """Stands in for anthropic.Anthropic, replaying scripted replies in order."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    @property
    def messages(self):
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.replies:
            raise AssertionError('client called more times than the test scripted')
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(content=[SimpleNamespace(text=reply)])


class ForbiddenClient:
    """Fails the test if the planner tries to call out at all."""

    @property
    def messages(self):
        raise AssertionError('the API was called when the fixture should have been replayed')


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Neither env var leaks in from the developer's shell."""
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.delenv('DEADLINE_RADAR_FIXTURES', raising=False)


@pytest.fixture
def seeded(conn):
    seed.seed_demo_data(conn)
    return conn


@pytest.fixture
def rows(seeded):
    return [app.to_analytics_row(row) for row in db.list_assessments(seeded)]


@pytest.fixture
def loads(rows):
    return analytics.weekly_workload(rows, app.get_all_term_weeks())


# ---------------------------------------------------------------------------
# Module hygiene
# ---------------------------------------------------------------------------
def test_plan_cannot_write_because_it_never_imports_the_database():
    """The no-writes guarantee is structural, not a matter of discipline."""
    tree = ast.parse((_ROOT / 'radar' / 'plan.py').read_text(encoding='utf-8'))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or '')
            imported.update(f'{node.module}.{a.name}' for a in node.names)
    assert not {name for name in imported if 'db' in name.split('.')}


def test_the_model_is_not_pinned_a_second_time():
    """One model constant for the whole project, per CLAUDE.md."""
    assert plan.MODEL is extract.MODEL
    assert plan.MAX_ATTEMPTS == extract.MAX_ATTEMPTS == 2


def test_the_plan_fixture_is_invisible_to_pdf_fixture_matching():
    """A handbook upload must never be answered with the planning fixture."""
    assert plan.PLAN_FIXTURE not in set(extract.FIXTURES_DIR.glob('*.json'))
    for name in ('plan.pdf', 'study_plan_2026.pdf', 'week_plan.pdf'):
        assert extract.fixture_path(name) != plan.PLAN_FIXTURE


# ---------------------------------------------------------------------------
# State digest
# ---------------------------------------------------------------------------
def test_state_digest_describes_every_outstanding_assessment(rows, loads):
    state = plan.summarise_state(rows, loads, TODAY)
    outstanding = [r for r in rows if r['status'] != 'submitted']
    for row in outstanding:
        assert row['title'] in state
    assert f'TODAY: {TODAY.isoformat()}' in state


def test_state_digest_omits_submitted_work(rows, loads):
    """There is nothing left to plan for work already handed in."""
    state = plan.summarise_state(rows, loads, TODAY)
    submitted = [r for r in rows if r['status'] == 'submitted']
    assert submitted
    for row in submitted:
        assert row['title'] not in state


def test_state_digest_carries_the_risk_flags_and_the_crunch_week(rows, loads):
    """The planner's two main inputs must actually reach the prompt."""
    state = plan.summarise_state(rows, loads, TODAY)
    assert analytics.REASON_INSUFFICIENT_TIME in state
    assert analytics.REASON_PROGRESS_BEHIND in state
    busiest = max(loads, key=lambda load: load.remaining_hours)
    assert busiest.week.start_date.isoformat() in state
    assert f'{busiest.remaining_hours:.1f}h' in state


def test_state_digest_marks_reading_weeks(rows, loads):
    state = plan.summarise_state(rows, loads, TODAY)
    assert 'READING WEEK' in state


def test_state_digest_survives_an_empty_radar(loads):
    state = plan.summarise_state([], loads, TODAY)
    assert 'none - everything is submitted' in state


def test_prompt_states_the_budget_and_horizon():
    prompt = plan.build_prompt('STATE HERE', 15.0)
    assert 'STATE HERE' in prompt
    assert '15 study hours' in prompt
    assert f'next {plan.HORIZON_DAYS} days' in prompt


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def test_a_valid_payload_validates():
    items = plan.validate_batch([VALID_ITEM])
    assert items == [PlanItem('COMP0004', 'Problem Set 1 (Graph Proofs)', 10.0, 1,
                              'Flagged at risk and due in a week.')]


def test_items_come_back_in_priority_order():
    payload = [item(priority=3, title='Third'), item(priority=1, title='First'),
               item(priority=2, title='Second')]
    assert [i.title for i in plan.validate_batch(payload)] == ['First', 'Second', 'Third']


def test_a_wrapped_payload_validates():
    assert len(plan.validate_batch({'plan': [VALID_ITEM]})) == 1


@pytest.mark.parametrize('key', plan.REQUIRED_KEYS)
def test_a_missing_field_is_rejected(key):
    with pytest.raises(PlanError, match=key):
        plan.validate_batch([item(**{key: _DROP})])


@pytest.mark.parametrize('bad_hours', [0, -5, plan.MAX_ITEM_HOURS + 0.5, 'lots'])
def test_unusable_hours_are_rejected(bad_hours):
    with pytest.raises(PlanError):
        plan.validate_batch([item(hours=bad_hours)])


@pytest.mark.parametrize('bad_priority', [0, -1, 'first'])
def test_an_unusable_priority_is_rejected(bad_priority):
    with pytest.raises(PlanError):
        plan.validate_batch([item(priority=bad_priority)])


def test_a_non_list_payload_is_rejected():
    with pytest.raises(PlanError, match='expected a JSON array'):
        plan.validate_batch({'items': []})


def test_planning_the_same_assessment_twice_is_rejected():
    """Two allocations against one row is a broken plan, not two suggestions."""
    with pytest.raises(PlanError, match='twice'):
        plan.validate_batch([item(priority=1), item(priority=2, hours=4.0)])


def test_an_empty_plan_is_allowed():
    """Nothing urgent this week is a legitimate answer."""
    assert plan.validate_batch([]) == []


def test_a_fenced_response_parses():
    text = '```json\n' + json.dumps([VALID_ITEM]) + '\n```'
    assert len(plan.parse_response(text)) == 1


def test_prose_instead_of_json_is_rejected():
    with pytest.raises(PlanError, match='not valid JSON'):
        plan.parse_response('Here is your plan! Work on the graph proofs.')


# ---------------------------------------------------------------------------
# Matching proposals onto real rows
# ---------------------------------------------------------------------------
def test_matching_attaches_the_real_assessment_id(rows):
    matched, unmatched = plan.match_to_assessments(plan.validate_batch([VALID_ITEM]), rows)
    assert unmatched == []
    expected = next(r for r in rows if r['title'] == VALID_ITEM['title'])
    assert matched[0].assessment_id == expected['id']


def test_an_assessment_the_student_does_not_have_is_dropped_not_fatal(rows):
    """A stray name must cost that one line, not the whole plan."""
    payload = [VALID_ITEM, item(title='Dissertation', priority=2)]
    matched, unmatched = plan.match_to_assessments(plan.validate_batch(payload), rows)
    assert [i.title for i in matched] == [VALID_ITEM['title']]
    assert unmatched == ['COMP0004 Dissertation']


def test_hours_planned_against_submitted_work_are_dropped(rows):
    submitted = next(r for r in rows if r['status'] == 'submitted')
    payload = [item(module_code=submitted['module_code'], title=submitted['title'])]
    matched, unmatched = plan.match_to_assessments(plan.validate_batch(payload), rows)
    assert matched == []
    assert unmatched == [f'{submitted["module_code"]} {submitted["title"]}']


def test_an_allocation_is_capped_at_the_hours_actually_left(rows):
    """A plan must not ask for 10 hours of work on a row with 4 hours left."""
    labs = next(r for r in rows if r['title'] == 'Lab Exercises 1-4')
    remaining = analytics.remaining_hours(labs)
    payload = [item(module_code=labs['module_code'], title=labs['title'], hours=30.0)]
    matched, _ = plan.match_to_assessments(plan.validate_batch(payload), rows)
    assert matched[0].hours == pytest.approx(round(remaining, 1))


def test_matching_is_insensitive_to_case_and_padding(rows):
    payload = [item(module_code='  comp0004 '.upper().strip().lower(),
                    title='  problem set 1 (GRAPH proofs)  ')]
    matched, unmatched = plan.match_to_assessments(plan.validate_batch(payload), rows)
    assert unmatched == []
    assert matched[0].title == 'Problem Set 1 (Graph Proofs)'


# ---------------------------------------------------------------------------
# The offline fixture
# ---------------------------------------------------------------------------
def test_the_fixture_validates_like_a_live_reply():
    items = plan.load_fixture()
    assert items
    assert [i.priority for i in items] == sorted(i.priority for i in items)


def test_the_fixture_plans_the_seeded_at_risk_work(rows, loads):
    """The offline demo must name the same rows the dashboard flags, or it looks broken."""
    result = plan.plan_week(rows, loads, TODAY, fixtures_mode=True, client=ForbiddenClient())
    planned = {i.title for i in result.items}
    at_risk = {
        r['title'] for r in rows
        if analytics.assess_risk(r, today=TODAY).at_risk
    }
    assert at_risk
    assert at_risk <= planned


def test_the_fixture_plan_fits_the_default_budget(rows, loads):
    result = plan.plan_week(rows, loads, TODAY, fixtures_mode=True, client=ForbiddenClient())
    assert result.hours_available == plan.DEFAULT_WEEKLY_HOURS
    assert result.total_hours == pytest.approx(plan.DEFAULT_WEEKLY_HOURS)
    assert result.over_budget_hours == 0.0


def test_the_fixture_degrades_quietly_against_a_database_it_does_not_know(conn):
    """A student's own rows offline get a thin plan, not a crash or a wrong one."""
    db.insert_assessment(conn, {
        'module_code': 'HIST0001', 'title': 'Source Analysis', 'type': 'essay',
        'due_date': '2026-11-20', 'estimated_hours': 15, 'source_quote': 'Week 8.',
    })
    rows = [app.to_analytics_row(r) for r in db.list_assessments(conn)]
    result = plan.plan_week(rows, [], TODAY, fixtures_mode=True, client=ForbiddenClient())
    assert result.items == []
    assert len(result.unmatched) == len(plan.load_fixture())


# ---------------------------------------------------------------------------
# plan_week: which path ran, and how it is reported
# ---------------------------------------------------------------------------
def test_a_live_reply_is_labelled_api_and_asks_the_pinned_model(rows, loads):
    client = FakeClient(json.dumps([VALID_ITEM]))
    result = plan.plan_week(rows, loads, TODAY, client=client)
    assert result.source == 'api'
    assert result.used_fixtures is False
    assert result.reason is None
    assert client.calls[0]['model'] == extract.MODEL


def test_the_live_prompt_carries_the_state_digest(rows, loads):
    client = FakeClient(json.dumps([VALID_ITEM]))
    plan.plan_week(rows, loads, TODAY, client=client, hours_available=12.0)
    sent = client.calls[0]['messages'][0]['content'][0]['text']
    assert 'Problem Set 1 (Graph Proofs)' in sent
    assert '12 study hours' in sent


def test_the_fixtures_flag_skips_the_api_entirely(rows, loads, monkeypatch):
    monkeypatch.setenv('DEADLINE_RADAR_FIXTURES', '1')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-should-not-be-used')
    result = plan.plan_week(rows, loads, TODAY, client=ForbiddenClient())
    assert result.used_fixtures is True
    assert 'DEADLINE_RADAR_FIXTURES=1' in result.reason


def test_a_missing_api_key_replays_the_fixture_and_says_so(rows, loads):
    result = plan.plan_week(rows, loads, TODAY)
    assert result.used_fixtures is True
    assert 'ANTHROPIC_API_KEY is not set' in result.reason


def test_bad_json_costs_one_attempt_and_the_retry_is_used(rows, loads):
    client = FakeClient('not json at all', json.dumps([VALID_ITEM]))
    result = plan.plan_week(rows, loads, TODAY, client=client)
    assert result.source == 'api'
    assert len(client.calls) == 2


def test_there_is_no_third_attempt(rows, loads):
    client = FakeClient('nope', 'still nope')
    result = plan.plan_week(rows, loads, TODAY, client=client)
    assert len(client.calls) == plan.MAX_ATTEMPTS
    assert result.used_fixtures is True
    assert 'every Claude attempt failed' in result.reason


def test_a_transport_error_also_falls_back(rows, loads):
    client = FakeClient(RuntimeError('connection reset'), RuntimeError('connection reset'))
    result = plan.plan_week(rows, loads, TODAY, client=client)
    assert result.used_fixtures is True
    assert 'connection reset' in result.reason


def test_an_over_budget_plan_is_reported_not_rejected(rows, loads):
    """The model may be right that there is too much work; say so rather than hide it."""
    client = FakeClient(json.dumps([
        item(hours=30.0, priority=1),
        item(module_code='COMP0010', title='Interpreter Implementation Project',
             hours=30.0, priority=2),
    ]))
    result = plan.plan_week(rows, loads, TODAY, client=client, hours_available=20.0)
    # 30h asked of Problem Set 1, which only has 23.8h left, plus 30h of the
    # interpreter's 70h: still far past a 20h week, and the UI must say so.
    assert result.total_hours == pytest.approx(53.8)
    assert result.over_budget_hours == pytest.approx(33.8)


def test_planning_never_writes_to_the_database(seeded, rows, loads):
    """A plan is a suggestion. Nothing it does may survive a page refresh."""
    def snapshot():
        return (
            [dict(r) for r in db.list_assessments(seeded)],
            seeded.execute('SELECT COUNT(*) FROM progress_log').fetchone()[0],
        )

    before = snapshot()
    plan.plan_week(rows, loads, TODAY, fixtures_mode=True, client=ForbiddenClient())
    plan.plan_week(rows, loads, TODAY, client=FakeClient(json.dumps([VALID_ITEM])))
    assert snapshot() == before
