"""Unit and integration tests for Deadline Radar's Streamlit frontend."""

from datetime import date, timedelta
from pathlib import Path
import sqlite3
import sys

import pytest

# Ensure frontend and sibling worktrees are in sys.path
_CURRENT_DIR = Path(__file__).resolve().parents[1]
if str(_CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(_CURRENT_DIR))

import app


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
    assert "ENGF0001" in modules

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


def test_offline_extraction_fixture():
    """Verify handbook PDF offline extraction returns structured candidates."""
    res = app.extract_from_pdf(b"%PDF-1.4 test", "COMP0004_Algorithms.pdf")
    assert len(res) == 2
    assert res[0]["module_code"] == "COMP0004"
    assert res[0]["confidence"] in app.CONFIDENCES
    assert len(res[0]["source_quote"]) > 0

    # Second item should be low confidence due to ambiguous week reference
    assert res[1]["confidence"] == "low"


def test_normalize_extracted_batch():
    """Verify that unformatted raw extracted items are properly cleaned."""
    raw = [
        {
            "module_code": "comp0002",
            "title": "Coursework 1",
            "type": "COURSEWORK",
            "due_date": "Term 1 Week 5",
            "weighting_pct": "30",
            "estimated_hours": "25",
            "confidence": "HIGH",
            "source_quote": "Due in Week 5",
        }
    ]
    normalized = app._normalize_extracted_batch(raw)
    assert len(normalized) == 1
    item = normalized[0]
    assert item["module_code"] == "comp0002"
    assert item["type"] == "coursework"
    assert item["weighting_pct"] == 30.0
    assert item["estimated_hours"] == 25.0
    assert item["confidence"] in app.CONFIDENCES
    # due_date was resolved to a valid date
    assert len(item["due_date"]) == 10
    assert date.fromisoformat(item["due_date"])


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
    at.sidebar.radio(key="main_nav_radio").set_value("📈 Workload Heatmap").run()
    assert not at.exception

    # Navigate to Upload & Review
    at.sidebar.radio(key="main_nav_radio").set_value("📄 Upload & Review").run()
    assert not at.exception

    # Navigate to Manage Assessments
    at.sidebar.radio(key="main_nav_radio").set_value("✏️ Manage Assessments").run()
    assert not at.exception

    # Navigate to Progress Tracking
    at.sidebar.radio(key="main_nav_radio").set_value("⏱️ Progress Tracking").run()
    assert not at.exception

    # Return to Dashboard
    at.sidebar.radio(key="main_nav_radio").set_value("📊 Dashboard").run()
    assert not at.exception


def test_review_confirmation_pipeline(memory_db):
    """Test extracting handbooks into review queue and confirming to DB."""
    candidates = app.extract_from_pdf(b"%PDF-1.4 sample", "COMP0002_Software_Engineering.pdf")
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


