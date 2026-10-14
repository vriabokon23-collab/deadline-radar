"""Unit and integration tests for Deadline Radar's Streamlit frontend."""

from datetime import date, timedelta
from pathlib import Path
import sqlite3
import sys

import pytest

from tests.ui_helpers import navigate

# Ensure frontend and sibling worktrees are in sys.path
_CURRENT_DIR = Path(__file__).resolve().parents[1]
if str(_CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(_CURRENT_DIR))

import app
from radar import extract


@pytest.fixture
def memory_db():
    """Create an isolated in-memory SQLite database initialized with schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    app.db.init_db(conn)
    yield conn
    conn.close()


def test_import_radar_modules():
    """Ensure all core radar backend modules are successfully loaded."""
    assert app.db is not None
    assert app.calendar is not None
    assert app.analytics is not None
    assert app.export is not None


def test_to_analytics_row():
    """Ensure raw SQLite rows with string due_date convert to datetime.date objects."""
    raw = {
        "due_date": "2026-11-20",
        "estimated_hours": 25.0,
        "progress_pct": 50.0,
        "status": "in_progress",
    }
    converted = app.to_analytics_row(raw)
    assert isinstance(converted["due_date"], date)
    assert converted["due_date"] == date(2026, 11, 20)
    assert converted["estimated_hours"] == 25.0
    assert converted["progress_pct"] == 50.0


def test_get_all_term_weeks():
    """Verify that all 30 UCL 2026/27 academic term weeks are generated."""
    weeks = app.get_all_term_weeks()
    assert len(weeks) == 30
    assert weeks[0].term == "T1"
    assert weeks[0].week_number == 1
    assert weeks[0].start_date == date(2026, 9, 28)

    # Term 1 Reading week is week 7 (2026-11-09)
    t1_w7 = next(w for w in weeks if w.term == "T1" and w.week_number == 7)
    assert t1_w7.is_reading_week is True
    assert t1_w7.start_date == date(2026, 11, 9)

    # Term 2 Reading week is week 6 (2027-02-15)
    t2_w6 = next(w for w in weeks if w.term == "T2" and w.week_number == 6)
    assert t2_w6.is_reading_week is True
    assert t2_w6.start_date == date(2027, 2, 15)


def test_seed_demo_data(memory_db):
    """Verify demo seed function populates representative UCL assessments and history."""
    count = app.seed_demo_data(memory_db)
    assert count >= 8

    rows = app.db.list_assessments(memory_db)
    assert len(rows) == count

    # Verify modules and progress history
    modules = {r["module_code"] for r in rows}
    assert "COMP0002" in modules
    assert "COMP0004" in modules
    assert "COMP0012" in modules

    # Check progress log exists
    submitted_item = next(r for r in rows if r["status"] == "submitted")
    hist = app.db.get_progress_history(memory_db, submitted_item["id"])
    assert len(hist) > 0
    assert hist[0]["progress_pct_after"] == 100.0


def test_workload_heatmap_and_crunch(memory_db):
    """Test weekly workload aggregation and crunch week detection."""
    app.seed_demo_data(memory_db)
    rows = [app.to_analytics_row(r) for r in app.db.list_assessments(memory_db)]
    weeks = app.get_all_term_weeks()

    loads = app.analytics.weekly_workload(rows, weeks)
    assert len(loads) == len(weeks)

    # Excluded submitted work
    total_remaining = sum(l.remaining_hours for l in loads)
    assert total_remaining > 0

    # Crunch week in Term 1 Week 10
    t1_w10 = next(l for l in loads if l.week.term == "T1" and l.week.week_number == 10)
    assert t1_w10.remaining_hours >= 20.0


def test_risk_indicators(memory_db):
    """Test risk flags on demo data."""
    app.seed_demo_data(memory_db)
    rows = [app.to_analytics_row(r) for r in app.db.list_assessments(memory_db)]

    risks = [app.analytics.assess_risk(r, today=app.REFERENCE_TODAY) for r in rows]
    at_risk_results = [r for r in risks if r.at_risk]
    assert len(at_risk_results) > 0

    # Check reasons are populated
    for r in at_risk_results:
        assert len(r.reasons) > 0
        assert any(
            reason in [app.analytics.REASON_PROGRESS_BEHIND, app.analytics.REASON_INSUFFICIENT_TIME]
            for reason in r.reasons
        )


def test_calendar_export(memory_db):
    """Verify iCalendar (.ics) generation produced by export module."""
    app.seed_demo_data(memory_db)
    rows = app.db.list_assessments(memory_db)
    ics_bytes = app.export.export_calendar(rows)

    assert isinstance(ics_bytes, bytes)
    assert b"BEGIN:VCALENDAR" in ics_bytes
    assert b"END:VCALENDAR" in ics_bytes
    assert b"COMP0002" in ics_bytes
    assert b"COMP0004" in ics_bytes


def fixture_row(**overrides):
    """A fabricated row shaped like one _generate_fixture_extractions produces."""
    row = {
        "module_code": "COMP9999",
        "module_name": "Invented Module",
        "title": "Fabricated Coursework",
        "type": "coursework",
        "due_date": "2026-12-07",  # T1 week 11
        "estimated_hours": 40.0,
        "source": "fixture",
        "confidence": "low",
        "source_quote": "Invented quote; Claude never read a handbook for this.",
    }
    row.update(overrides)
    return row


def test_fixture_rows_excluded_from_calendar_export(memory_db):
    """A fabricated deadline must never reach the student's real calendar."""
    app.seed_demo_data(memory_db)
    app.db.insert_assessment(memory_db, fixture_row())
    rows = app.db.list_assessments(memory_db)

    exportable, fabricated = app.export.split_fabricated(rows)
    assert len(fabricated) == 1
    assert len(exportable) == len(rows) - 1

    ics_bytes = app.export.export_calendar(exportable)
    assert b"COMP0002" in ics_bytes
    assert b"COMP9999" not in ics_bytes
    assert b"Fabricated Coursework" not in ics_bytes

    # Same outcome when a caller forgets to filter first.
    assert b"COMP9999" not in app.export.export_calendar(rows)


def test_export_split_without_fixture_rows_keeps_everything(memory_db):
    app.seed_demo_data(memory_db)
    rows = app.db.list_assessments(memory_db)
    exportable, fabricated = app.export.split_fabricated(rows)
    assert fabricated == []
    assert len(exportable) == len(rows)


def test_heatmap_separates_fabricated_hours(memory_db):
    """The heatmap keeps fabricated hours in the totals but reports them apart."""
    app.db.insert_assessment(memory_db, fixture_row(estimated_hours=40.0))
    app.db.insert_assessment(memory_db, fixture_row(
        module_code="COMP0002", title="Real Coursework", source="extracted",
        estimated_hours=10.0, source_quote="Submit by 7 December 2026.",
    ))
    rows = [app.to_analytics_row(r) for r in app.db.list_assessments(memory_db)]
    loads = app.analytics.weekly_workload(rows, app.get_all_term_weeks())

    week11 = next(l for l in loads if l.week.term == "T1" and l.week.week_number == 11)
    assert week11.remaining_hours == pytest.approx(50.0)
    assert week11.fabricated_hours == pytest.approx(40.0)
    assert week11.fabricated_count == 1

    # Without the fabricated hours this week would fall under the 20h crunch line.
    assert week11.remaining_hours >= 20.0
    assert week11.remaining_hours - week11.fabricated_hours < 20.0
    assert sum(l.fabricated_hours for l in loads) == pytest.approx(40.0)


def test_extraction_result_reports_fixture_fallback(monkeypatch):
    """Fixture replay must be reported to the caller, not silently returned."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DEADLINE_RADAR_FIXTURES", raising=False)

    result = app.extract_from_pdf(b"%PDF-1.4 test", "COMP0004_Algorithms.pdf")
    assert result.used_fixtures is True
    assert result.source == "fixture"
    assert "ANTHROPIC_API_KEY" in result.reason

    monkeypatch.setenv("DEADLINE_RADAR_FIXTURES", "1")
    forced = app.extract_from_pdf(b"%PDF-1.4 test", "COMP0004_Algorithms.pdf")
    assert forced.used_fixtures is True
    assert "DEADLINE_RADAR_FIXTURES" in forced.reason


def test_manual_crud_and_progress_logging(memory_db):
    """Test manual creation, editing, deleting and progress logging."""
    # 1. Insert manual assessment
    new_data = {
        "module_code": "COMP0099",
        "module_name": "Distributed Systems",
        "title": "Raft Consensus Lab",
        "type": "coursework",
        "due_date": "2026-11-27",
        "due_time": "16:00:00",
        "weighting_pct": 50.0,
        "word_count": None,
        "estimated_hours": 30.0,
        "status": "not_started",
        "progress_pct": 0.0,
        "source": "manual",
        "confidence": "high",
        "source_quote": "Manual entry",
        "notes": "Leader election implementation",
    }
    a_id = app.db.insert_assessment(memory_db, new_data)
    assert a_id > 0

    fetched = app.db.get_assessment(memory_db, a_id)
    assert fetched["module_code"] == "COMP0099"
    assert fetched["progress_pct"] == 0.0

    # 2. Log progress
    log_id = app.db.log_progress(
        memory_db,
        assessment_id=a_id,
        hours_spent=5.5,
        progress_pct_after=25.0,
        note="Implemented RPC handlers",
    )
    assert log_id > 0

    updated = app.db.get_assessment(memory_db, a_id)
    assert updated["progress_pct"] == 25.0
    assert app.db.total_hours_spent(memory_db, a_id) == 5.5

    # 3. Update assessment details
    app.db.update_assessment(memory_db, a_id, {"title": "Raft Consensus Lab (Part 1)"})
    after_update = app.db.get_assessment(memory_db, a_id)
    assert after_update["title"] == "Raft Consensus Lab (Part 1)"

    # 4. Delete assessment
    deleted = app.db.delete_assessment(memory_db, a_id)
    assert deleted is True
    assert app.db.get_assessment(memory_db, a_id) is None
    # Progress logs should be cascade deleted
    hist = app.db.get_progress_history(memory_db, a_id)
    assert len(hist) == 0


def test_streamlit_app_navigation_and_rendering(tmp_path):
    """End-to-end simulation of Streamlit UI navigation and rendering."""
    from streamlit.testing.v1 import AppTest

    # Use isolated test database file
    db_file = tmp_path / "test_radar.db"
    conn = app.db.connect(db_file)
    app.seed_demo_data(conn)
    conn.close()

    at = AppTest.from_file(str(_CURRENT_DIR / "app.py"), default_timeout=15)
    # Inject db_path into session state
    at.session_state["db_path"] = str(db_file)
    at.run()
    assert not at.exception

    # Navigate to Workload Heatmap
    navigate(at, "📈 Workload Heatmap")
    assert not at.exception

    # Navigate to Upload & Review
    navigate(at, "📄 Upload & Review")
    assert not at.exception

    # Navigate to Manage Assessments
    navigate(at, "✏️ Manage Assessments")
    assert not at.exception

    # Navigate to Progress Tracking
    navigate(at, "⏱️ Progress Tracking")
    assert not at.exception

    # Return to Dashboard
    navigate(at, "📊 Dashboard")
    assert not at.exception


def test_fixture_fallback_notice_survives_rerun(tmp_path, monkeypatch):
    """Uploading with no API key must visibly warn that the rows are fabricated.

    Regression: the notice was rendered in the same pass as st.rerun(), which
    discards it, so a misconfigured demo showed invented deadlines silently.
    """
    from streamlit.testing.v1 import AppTest

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    db_file = tmp_path / "test_radar.db"
    app.db.connect(db_file).close()

    at = AppTest.from_file(str(_CURRENT_DIR / "app.py"), default_timeout=30)
    at.session_state["db_path"] = str(db_file)
    at.run()
    navigate(at, "📄 Upload & Review")
    assert not at.exception

    at.file_uploader[0].set_value(("COMP0004_Algorithms.pdf", b"%PDF-1.4 fake", "application/pdf"))
    at.run()
    extract_btn = next(b for b in at.button if "Extract deadlines" in b.label)
    extract_btn.click().run()
    assert not at.exception

    # Rows reached the review gate...
    assert len(at.session_state["review_queue"]) == 2
    # ...and the fabrication notice is actually on screen alongside them.
    assert any("fabricated" in w.value for w in at.warning)
    assert any("Extracted 2 candidate assessments" in s.value for s in at.success)
    assert any("Fixture data in this queue" in e.value for e in at.warning)

    # One-shot banners drain; the queue badge persists while the rows do.
    at.run()
    assert not any("not a real extraction" in w.value for w in at.warning)
    assert any("Fixture data in this queue" in e.value for e in at.warning)


def test_extraction_result_stamps_row_provenance(monkeypatch):
    """Every row must carry its own provenance, not just the enclosing result."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DEADLINE_RADAR_FIXTURES", raising=False)

    fixture = app.extract_from_pdf(b"%PDF-1.4 test", "COMP0004_Algorithms.pdf")
    assert fixture.used_fixtures is True
    assert [r["source"] for r in fixture.assessments] == ["fixture"] * len(fixture.assessments)

    # A genuine API result is stamped 'extracted' through the same single path.
    api = app.ExtractionResult(extract.validate_batch([{
        "module_code": "COMP0002",
        "title": "Coursework 1",
        "type": "coursework",
        "due_date": "2026-11-16",
        "confidence": "high",
        "source_quote": "Due 16 November.",
    }]), "api")
    assert api.used_fixtures is False
    assert [r["source"] for r in api.assessments] == ["extracted"]


def test_confirmed_fixture_rows_stay_labelled_in_database(tmp_path, monkeypatch):
    """Fabricated rows must remain distinguishable after Confirm & save.

    Regression: the Confirm handler hardcoded source='extracted' for every row,
    so a demo with no ANTHROPIC_API_KEY wrote invented deadlines into the
    database labelled exactly like genuine Claude extractions, and the review
    gate's fixture badge was cleared on save leaving no trace.
    """
    from streamlit.testing.v1 import AppTest

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DEADLINE_RADAR_FIXTURES", raising=False)
    db_file = tmp_path / "test_radar.db"
    app.db.connect(db_file).close()

    at = AppTest.from_file(str(_CURRENT_DIR / "app.py"), default_timeout=30)
    at.session_state["db_path"] = str(db_file)
    at.run()
    navigate(at, "📄 Upload & Review")

    at.file_uploader[0].set_value(("COMP0004_Algorithms.pdf", b"%PDF-1.4 fake", "application/pdf"))
    at.run()
    next(b for b in at.button if "Extract deadlines" in b.label).click().run()
    assert not at.exception
    assert len(at.session_state["review_queue"]) == 2

    next(b for b in at.button if "Confirm & save" in b.label).click().run()
    assert not at.exception
    assert at.session_state["review_queue"] == []

    # The rows landed, and they are still identifiable as fabricated.
    conn = app.db.connect(db_file)
    try:
        persisted = app.db.list_assessments(conn)
        assert len(persisted) == 2
        assert {p["source"] for p in persisted} == {"fixture"}
        assert all(p["source"] != "extracted" for p in persisted)
    finally:
        conn.close()

    # Saving warned that fabricated data was committed...
    assert any("fixture data" in w.value for w in at.warning)
    # ...and the dashboard flags them rather than treating them as real.
    navigate(at, "📊 Dashboard")
    assert not at.exception
    assert any("fabricated fixture data" in e.value for e in at.warning)


def test_confirmed_api_rows_are_labelled_extracted(tmp_path, monkeypatch):
    """The fix must not relabel genuine extractions: real rows stay 'extracted'."""
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-used")
    db_file = tmp_path / "test_radar.db"
    app.db.connect(db_file).close()

    genuine = extract.validate_batch([{
        "module_code": "COMP0002",
        "module_name": "Software Engineering",
        "title": "Coursework 1",
        "type": "coursework",
        "due_date": "2026-11-16",
        "due_time": "16:00:00",
        "weighting_pct": 30,
        "estimated_hours": 25,
        "confidence": "high",
        "source_quote": "Coursework 1 must be submitted by Monday 16 November.",
    }])

    at = AppTest.from_file(str(_CURRENT_DIR / "app.py"), default_timeout=30)
    at.session_state["db_path"] = str(db_file)
    at.run()
    navigate(at, "📄 Upload & Review")
    # Seed the gate the way a successful API extraction would.
    at.session_state["review_queue"] = app.ExtractionResult(genuine, "api").assessments
    at.run()

    next(b for b in at.button if "Confirm & save" in b.label).click().run()
    assert not at.exception

    conn = app.db.connect(db_file)
    try:
        persisted = app.db.list_assessments(conn)
        assert [p["source"] for p in persisted] == ["extracted"]
    finally:
        conn.close()

    navigate(at, "📊 Dashboard")
    assert not any("fabricated fixture data" in e.value for e in at.warning)


def test_saved_fixture_rows_are_flagged_across_views(tmp_path):
    """A fixture row in the database must be identifiable wherever it appears."""
    from streamlit.testing.v1 import AppTest

    db_file = tmp_path / "test_radar.db"
    conn = app.db.connect(db_file)
    app.db.insert_assessment(conn, {
        "module_code": "COMP0004", "module_name": "Algorithms",
        "title": "Problem Set 1", "type": "problem_set", "due_date": "2026-10-30",
        "estimated_hours": 15.0, "status": "not_started", "progress_pct": 0.0,
        "source": "fixture", "confidence": "high",
        "source_quote": "Invented: due Friday 30 October 2026.",
    })
    conn.close()

    at = AppTest.from_file(str(_CURRENT_DIR / "app.py"), default_timeout=60)
    at.session_state["db_path"] = str(db_file)
    at.run()

    # Every view still renders with the new provenance value present.
    for view in ["📊 Dashboard", "📈 Workload Heatmap", "📄 Upload & Review",
                 "✏️ Manage Assessments", "⏱️ Progress Tracking"]:
        navigate(at, view)
        assert not at.exception, f"{view} raised"

    navigate(at, "📊 Dashboard")
    assert any("fabricated fixture data" in e.value for e in at.warning)
    assert any("FIXTURE" in m.value for m in at.caption)
    # The quote a student would check to verify the date is itself invented.
    assert any("fabricated fixture text" in e.value for e in at.caption)

    # Deleting them requires finding them in the management selectors.
    navigate(at, "✏️ Manage Assessments")
    assert any("FIXTURE" in opt for sb in at.selectbox for opt in (sb.options or []))


@pytest.mark.parametrize("raw,expected", [
    ("fixture", "fixture"),
    ("extracted", "extracted"),
    ("manual", "manual"),
    ("  fixture  ", "fixture"),
    # Rows appended in the data editor carry no provenance at all.
    (None, "manual"),
    (float("nan"), "manual"),
    ("", "manual"),
    # An unrecognised label must never be promoted to a trustworthy one.
    ("api", "manual"),
    ("EXTRACTED", "manual"),
])
def test_resolve_row_source(raw, expected):
    """Confirm-time provenance resolution: trust the row, never invent trust."""
    assert app._resolve_row_source(raw) == expected


def test_rows_typed_into_review_editor_are_manual(memory_db):
    """A row added in the editor has no provenance and saves as the student's own."""
    resolved = app._resolve_row_source(float("nan"))
    # 'manual' is the one source that may be saved without a quote.
    saved = app.db.insert_assessment(memory_db, {
        "module_code": "COMP0099", "title": "Self-entered", "type": "essay",
        "due_date": "2026-11-20", "estimated_hours": 5.0, "source": resolved,
        "confidence": "high", "source_quote": "",
    })
    assert app.db.get_assessment(memory_db, saved)["source"] == "manual"


def test_review_confirmation_pipeline(memory_db):
    """Test extracting handbooks into review queue and confirming to DB."""
    candidates = app.extract_from_pdf(b"%PDF-1.4 sample", "COMP0002_Software_Engineering.pdf").assessments
    assert len(candidates) >= 2

    # Verify review gate holds: nothing in database yet
    assert len(app.db.list_assessments(memory_db)) == 0

    # User confirms the batch
    to_save = []
    for c in candidates:
        to_save.append({
            "module_code": c["module_code"],
            "module_name": c["module_name"],
            "title": c["title"],
            "type": c["type"],
            "due_date": c["due_date"],
            "due_time": c["due_time"],
            "weighting_pct": c["weighting_pct"],
            "word_count": c["word_count"],
            "estimated_hours": c["estimated_hours"],
            "status": "not_started",
            "progress_pct": 0.0,
            "source": "extracted",
            "confidence": c["confidence"],
            "source_quote": c["source_quote"],
            "notes": c["notes"],
        })

    saved_ids = app.db.insert_assessments(memory_db, to_save)
    assert len(saved_ids) == len(candidates)

    persisted = app.db.list_assessments(memory_db)
    assert len(persisted) == len(candidates)
    for p in persisted:
        assert p["source"] == "extracted"
        assert p["progress_pct"] == 0.0
        assert p["status"] == "not_started"


def test_parse_date_helper():
    """Test date parsing handles dates, strings, nulls, and malformed values."""
    assert app.parse_date(date(2026, 11, 20)) == date(2026, 11, 20)
    assert app.parse_date("2026-11-20") == date(2026, 11, 20)
    assert app.parse_date("2026-11-20 16:00:00") == date(2026, 11, 20)
    assert app.parse_date(None) is None
    assert app.parse_date("") is None
    assert app.parse_date("invalid-date-string") is None


def test_progress_completion_submitted_workflow(memory_db):
    """Test updating progress to 100% and explicitly confirming submitted status."""
    data = {
        "module_code": "COMP0010",
        "module_name": "Programming",
        "title": "Lab 1",
        "type": "coursework",
        "due_date": "2026-11-05",
        "estimated_hours": 10.0,
        "status": "not_started",
        "progress_pct": 0.0,
        "source": "manual",
        "confidence": "high",
    }
    a_id = app.db.insert_assessment(memory_db, data)

    # 1. Progress to 100% but user does NOT mark submitted
    app.db.log_progress(memory_db, a_id, hours_spent=10.0, progress_pct_after=100.0, note="Done")
    rec = app.db.get_assessment(memory_db, a_id)
    assert rec["progress_pct"] == 100.0
    # Status should still be not_started (never infer submitted silently)
    assert rec["status"] == "not_started"

    # 2. User explicitly confirms submission
    app.db.update_assessment(memory_db, a_id, {"status": "submitted"})
    submitted_rec = app.db.get_assessment(memory_db, a_id)
    assert submitted_rec["status"] == "submitted"



@pytest.mark.parametrize('changes', [{'due_time': '25:00'}, {'word_count': 2.5},
                                     {'estimated_hours': -1}])
def test_invalid_review_keeps_queue_and_database_unchanged(tmp_path, changes):
    from streamlit.testing.v1 import AppTest
    db_file = tmp_path / 'review.db'
    at = AppTest.from_file(str(_CURRENT_DIR / 'app.py'), default_timeout=15)
    at.session_state['db_path'] = str(db_file)
    at.session_state['review_queue'] = [{
        'module_code': 'TEST0001', 'title': 'Essay', 'type': 'essay',
        'due_date': '2026-12-04', 'source': 'manual', 'estimated_hours': 20,
        **changes,
    }]
    at.run()
    navigate(at, '📄 Upload & Review')
    next(b for b in at.button if 'Confirm & save' in b.label).click().run()
    assert not at.exception
    assert at.error
    assert len(at.session_state['review_queue']) == 1
    conn = app.db.connect(db_file)
    try:
        assert app.db.list_assessments(conn) == []
    finally:
        conn.close()


def test_progress_picker_preserves_duplicates_and_submission_is_explicit(tmp_path):
    from streamlit.testing.v1 import AppTest
    db_file = tmp_path / 'progress.db'
    conn = app.db.connect(db_file)
    data = {'module_code': 'TEST0001', 'title': 'Essay', 'type': 'essay',
            'due_date': '2026-12-04', 'estimated_hours': 20}
    ids = [app.db.insert_assessment(conn, data) for _ in range(2)]
    conn.close()
    at = AppTest.from_file(str(_CURRENT_DIR / 'app.py'), default_timeout=15)
    at.session_state['db_path'] = str(db_file)
    at.run()
    navigate(at, 'Settings')
    assert at.button(key='side_reset').disabled
    navigate(at, '⏱️ Progress Tracking')
    assert len(at.selectbox[0].options) == 2
    at.slider[0].set_value(100)
    next(c for c in at.checkbox if 'Submitted' in c.label).check()
    next(b for b in at.button if 'Record progress' in b.label).click().run()
    assert not at.exception
    conn = app.db.connect(db_file)
    try:
        assert app.db.get_assessment(conn, ids[0])['status'] == 'submitted'
        assert app.db.get_assessment(conn, ids[1])['progress_pct'] == 0
    finally:
        conn.close()


def test_plan_is_invalidated_when_progress_changes(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv('DEADLINE_RADAR_FIXTURES', '1')
    db_file = tmp_path / 'plan.db'
    conn = app.db.connect(db_file)
    app.seed_demo_data(conn)
    target = app.db.list_assessments(conn)[0]['id']
    conn.close()
    at = AppTest.from_file(str(_CURRENT_DIR / 'app.py'), default_timeout=15)
    at.session_state['db_path'] = str(db_file)
    at.run()
    navigate(at, '🗓️ Plan My Week')
    at.button(key='plan_run').click().run()
    assert at.session_state['week_plan'] is not None
    conn = app.db.connect(db_file)
    app.db.log_progress(conn, target, 1, 40)
    conn.close()
    at.run()
    assert not at.exception
    assert at.session_state['week_plan'] is None
