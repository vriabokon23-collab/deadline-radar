"""The demo seed has to tell a specific story on stage.

These tests pin the properties the A12 rehearsal depends on. If someone retunes
`estimated_hours` or a due date and the narrative quietly stops working, this is
what catches it before a judge does.
"""

from datetime import date

import pytest

import app
from radar import analytics, db
from sample_data import seed


DEMO_TODAY = date.fromisoformat(seed.DEMO_TODAY_ISO)
CRUNCH_MONDAY = date.fromisoformat(seed.CRUNCH_WEEK_MONDAY_ISO)


@pytest.fixture
def seeded(conn):
    seed.seed_demo_data(conn)
    return conn


def analytics_rows(conn):
    return [app.to_analytics_row(row) for row in db.list_assessments(conn)]


def test_seed_date_matches_app_reference_today():
    """The seed is tuned to one date; app.py must open on the same one."""
    assert app.REFERENCE_TODAY == DEMO_TODAY


def test_shape_is_ten_assessments_across_four_modules(seeded):
    rows = db.list_assessments(seeded)
    assert len(rows) == 10
    assert len({row["module_code"] for row in rows}) == 4


def test_exactly_two_at_risk_for_different_reasons(seeded):
    """Both halves of the CLAUDE.md risk rule must be visible, separately."""
    flagged = {
        row["title"]: analytics.assess_risk(row, today=DEMO_TODAY)
        for row in analytics_rows(seeded)
        if analytics.assess_risk(row, today=DEMO_TODAY).at_risk
    }
    assert len(flagged) == 2

    reason_sets = {title: set(result.reasons) for title, result in flagged.items()}
    assert reason_sets == {
        "Problem Set 1 (Graph Proofs)": {
            analytics.REASON_PROGRESS_BEHIND,
            analytics.REASON_INSUFFICIENT_TIME,
        },
        "Interpreter Implementation Project": {
            analytics.REASON_INSUFFICIENT_TIME
        },
    }


def test_submitted_work_is_never_flagged(seeded):
    submitted = [row for row in analytics_rows(seeded) if row["status"] == "submitted"]
    assert submitted
    for row in submitted:
        assert analytics.assess_risk(row, today=DEMO_TODAY).at_risk is False


def test_crunch_week_dominates_the_heatmap(seeded):
    loads = analytics.weekly_workload(analytics_rows(seeded), app.get_all_term_weeks())
    busiest, runner_up = sorted(loads, key=lambda load: -load.remaining_hours)[:2]

    assert busiest.week.start_date == CRUNCH_MONDAY
    assert busiest.assessment_count == 3
    # Comfortably the worst week, so the chart reads at a glance from the back row.
    assert busiest.remaining_hours > 2 * runner_up.remaining_hours


def test_submitted_work_is_absent_from_the_heatmap(seeded):
    loads = analytics.weekly_workload(analytics_rows(seeded), app.get_all_term_weeks())
    week_of_submitted = next(
        load for load in loads if load.week.start_date == date(2026, 10, 5)
    )
    assert week_of_submitted.remaining_hours == 0.0
    assert week_of_submitted.assessment_count == 0


def test_one_low_confidence_row_carries_its_ambiguous_quote(seeded):
    low = [row for row in db.list_assessments(seeded) if row["confidence"] == "low"]
    assert len(low) == 1
    assert low[0]["source_quote"] == "Problem Set 2 is due in Term 1 Week 10."
    assert low[0]["notes"]


def test_progress_history_matches_the_rows_it_belongs_to(seeded):
    """log_progress writes progress_pct_after onto the parent, so they must agree."""
    with_history = 0
    for row in db.list_assessments(seeded):
        history = db.get_progress_history(seeded, row["id"])
        if not history:
            continue
        with_history += 1
        assert history[0]["progress_pct_after"] == pytest.approx(row["progress_pct"])
        assert db.total_hours_spent(seeded, row["id"]) > 0
    assert with_history == len(seed.DEMO_PROGRESS)


def test_no_seed_row_claims_to_be_a_fabricated_extraction(seeded):
    """`fixture` means the offline fallback invented it; demo rows are opt-in."""
    sources = {row["source"] for row in db.list_assessments(seeded)}
    assert db.FABRICATED_SOURCE not in sources


def test_every_seed_row_is_exportable(seeded):
    rows = db.list_assessments(seeded)
    exportable, fabricated = app.export.split_fabricated(rows)
    assert fabricated == []
    assert len(exportable) == len(rows)


def test_reseeding_replaces_rather_than_duplicates(seeded):
    before = db.list_assessments(seeded)
    log_count = seeded.execute("SELECT COUNT(*) FROM progress_log").fetchone()[0]

    seed.seed_demo_data(seeded)

    after = db.list_assessments(seeded)
    assert len(after) == len(before)
    assert [row["title"] for row in after] == [row["title"] for row in before]
    assert seeded.execute("SELECT COUNT(*) FROM progress_log").fetchone()[0] == log_count


def test_seeding_leaves_real_assessments_alone(conn, assessment):
    """A student's own row must survive both seeding and demo cleanup."""
    mine = db.insert_assessment(conn, {**assessment, "title": "My Own Essay"})

    seed.seed_demo_data(conn)
    assert len(db.list_assessments(conn)) == 11

    removed = seed.clear_demo_data(conn)
    assert removed == 10

    survivors = db.list_assessments(conn)
    assert [row["id"] for row in survivors] == [mine]


def test_clearing_demo_data_takes_its_progress_history_with_it(seeded):
    assert seeded.execute("SELECT COUNT(*) FROM progress_log").fetchone()[0] > 0
    seed.clear_demo_data(seeded)
    assert db.list_assessments(seeded) == []
    assert seeded.execute("SELECT COUNT(*) FROM progress_log").fetchone()[0] == 0


def test_cli_seeds_and_clears_a_named_database(tmp_path, capsys):
    db_path = tmp_path / "nested" / "demo.db"

    assert seed.main(["--db", str(db_path)]) == 0
    assert "10 assessments across 4 modules" in capsys.readouterr().out
    conn = db.connect(db_path)
    assert len(db.list_assessments(conn)) == 10
    conn.close()

    assert seed.main(["--db", str(db_path), "--clear"]) == 0
    assert "removed 10 demo assessments" in capsys.readouterr().out
    conn = db.connect(db_path)
    assert db.list_assessments(conn) == []
    conn.close()
