"""Interaction coverage for the redesigned productivity interface."""
from datetime import date
from pathlib import Path
import tomllib

import pytest
from streamlit.testing.v1 import AppTest

from radar import db
from sample_data.seed import seed_demo_data
from tests.ui_helpers import navigate

ROOT = Path(__file__).resolve().parents[1]


def start_app(tmp_path, seeded=True):
    path = tmp_path / 'redesign.db'
    conn = db.connect(path)
    if seeded:
        seed_demo_data(conn)
    conn.close()
    at = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=20)
    at.session_state['db_path'] = str(path)
    at.run()
    assert not at.exception
    return at, path


def snapshot(path):
    conn = db.connect(path)
    try:
        return (db.list_assessments(conn),
                [tuple(r) for r in conn.execute('SELECT * FROM progress_log ORDER BY id')])
    finally:
        conn.close()


@pytest.mark.parametrize('seeded', [True, False], ids=['populated', 'empty'])
def test_every_page_renders_without_modifying_student_data(tmp_path, seeded):
    at, path = start_app(tmp_path, seeded)
    before = snapshot(path)
    for page in ('Overview', 'Assessments', 'Planner', 'Workload', 'Import', 'Settings'):
        navigate(at, page)
        assert not at.exception, page
        assert at.title[0].value == page
    assert snapshot(path) == before


def test_quick_edit_targets_the_selected_row_and_preserves_source(tmp_path):
    at, path = start_app(tmp_path)
    rows = snapshot(path)[0]
    target = next(r for r in rows if r['title'] == 'Concurrency Kernel Lab')
    navigate(at, 'Assessments')
    at.button(key=f"list_edit_{target['id']}").click().run()
    assert not at.exception
    assert at.segmented_control(key='assessment_view').value == 'Edit'
    assert at.text_input[1].value == target['title']
    at.text_input[1].set_value('Concurrency Kernel Lab — final submission')
    next(b for b in at.button if b.label == 'Save changes').click().run()
    assert not at.exception
    updated = next(r for r in snapshot(path)[0] if r['id'] == target['id'])
    assert updated['title'] == 'Concurrency Kernel Lab — final submission'
    assert updated['source'] == target['source']
    assert at.success


def test_quick_progress_logs_against_the_selected_assessment(tmp_path):
    at, path = start_app(tmp_path)
    target = next(r for r in snapshot(path)[0] if r['title'] == 'Interpreter Implementation Project')
    navigate(at, 'Assessments')
    at.button(key=f"list_progress_{target['id']}").click().run()
    assert at.segmented_control(key='assessment_view').value == 'Progress'
    assert target['title'] in at.selectbox(key='progress_selection').value
    at.slider[0].set_value(40)
    next(b for b in at.button if b.label == 'Record progress').click().run()
    assert not at.exception
    updated = next(r for r in snapshot(path)[0] if r['id'] == target['id'])
    assert updated['progress_pct'] == 40
    conn = db.connect(path)
    try:
        assert db.get_progress_history(conn, target['id'])[0]['progress_pct_after'] == 40
    finally:
        conn.close()
    assert at.dataframe
    assert at.success


def test_add_delete_and_confirmation_are_reachable(tmp_path):
    at, path = start_app(tmp_path, seeded=False)
    navigate(at, 'Assessments')
    at.segmented_control(key='assessment_view').set_value('Add').run()
    at.text_input[0].set_value('TEST0001')
    at.text_input[1].set_value('Design review')
    next(b for b in at.button if b.label == 'Save assessment').click().run()
    assert not at.exception
    assert len(snapshot(path)[0]) == 1
    at.segmented_control(key='assessment_view').set_value('Delete').run()
    delete = next(b for b in at.button if b.label == 'Delete assessment')
    assert delete.disabled
    at.checkbox[0].check().run()
    next(b for b in at.button if b.label == 'Delete assessment').click().run()
    assert not at.exception
    assert snapshot(path)[0] == []


def test_search_filters_and_sorting_keep_assessments_accessible(tmp_path):
    at, _ = start_app(tmp_path)
    navigate(at, 'Assessments')
    at.text_input(key='assessment_search').set_value('interpreter').run()
    assert any('Interpreter Implementation Project' in m.value for m in at.markdown)
    assert not any('Concurrency Kernel Lab' in m.value for m in at.markdown)
    at.text_input(key='assessment_search').set_value('no such assessment').run()
    assert any('No assessments match' in c.value for c in at.caption)
    at.text_input(key='assessment_search').set_value('')
    at.multiselect(key='assessment_statuses').set_value(['submitted']).run()
    assert any('Submitted' in m.value for m in at.markdown)
    at.multiselect(key='assessment_statuses').set_value(['not_started', 'in_progress'])
    at.selectbox(key='assessment_sort').set_value('Risk first').run()
    assert not at.exception


def test_reference_date_survives_page_widget_cleanup(tmp_path):
    at, _ = start_app(tmp_path)
    navigate(at, 'Settings')
    at.date_input(key='simulated_today_picker').set_value(date(2026, 11, 2)).run()
    navigate(at, 'Overview')
    assert at.session_state['reference_today'] == date(2026, 11, 2)
    assert any('02 November 2026' in c.value for c in at.caption)
    navigate(at, 'Planner')
    assert any('02 Nov' in s.value for s in at.subheader)
    navigate(at, 'Settings')
    assert at.date_input(key='simulated_today_picker').value == date(2026, 11, 2)


def test_workload_switches_terms_and_plan_survives_budget_change(tmp_path, monkeypatch):
    monkeypatch.setenv('DEADLINE_RADAR_FIXTURES', '1')
    at, _ = start_app(tmp_path)
    navigate(at, 'Workload')
    for term in ('All terms', 'Term 1', 'Term 2', 'Term 3'):
        at.segmented_control(key='workload_term').set_value(term).run()
        assert not at.exception
        assert len(at.get('plotly_chart')) == 1
    navigate(at, 'Planner')
    at.button(key='plan_run').click().run()
    assert not at.exception
    original = at.session_state['week_plan']
    at.slider(key='plan_budget').set_value(10.0).run()
    assert at.session_state['week_plan'] == original
    assert any('Showing a plan built for' in c.value for c in at.caption)
    assert any('Offline fixture plan' in w.value for w in at.warning)


def test_discard_clears_the_editor_draft(tmp_path, monkeypatch):
    from radar import extract
    monkeypatch.setenv('DEADLINE_RADAR_FIXTURES', '1')
    at, _ = start_app(tmp_path, seeded=False)
    at.session_state['review_queue'] = extract.ExtractionResult(extract.load_fixture('ANTH0045.pdf'), 'fixture').assessments
    navigate(at, 'Import')
    assert any('2. Review' in s.value for s in at.subheader)
    assert any('3. Save' in s.value for s in at.subheader)
    next(b for b in at.button if b.label == 'Discard review').click().run()
    assert not at.exception
    assert at.session_state['review_queue'] == []
    assert 'review_editor_draft' not in at.session_state


def test_native_light_and_dark_themes_use_no_external_fonts():
    config = tomllib.loads((ROOT / '.streamlit' / 'config.toml').read_text())
    theme = config['theme']
    assert theme['base'] == 'light'
    assert 'light' in theme and 'dark' in theme
    assert theme['font'] == 'sans-serif'
    assert theme['light']['backgroundColor'] != theme['dark']['backgroundColor']


def test_settings_demo_actions_preserve_student_work(tmp_path):
    at, path = start_app(tmp_path, seeded=False)
    conn = db.connect(path)
    student_id = db.insert_assessment(conn, {
        'module_code': 'TEST0001', 'title': 'My own essay', 'type': 'essay',
        'due_date': '2026-12-04', 'estimated_hours': 20,
    })
    db.log_progress(conn, student_id, 2, 10, 'Outline')
    conn.close()
    navigate(at, 'Settings')
    at.button(key='side_seed').click().run()
    assert not at.exception
    assert len(snapshot(path)[0]) == 11
    at.button(key='side_clear_demo').click().run()
    assert not at.exception
    rows, logs = snapshot(path)
    assert [r['id'] for r in rows] == [student_id]
    assert len(logs) == 1


def test_settings_reset_requires_confirmation_and_removes_progress(tmp_path):
    at, path = start_app(tmp_path)
    before = snapshot(path)
    navigate(at, 'Settings')
    assert at.button(key='side_reset').disabled
    assert snapshot(path) == before
    at.checkbox(key='confirm_reset').check().run()
    at.button(key='side_reset').click().run()
    assert not at.exception
    assert snapshot(path) == ([], [])
