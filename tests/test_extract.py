"""Tests for radar/extract.py. Everything here runs offline against fixtures.

No test may reach the network: the Anthropic client is always injected, and the
fixture-replay tests pass a client that fails the test if it is called at all.
"""

from datetime import date
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from radar import extract
from radar.extract import ExtractionError, ExtractionResult

SAMPLE_DATA = _ROOT / 'sample_data'

# The counts recorded in sample_data/README.md by A4. Extraction must agree with
# them; if either side changes, this is the test that should fail.
EXPECTED_COUNTS = {
    'COMP0197_applied_machine_learning_systems.pdf': 4,
    'STAT0023_statistical_computing.pdf': 3,
    'ANTH0045_digital_anthropology.pdf': 3,
}

VALID_ROW = {
    'module_code': 'COMP0197',
    'module_name': 'Applied Machine Learning Systems',
    'title': 'Problem Set 1',
    'type': 'problem_set',
    'due_date': '2026-12-04',
    'due_time': '16:00:00',
    'weighting_pct': 15,
    'word_count': None,
    'estimated_hours': 12.0,
    'confidence': 'high',
    'source_quote': 'Submit by 4 December 2026, 16:00.',
    'notes': '',
}


def row(**overrides):
    """A valid extracted row with fields replaced or removed (pass None to drop)."""
    item = dict(VALID_ROW)
    for key, value in overrides.items():
        if value is _DROP:
            item.pop(key, None)
        else:
            item[key] = value
    return item


_DROP = object()


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
        return SimpleNamespace(content=[SimpleNamespace(type='text', text=reply)])


class ForbiddenClient:
    """Fails the test if the extractor tries to call out at all."""

    @property
    def messages(self):
        raise AssertionError('the API was called when fixtures should have been replayed')


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Neither env var leaks in from the developer's shell."""
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.delenv('DEADLINE_RADAR_FIXTURES', raising=False)


# ---------------------------------------------------------------------------
# Module hygiene
# ---------------------------------------------------------------------------

def test_extract_module_does_not_import_streamlit():
    """Business logic stays out of the UI layer (CLAUDE.md conventions)."""
    source = (_ROOT / 'radar' / 'extract.py').read_text(encoding='utf-8')
    assert 'streamlit' not in source


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

def test_valid_payload_parses():
    [parsed] = extract.validate_batch([VALID_ROW])
    assert parsed['module_code'] == 'COMP0197'
    assert parsed['type'] == 'problem_set'
    assert parsed['due_date'] == '2026-12-04'
    assert parsed['due_time'] == '16:00:00'
    assert parsed['weighting_pct'] == 15.0
    assert parsed['word_count'] is None
    assert parsed['estimated_hours'] == 12.0
    assert parsed['confidence'] == 'high'
    assert parsed['source_quote'].startswith('Submit by 4 December')


def test_missing_source_quote_is_rejected():
    with pytest.raises(ExtractionError, match='source_quote'):
        extract.validate_batch([row(source_quote='')])


@pytest.mark.parametrize('key', extract.REQUIRED_KEYS)
def test_every_required_key_is_enforced(key):
    with pytest.raises(ExtractionError, match=key):
        extract.validate_batch([row(**{key: _DROP})])


@pytest.mark.parametrize('bad_type', ['dissertation', 'quiz', '', 'ESSAY ME'])
def test_bad_type_is_rejected(bad_type):
    with pytest.raises(ExtractionError):
        extract.validate_batch([row(type=bad_type)])


def test_bad_confidence_is_rejected():
    with pytest.raises(ExtractionError, match='confidence'):
        extract.validate_batch([row(confidence='certain')])


def test_type_and_confidence_are_case_insensitive():
    [parsed] = extract.validate_batch([row(type='COURSEWORK', confidence='High')])
    assert parsed['type'] == 'coursework'
    assert parsed['confidence'] == 'high'


def test_non_list_payload_is_rejected():
    with pytest.raises(ExtractionError, match='expected a JSON array'):
        extract.validate_batch({'assessments': []})


def test_non_object_row_is_rejected():
    with pytest.raises(ExtractionError, match='expected an object'):
        extract.validate_batch(['Coursework 1 due Friday'])


@pytest.mark.parametrize('weighting', [-1, 101, 250])
def test_weighting_outside_0_100_is_rejected(weighting):
    with pytest.raises(ExtractionError, match='weighting_pct'):
        extract.validate_batch([row(weighting_pct=weighting)])


def test_non_numeric_weighting_is_rejected():
    with pytest.raises(ExtractionError, match='weighting_pct'):
        extract.validate_batch([row(weighting_pct='about a third')])


def test_negative_word_count_is_rejected():
    with pytest.raises(ExtractionError, match='word_count'):
        extract.validate_batch([row(word_count=-10)])


def test_null_weighting_and_word_count_survive():
    [parsed] = extract.validate_batch([row(weighting_pct=None, word_count=None)])
    assert parsed['weighting_pct'] is None
    assert parsed['word_count'] is None


def test_empty_batch_is_valid():
    assert extract.validate_batch([]) == []


# ---------------------------------------------------------------------------
# Week references resolve through radar.calendar, not through the model
# ---------------------------------------------------------------------------

def test_iso_date_keeps_the_models_confidence():
    [parsed] = extract.validate_batch([row(due_date='2026-12-04', confidence='medium')])
    assert parsed['due_date'] == '2026-12-04'
    assert parsed['confidence'] == 'medium'


def test_ambiguous_term_week_resolves_and_downgrades_confidence():
    """'Term 1 Week 10' could be calendar or teaching numbering, so it drags to low."""
    [parsed] = extract.validate_batch([row(due_date='Term 1 Week 10', confidence='high')])
    assert parsed['due_date'] == '2026-11-30'
    assert parsed['confidence'] == 'low'


def test_explicit_timetable_week_keeps_high_confidence():
    """A named numbering scheme is unambiguous, so confidence survives."""
    [parsed] = extract.validate_batch([row(due_date='UCL timetable week 14', confidence='high')])
    assert parsed['due_date'] == '2026-11-30'
    assert parsed['confidence'] == 'high'


def test_bare_week_number_resolves_to_term_one():
    [parsed] = extract.validate_batch([row(due_date='Week 5', confidence='high')])
    assert parsed['due_date'] == '2026-10-26'
    assert parsed['confidence'] == 'low'


@pytest.mark.parametrize('phrase', [
    'two weeks after the reading week',
    'second half of term',
    'to be announced',
])
def test_unresolvable_date_is_quarantined_not_rejected(phrase):
    """A handbook can genuinely leave a date open; that is what low confidence is for."""
    [parsed] = extract.validate_batch([row(due_date=phrase, confidence='high')])
    assert parsed['due_date'] == extract.UNRESOLVED_DUE_DATE
    assert parsed['confidence'] == 'low'
    assert phrase in parsed['notes']
    assert date.fromisoformat(parsed['due_date'])


def test_quarantine_note_is_prepended_to_existing_notes():
    [parsed] = extract.validate_batch([row(due_date='whenever', notes='Submit via Moodle')])
    assert 'Submit via Moodle' in parsed['notes']
    assert 'correct before confirming' in parsed['notes']


# ---------------------------------------------------------------------------
# estimated_hours
# ---------------------------------------------------------------------------

def test_missing_estimated_hours_is_proposed_from_type_and_word_count():
    [parsed] = extract.validate_batch([
        row(estimated_hours=_DROP, word_count=2500, weighting_pct=35)])
    assert parsed['estimated_hours'] == 25.0


def test_proposal_falls_back_to_type_when_no_word_count():
    [parsed] = extract.validate_batch([
        row(estimated_hours=_DROP, type='presentation', word_count=None, weighting_pct=None)])
    assert parsed['estimated_hours'] == extract.BASE_HOURS_BY_TYPE['presentation']


def test_heavy_weighting_lifts_a_thin_proposal():
    """A 60% component should not be estimated at 10 hours just for lacking a word count."""
    [parsed] = extract.validate_batch([
        row(estimated_hours=_DROP, type='problem_set', word_count=None, weighting_pct=60)])
    assert parsed['estimated_hours'] == 30.0


def test_proposal_is_capped():
    assert extract.propose_estimated_hours('essay', 100, 100_000) == extract.MAX_ESTIMATED_HOURS


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('fence', ['```json\n{body}\n```', '```\n{body}\n```'])
def test_code_fenced_json_is_unwrapped(fence):
    text = fence.format(body=json.dumps([VALID_ROW]))
    assert extract.parse_response(text)[0]['title'] == 'Problem Set 1'


def test_malformed_json_raises_extraction_error():
    with pytest.raises(ExtractionError, match='not valid JSON'):
        extract.parse_response('here you go: [{"title": ')


# ---------------------------------------------------------------------------
# The API path
# ---------------------------------------------------------------------------

def test_successful_call_returns_rows_stamped_extracted():
    client = FakeClient(json.dumps([VALID_ROW]))
    result = extract.extract_from_pdf(b'%PDF-1.4', 'COMP0197.pdf', client=client)
    assert result.source == 'api'
    assert result.used_fixtures is False
    assert [r['source'] for r in result.assessments] == ['extracted']
    assert len(client.calls) == 1


def test_pdf_is_sent_as_a_base64_document_block():
    client = FakeClient(json.dumps([VALID_ROW]))
    extract.extract_from_pdf(b'%PDF-1.4 body', 'COMP0197.pdf', client=client)
    content = client.calls[0]['messages'][0]['content']
    document = next(b for b in content if b['type'] == 'document')
    assert document['source']['media_type'] == 'application/pdf'
    assert document['source']['type'] == 'base64'
    # The bytes travel as base64, not as pasted text.
    import base64 as b64
    assert b64.b64decode(document['source']['data']) == b'%PDF-1.4 body'


def test_the_configured_model_is_never_silently_substituted():
    """A1: a missing model must surface, not fall through to an older one."""
    client = FakeClient(json.dumps([VALID_ROW]))
    extract.extract_from_pdf(b'%PDF', 'COMP0197.pdf', client=client)
    assert {call['model'] for call in client.calls} == {extract.MODEL}


def test_malformed_json_triggers_exactly_one_retry():
    client = FakeClient('not json at all', json.dumps([VALID_ROW]))
    result = extract.extract_from_pdf(b'%PDF', 'COMP0197.pdf', client=client)
    assert len(client.calls) == 2
    assert result.source == 'api'


def test_a_second_failure_stops_and_falls_back_to_fixtures():
    """Two attempts and no third, then the fabricated fallback says why."""
    client = FakeClient('not json', 'still not json')
    result = extract.extract_from_pdf(
        b'%PDF', 'COMP0197_applied_machine_learning_systems.pdf', client=client)
    assert len(client.calls) == 2
    assert result.used_fixtures is True
    assert 'every Claude attempt failed' in result.reason
    assert all(r['source'] == 'fixture' for r in result.assessments)


def test_a_rejected_row_costs_an_attempt_like_bad_json():
    client = FakeClient(json.dumps([row(type='dissertation')]), json.dumps([VALID_ROW]))
    result = extract.extract_from_pdf(b'%PDF', 'COMP0197.pdf', client=client)
    assert len(client.calls) == 2
    assert result.source == 'api'


def test_api_errors_fall_back_with_a_reason():
    client = FakeClient(RuntimeError('503 overloaded'), RuntimeError('503 overloaded'))
    result = extract.extract_from_pdf(b'%PDF', 'COMP0197.pdf', client=client)
    assert result.used_fixtures is True
    assert '503 overloaded' in result.reason


def test_injected_client_is_used_even_without_an_api_key():
    client = FakeClient(json.dumps([VALID_ROW]))
    result = extract.extract_from_pdf(b'%PDF', 'COMP0197.pdf', client=client)
    assert result.source == 'api'


# ---------------------------------------------------------------------------
# Fixture replay: the demo-day safety net A12 depends on
# ---------------------------------------------------------------------------

def test_missing_api_key_replays_fixtures_without_calling_out():
    result = extract.extract_from_pdf(
        b'%PDF', 'COMP0197_applied_machine_learning_systems.pdf', client=None)
    assert result.used_fixtures is True
    assert 'ANTHROPIC_API_KEY' in result.reason


def test_fixtures_env_var_skips_the_api(monkeypatch):
    monkeypatch.setenv('DEADLINE_RADAR_FIXTURES', '1')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-would-work-if-called')
    result = extract.extract_from_pdf(
        b'%PDF', 'COMP0197_applied_machine_learning_systems.pdf',
        client=ForbiddenClient())
    assert result.used_fixtures is True
    assert 'DEADLINE_RADAR_FIXTURES' in result.reason


def test_fixtures_mode_argument_overrides_the_environment(monkeypatch):
    monkeypatch.setenv('DEADLINE_RADAR_FIXTURES', '1')
    client = FakeClient(json.dumps([VALID_ROW]))
    result = extract.extract_from_pdf(
        b'%PDF', 'COMP0197.pdf', client=client, fixtures_mode=False)
    assert result.source == 'api'


def test_every_fixture_file_satisfies_the_schema():
    """A broken fixture must fail here, not silently feed the demo bad rows."""
    files = sorted(extract.FIXTURES_DIR.glob('*.json'))
    assert files, 'no fixtures found'
    for path in files:
        raw = json.loads(path.read_text(encoding='utf-8'))
        rows = raw.get('assessments') if isinstance(raw, dict) else raw
        assert extract.validate_batch(rows), f'{path.name} produced no rows'


def test_unknown_filename_falls_back_to_the_default_fixture():
    assert extract.fixture_path('something_unseen.pdf').stem == extract.DEFAULT_FIXTURE
    result = extract.extract_from_pdf(b'%PDF', 'something_unseen.pdf')
    assert result.used_fixtures is True
    assert result.assessments


@pytest.mark.parametrize('filename', sorted(EXPECTED_COUNTS))
def test_fixture_routes_to_the_matching_module(filename):
    code = filename.split('_')[0].lower()
    assert extract.fixture_path(filename).stem.startswith(code)


# ---------------------------------------------------------------------------
# A5's done-when: the sample handbooks return A4's documented counts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('filename,expected', sorted(EXPECTED_COUNTS.items()))
def test_sample_handbook_returns_the_documented_count(filename, expected):
    pdf = SAMPLE_DATA / filename
    assert pdf.exists(), f'{filename} is missing from sample_data/'
    result = extract.extract_from_pdf(
        pdf.read_bytes(), filename, client=ForbiddenClient(), fixtures_mode=True)
    assert len(result.assessments) == expected


def test_sample_handbooks_total_ten_assessments():
    """sample_data/README.md promises ten across the three files."""
    total = sum(
        len(extract.extract_from_pdf(
            (SAMPLE_DATA / name).read_bytes(), name, fixtures_mode=True).assessments)
        for name in EXPECTED_COUNTS
    )
    assert total == 10


def test_sample_handbook_dates_match_the_readme():
    """The resolved dates and confidences A4 documented, end to end through the resolver."""
    expected = {
        'COMP0197_applied_machine_learning_systems.pdf': [
            ('2026-12-04', 'high'), ('2026-10-26', 'low'),
            ('2026-11-30', 'high'), ('2026-12-10', 'high'),
        ],
        'STAT0023_statistical_computing.pdf': [
            ('2026-11-30', 'low'),
            (extract.UNRESOLVED_DUE_DATE, 'low'),   # known gap, see sample_data/README.md
            ('2027-01-11', 'medium'),
        ],
        'ANTH0045_digital_anthropology.pdf': [
            ('2027-03-05', 'high'), ('2027-03-05', 'medium'),
            (extract.UNRESOLVED_DUE_DATE, 'low'),
        ],
    }
    for name, pairs in expected.items():
        result = extract.extract_from_pdf(
            (SAMPLE_DATA / name).read_bytes(), name, fixtures_mode=True)
        assert [(r['due_date'], r['confidence']) for r in result.assessments] == pairs, name


def test_sample_handbook_nullable_columns_are_exercised():
    """A4 promised rows with no weighting and rows with no word count."""
    rows = []
    for name in EXPECTED_COUNTS:
        rows += extract.extract_from_pdf(
            (SAMPLE_DATA / name).read_bytes(), name, fixtures_mode=True).assessments
    assert any(r['weighting_pct'] is None for r in rows)
    assert any(r['word_count'] is None for r in rows)
    assert any(r['due_time'] is None for r in rows)


def test_every_replayed_row_is_labelled_fixture():
    """CLAUDE.md: a fabricated row must never claim to be a real extraction."""
    for name in EXPECTED_COUNTS:
        result = extract.extract_from_pdf(
            (SAMPLE_DATA / name).read_bytes(), name, fixtures_mode=True)
        assert all(r['source'] == 'fixture' for r in result.assessments)
        assert all(r['source_quote'].strip() for r in result.assessments)


# ---------------------------------------------------------------------------
# ExtractionResult
# ---------------------------------------------------------------------------

def test_result_stamps_provenance_on_every_row():
    rows = extract.validate_batch([VALID_ROW, row(title='Coursework 1')])
    assert [r['source'] for r in ExtractionResult(rows, 'api').assessments] == ['extracted'] * 2
    rows = extract.validate_batch([VALID_ROW])
    assert ExtractionResult(rows, 'fixture').assessments[0]['source'] == 'fixture'

@pytest.mark.parametrize('field,value', [
    ('estimated_hours', float('nan')), ('estimated_hours', float('inf')),
    ('word_count', 2.5), ('word_count', True), ('word_count', float('inf')),
    ('due_time', '25:00'), ('due_time', '16:00+01:00'),
])
def test_extraction_rejects_values_that_cannot_be_saved(field, value):
    with pytest.raises(ExtractionError):
        extract.validate_row({**VALID_ROW, field: value})


def test_sdk_retries_are_disabled_and_timeout_is_bounded(monkeypatch):
    import anthropic
    captured = {}
    class Client:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.messages = self
        def create(self, **kwargs):
            return SimpleNamespace(content=[SimpleNamespace(type='text', text=json.dumps([VALID_ROW]))])
    monkeypatch.setattr(anthropic, 'Anthropic', Client)
    result = extract.extract_from_pdf(b'%PDF-1.4', 'test.pdf',
                                      api_key='test-only', fixtures_mode=False)
    assert result.source == 'api'
    assert captured['max_retries'] == 0
    assert captured['timeout'] == extract.API_TIMEOUT_SECONDS
