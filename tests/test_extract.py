import json
import subprocess
import sys
from datetime import date

import pytest

from radar.db import ASSESSMENT_TYPES, CONFIDENCES
from radar.extract import (
    MODEL, UNRESOLVED_DUE_DATE, ExtractionResult, extract_from_pdf,
    generate_fixture_extractions, normalize_extracted_batch,
)

PDF = b'%PDF-1.4 handbook'

REQUIRED_KEYS = {
    'module_code', 'module_name', 'title', 'type', 'due_date', 'due_time',
    'weighting_pct', 'word_count', 'estimated_hours', 'confidence',
    'source_quote', 'notes',
}


class FakeMessages:
    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self._replies.pop(0) if self._replies else ''
        if isinstance(reply, Exception):
            raise reply
        return type('Response', (), {'content': [type('Block', (), {'text': reply})()]})()


class FakeClient:
    def __init__(self, *replies):
        self.messages = FakeMessages(replies)


def api_payload(**overrides):
    row = {
        'module_code': 'COMP0002', 'module_name': 'Software Engineering',
        'title': 'Coursework 1', 'type': 'coursework', 'due_date': '2026-11-16',
        'due_time': '16:00:00', 'weighting_pct': 30, 'word_count': 2500,
        'estimated_hours': 25, 'confidence': 'high',
        'source_quote': 'Submit by 16 November 2026.', 'notes': '',
    }
    row.update(overrides)
    return json.dumps([row])


# ---------------------------------------------------------------------------
# Module boundary
# ---------------------------------------------------------------------------
def test_extract_module_does_not_import_streamlit():
    """Business logic must stay importable outside the Streamlit runtime."""
    code = 'import radar.extract, sys; assert "streamlit" not in sys.modules'
    assert subprocess.run([sys.executable, '-c', code], capture_output=True).returncode == 0


# ---------------------------------------------------------------------------
# Fixture replay
# ---------------------------------------------------------------------------
def test_missing_api_key_falls_back_to_fixtures(monkeypatch):
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.delenv('DEADLINE_RADAR_FIXTURES', raising=False)
    result = extract_from_pdf(PDF, 'COMP0004_Algorithms.pdf')
    assert result.source == 'fixture'
    assert result.used_fixtures
    assert result.error is None
    assert len(result.assessments) == 2


def test_fixtures_mode_env_var_skips_api(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-live-key')
    monkeypatch.setenv('DEADLINE_RADAR_FIXTURES', '1')
    assert extract_from_pdf(PDF, 'handbook.pdf').source == 'fixture'


def test_fixtures_mode_argument_overrides_env(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-live-key')
    monkeypatch.setenv('DEADLINE_RADAR_FIXTURES', '0')
    assert extract_from_pdf(PDF, 'handbook.pdf', fixtures_mode=True).source == 'fixture'


@pytest.mark.parametrize('filename,expected_code', [
    ('COMP0004_Algorithms.pdf', 'COMP0004'),
    ('algorithms-handbook.pdf', 'COMP0004'),
    ('COMP0002_Software_Engineering.pdf', 'COMP0002'),
    ('software_engineering.pdf', 'COMP0002'),
])
def test_fixture_routing_by_filename(filename, expected_code):
    rows = generate_fixture_extractions(filename)
    assert all(row['module_code'] == expected_code for row in rows)


def test_unrecognised_filename_derives_module_from_stem():
    rows = generate_fixture_extractions('PHYS0010_Quantum.pdf')
    assert rows[0]['module_name'] == 'PHYS0010 QUANTUM'
    assert rows[0]['module_code'] == 'PHYS0010'


def test_short_unrecognised_filename_uses_placeholder_code():
    assert generate_fixture_extractions('mod.pdf')[0]['module_code'] == 'COMP0099'


@pytest.mark.parametrize('filename', [
    'COMP0004_Algorithms.pdf', 'COMP0002_Software_Engineering.pdf', 'other.pdf',
])
def test_every_fixture_row_satisfies_the_data_model(filename):
    for row in generate_fixture_extractions(filename):
        assert set(row) == REQUIRED_KEYS
        assert row['type'] in ASSESSMENT_TYPES
        assert row['confidence'] in CONFIDENCES
        assert row['source_quote'].strip()
        assert date.fromisoformat(row['due_date'])


def test_ambiguous_week_fixture_is_flagged_low_confidence():
    rows = generate_fixture_extractions('COMP0004_Algorithms.pdf')
    assert rows[1]['confidence'] == 'low'
    assert 'Week 10' in rows[1]['source_quote']


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------
def test_normalize_coerces_strings_and_casing():
    item = normalize_extracted_batch([{
        'module_code': ' comp0002 ', 'title': ' Coursework 1 ', 'type': 'COURSEWORK',
        'due_date': '2026-11-16', 'weighting_pct': '30', 'word_count': '2500',
        'estimated_hours': '25', 'confidence': 'HIGH', 'source_quote': ' Due 16 Nov ',
    }])[0]
    assert item['module_code'] == 'comp0002'
    assert item['title'] == 'Coursework 1'
    assert item['type'] == 'coursework'
    assert item['weighting_pct'] == 30.0
    assert item['word_count'] == 2500
    assert item['estimated_hours'] == 25.0
    assert item['confidence'] == 'high'
    assert item['source_quote'] == 'Due 16 Nov'


def test_normalize_applies_defaults_for_missing_fields():
    item = normalize_extracted_batch([{'due_date': '2026-11-16'}])[0]
    assert item['module_code'] == 'UCL0001'
    assert item['title'] == 'Assessment'
    assert item['type'] == 'coursework'
    assert item['estimated_hours'] == 20.0
    assert item['weighting_pct'] is None
    assert item['word_count'] is None
    assert item['due_time'] is None
    assert item['notes'] == ''


@pytest.mark.parametrize('bad_type', ['dissertation', '', 'ESSAY_LONG'])
def test_unknown_type_falls_back_to_coursework(bad_type):
    item = normalize_extracted_batch([{'due_date': '2026-11-16', 'type': bad_type}])[0]
    assert item['type'] == 'coursework'


@pytest.mark.parametrize('bad_confidence', ['certain', '', 'very high'])
def test_unknown_confidence_falls_back_to_medium(bad_confidence):
    item = normalize_extracted_batch(
        [{'due_date': '2026-11-16', 'confidence': bad_confidence}]
    )[0]
    assert item['confidence'] == 'medium'


def test_iso_due_date_preserves_model_confidence():
    item = normalize_extracted_batch(
        [{'due_date': '2026-11-16', 'confidence': 'high'}]
    )[0]
    assert item['due_date'] == '2026-11-16'
    assert item['confidence'] == 'high'


def test_ambiguous_week_reference_is_resolved_and_downgraded():
    """'Term 1 Week 3' is resolvable but scheme-ambiguous, so confidence drops."""
    item = normalize_extracted_batch(
        [{'due_date': 'Term 1 Week 3', 'confidence': 'high'}]
    )[0]
    assert item['due_date'] == '2026-10-12'
    assert item['confidence'] == 'low'


def test_unambiguous_week_commencing_keeps_high_confidence():
    item = normalize_extracted_batch(
        [{'due_date': 'week commencing 9 November 2026', 'confidence': 'high'}]
    )[0]
    assert item['due_date'] == '2026-11-09'
    assert item['confidence'] == 'high'


@pytest.mark.parametrize('raw', ['', 'sometime before Christmas', 'Week 99'])
def test_unresolvable_due_date_is_quarantined_as_low_confidence(raw):
    item = normalize_extracted_batch([{'due_date': raw, 'confidence': 'high'}])[0]
    assert item['due_date'] == UNRESOLVED_DUE_DATE
    assert item['confidence'] == 'low'
    assert date.fromisoformat(item['due_date'])


def test_normalize_empty_batch():
    assert normalize_extracted_batch([]) == []


# ---------------------------------------------------------------------------
# API path
# ---------------------------------------------------------------------------
def test_successful_api_call_returns_normalised_rows():
    client = FakeClient(api_payload())
    result = extract_from_pdf(PDF, 'handbook.pdf', client=client)
    assert result.source == 'api'
    assert not result.used_fixtures
    assert result.error is None
    assert result.assessments[0]['module_code'] == 'COMP0002'
    assert set(result.assessments[0]) == REQUIRED_KEYS


def test_api_call_sends_pdf_as_base64_document_block():
    client = FakeClient(api_payload())
    extract_from_pdf(PDF, 'handbook.pdf', client=client)
    call = client.messages.calls[0]
    assert call['model'] == MODEL
    document, text = call['messages'][0]['content']
    assert document['source']['media_type'] == 'application/pdf'
    assert document['source']['type'] == 'base64'
    import base64 as b64
    assert b64.b64decode(document['source']['data']) == PDF
    assert 'JSON array' in text['text']


@pytest.mark.parametrize('fenced', [
    '```json\n[]\n```',
    '```\n[]\n```',
])
def test_code_fenced_json_is_unwrapped(fenced):
    result = extract_from_pdf(PDF, 'handbook.pdf', client=FakeClient(fenced))
    assert result.source == 'api'
    assert result.assessments == []


def test_invalid_json_is_retried_once_before_the_next_model():
    client = FakeClient('not json', api_payload())
    result = extract_from_pdf(PDF, 'handbook.pdf', client=client)
    assert result.source == 'api'
    assert len(client.messages.calls) == 2
    assert [c['model'] for c in client.messages.calls] == [MODEL, MODEL]


def test_api_errors_fall_back_to_fixtures_with_a_reason():
    client = FakeClient(*[RuntimeError('overloaded')] * 6)
    result = extract_from_pdf(PDF, 'COMP0004_Algorithms.pdf', client=client)
    assert result.source == 'fixture'
    assert result.error == 'overloaded'
    assert len(result.assessments) == 2


def test_non_list_json_falls_back_to_fixtures():
    client = FakeClient(*['{"module_code": "COMP0002"}'] * 6)
    result = extract_from_pdf(PDF, 'handbook.pdf', client=client)
    assert result.source == 'fixture'
    assert result.error == 'response was not a JSON array'


def test_injected_client_is_used_even_without_an_api_key(monkeypatch):
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    result = extract_from_pdf(PDF, 'handbook.pdf', client=FakeClient(api_payload()))
    assert result.source == 'api'


def test_extraction_result_is_immutable():
    with pytest.raises(Exception):
        ExtractionResult([], 'api').source = 'fixture'
