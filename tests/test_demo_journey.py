"""Rehearse the student journey with actual sample bytes and isolated storage."""
import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from icalendar import Calendar
from streamlit.testing.v1 import AppTest

import app
from radar import db, extract, export, plan

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('offline', [True, False], ids=['offline', 'mocked-api'])
def test_complete_demo_journey(tmp_path, monkeypatch, offline):
    monkeypatch.setenv('DEADLINE_RADAR_FIXTURES', '1' if offline else '0')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-only-never-sent')
    received = []
    generated = []
    original_export = export.export_calendar
    def capture_export(rows):
        data = original_export(rows)
        generated.append(data)
        return data
    monkeypatch.setattr(export, 'export_calendar', capture_export)
    if not offline:
        import anthropic
        class FakeClient:
            def __init__(self, **kwargs):
                self.messages = self
            def create(self, **kwargs):
                assert kwargs['model'] == extract.MODEL
                content = kwargs['messages'][0]['content']
                if content[0]['type'] == 'document':
                    received.append(base64.b64decode(content[0]['source']['data']))
                    payload = json.loads((extract.FIXTURES_DIR /
                                          'anth0045_digital_anthropology.json').read_text())['assessments']
                else:
                    payload = json.loads(plan.PLAN_FIXTURE.read_text())['plan']
                return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(payload))])
        monkeypatch.setattr(anthropic, 'Anthropic', FakeClient)
    path = tmp_path / 'demo.db'
    at = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=20)
    at.session_state['db_path'] = str(path)
    at.run()
    at.sidebar.button(key='side_seed').click().run()
    assert not at.exception
    assert next(m.value for m in at.metric if m.label == 'Total Tracked') == '10 items'
    at.sidebar.radio(key='main_nav_radio').set_value('📄 Upload & Review').run()
    handbook = ROOT / 'sample_data' / 'ANTH0045_digital_anthropology.pdf'
    at.file_uploader[0].set_value((handbook.name, handbook.read_bytes(), 'application/pdf')).run()
    next(b for b in at.button if 'Extract Assessments' in b.label).click().run()
    assert not at.exception
    assert len(at.session_state['review_queue']) == 3
    if not offline:
        assert received == [handbook.read_bytes()]
    conn = db.connect(path)
    assert len(db.list_assessments(conn)) == 10  # review is a gate
    conn.close()
    # Simulate the native editor's date correction and row deletion payload.
    at.session_state['review_data_editor'] = {
        'edited_rows': {2: {'due_date': '2027-02-25'}},
        'added_rows': [], 'deleted_rows': [1],
    }
    next(b for b in at.button if 'Confirm & Save' in b.label).click().run()
    assert not at.exception
    assert at.session_state['review_queue'] == []
    conn = db.connect(path)
    rows = db.list_assessments(conn)
    assert len(rows) == 12
    presentation = next(r for r in rows if r['title'] == 'Seminar presentation')
    assert presentation['due_date'] == '2027-02-25'
    assert 'corrected during review to 2027-02-25' in presentation['notes']
    assert presentation['source'] == ('fixture' if offline else 'extracted')
    at.sidebar.radio(key='main_nav_radio').set_value('📊 Dashboard').run()
    assert not at.exception
    assert any('Research essay' in m.value for m in at.markdown)
    at.sidebar.radio(key='main_nav_radio').set_value('📈 Workload Heatmap').run()
    assert not at.exception
    assert len(at.get('plotly_chart')) == 4
    at.sidebar.radio(key='main_nav_radio').set_value('🗓️ Plan My Week').run()
    at.button(key='plan_run').click().run()
    assert not at.exception
    assert at.session_state['week_plan'].items
    assert at.session_state['week_plan'].used_fixtures is offline
    assert at.get('download_button')
    events = Calendar.from_ical(generated[-1]).walk('VEVENT')
    assert len(events) == (10 if offline else 12)
    assert all('Fieldwork notebook' not in str(e['SUMMARY']) for e in events)
    conn.close()


def test_review_corrections_survive_navigation(tmp_path, monkeypatch):
    monkeypatch.setenv('DEADLINE_RADAR_FIXTURES', '1')
    path = tmp_path / 'review.db'
    at = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=20)
    at.session_state['db_path'] = str(path)
    at.session_state['review_queue'] = extract.ExtractionResult(
        extract.load_fixture('ANTH0045.pdf'), 'fixture').assessments
    at.run()
    at.sidebar.radio(key='main_nav_radio').set_value('📄 Upload & Review').run()
    at.session_state['review_data_editor'] = {
        'edited_rows': {2: {'due_date': '2027-02-25'}}, 'added_rows': [], 'deleted_rows': []}
    at.run()
    at.sidebar.radio(key='main_nav_radio').set_value('📊 Dashboard').run()
    at.sidebar.radio(key='main_nav_radio').set_value('📄 Upload & Review').run()
    next(b for b in at.button if 'Confirm & Save' in b.label).click().run()
    assert not at.exception
    assert at.session_state['review_queue'] == []
    conn = db.connect(path)
    try:
        assert next(r for r in db.list_assessments(conn)
                    if r['title'] == 'Seminar presentation')['due_date'] == '2027-02-25'
    finally:
        conn.close()


def test_unresolved_date_must_be_corrected_before_save(tmp_path, monkeypatch):
    monkeypatch.setenv('DEADLINE_RADAR_FIXTURES', '1')
    path = tmp_path / 'unresolved.db'
    at = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=20)
    at.session_state['db_path'] = str(path)
    at.session_state['review_queue'] = extract.ExtractionResult(
        extract.load_fixture('ANTH0045.pdf'), 'fixture').assessments
    at.run()
    at.sidebar.radio(key='main_nav_radio').set_value('📄 Upload & Review').run()
    next(b for b in at.button if 'Confirm & Save' in b.label).click().run()
    assert not at.exception
    assert any('unresolved placeholder' in e.value for e in at.error)
    assert len(at.session_state['review_queue']) == 3
    conn = db.connect(path)
    try:
        assert db.list_assessments(conn) == []
    finally:
        conn.close()


def test_repeated_save_requires_explicit_duplicate_choice(tmp_path):
    path = tmp_path / 'duplicates.db'
    row = {'module_code': 'TEST0001', 'title': 'Essay', 'type': 'essay',
           'due_date': '2026-12-04', 'estimated_hours': 20, 'source': 'manual'}
    conn = db.connect(path)
    db.insert_assessment(conn, row)
    conn.close()
    at = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=20)
    at.session_state['db_path'] = str(path)
    at.session_state['review_queue'] = [row]
    at.run()
    at.sidebar.radio(key='main_nav_radio').set_value('📄 Upload & Review').run()
    next(b for b in at.button if 'Confirm & Save' in b.label).click().run()
    assert not at.exception
    assert any('duplicate rows' in e.value for e in at.error)
    conn = db.connect(path)
    assert len(db.list_assessments(conn)) == 1
    conn.close()
    at.checkbox(key='review_allow_duplicates').check().run()
    next(b for b in at.button if 'Confirm & Save' in b.label).click().run()
    assert not at.exception
    conn = db.connect(path)
    assert len(db.list_assessments(conn)) == 2
    conn.close()
