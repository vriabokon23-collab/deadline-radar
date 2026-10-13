"""Deadline Radar - Streamlit Frontend.

UCL assessment tracker built for the Claude Campus Hackathon.
Surfaces crunch weeks, tracks progress, and flags assessments at risk of slipping.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any, Mapping

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ---------------------------------------------------------------------------
# Module import resolution
# Ensure radar modules are discoverable locally, post-merge, or in worktrees
# ---------------------------------------------------------------------------
_CURRENT_DIR = Path(__file__).resolve().parent
if str(_CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(_CURRENT_DIR))

# Sibling worktree resolution during multi-worktree hackathon development
for _sibling_name in ("deadline-radar", "radar-backend", "radar-integration"):
    _sibling_dir = _CURRENT_DIR.parent / _sibling_name
    if _sibling_dir.is_dir() and str(_sibling_dir) not in sys.path:
        sys.path.append(str(_sibling_dir))

try:
    import radar
    for _sibling_name in ("deadline-radar", "radar-backend", "radar-integration"):
        _s_radar = _CURRENT_DIR.parent / _sibling_name / "radar"
        if _s_radar.is_dir() and str(_s_radar) not in radar.__path__:
            radar.__path__.append(str(_s_radar))
except Exception:
    pass

import radar.db as db
import radar.calendar as calendar
import radar.analytics as analytics
import radar.export as export
import radar.extract as extract

# Re-exported so the upload flow and its tests can keep calling these unqualified;
# the implementation lives in radar/extract.py, never in a Streamlit callback.
from radar.extract import ExtractionResult, extract_from_pdf

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Constants & Configurations
# ---------------------------------------------------------------------------
DEFAULT_DB_FILE = Path(os.environ.get("RADAR_DB_PATH", _CURRENT_DIR / "data" / "radar.db"))
REFERENCE_TODAY = date(2026, 10, 12)  # Hackathon week anchor

ASSESSMENT_TYPES = list(db.ASSESSMENT_TYPES)
STATUSES = list(db.STATUSES)
CONFIDENCES = list(db.CONFIDENCES)
SOURCES = list(db.SOURCES)


def get_db_path() -> Path:
    """Return active database path stored in session state or default."""
    if "db_path" not in st.session_state:
        st.session_state.db_path = str(DEFAULT_DB_FILE)
    path = Path(st.session_state.db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def get_connection() -> sqlite3.Connection:
    """Connect to SQLite database with schema initialized."""
    return db.connect(get_db_path())


def parse_date(val: Any) -> date | None:
    """Safely convert string or date to datetime.date."""
    if val is None or val == "" or pd.isna(val):
        return None
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        try:
            return date.fromisoformat(val.strip()[:10])
        except ValueError:
            return None
    return None


def to_analytics_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Adapt SQLite row to analytics expected format (due_date as date object)."""
    d = dict(row)
    parsed = parse_date(d.get("due_date"))
    d["due_date"] = parsed or REFERENCE_TODAY
    d["estimated_hours"] = float(d.get("estimated_hours") or 0.0)
    d["progress_pct"] = float(d.get("progress_pct") or 0.0)
    d["status"] = str(d.get("status") or "not_started")
    return d


def get_all_term_weeks() -> list[analytics.TermWeek]:
    """Build all UCL term weeks across Term 1, 2, and 3 using calendar.py."""
    weeks: list[analytics.TermWeek] = []
    for term_num, _ in sorted(calendar.TERMS.items()):
        mondays = calendar.term_weeks(term_num)
        for idx, monday in enumerate(mondays, start=1):
            weeks.append(
                analytics.TermWeek(
                    term=f"T{term_num}",
                    week_number=idx,
                    start_date=monday,
                    is_reading_week=calendar.is_reading_week(monday),
                )
            )
    return sorted(weeks, key=lambda w: w.start_date)


# ---------------------------------------------------------------------------
# Demo Seed Data Generator
# ---------------------------------------------------------------------------
DEMO_ASSESSMENTS = [
    {
        "module_code": "COMP0004",
        "module_name": "Algorithms & Data Structures",
        "title": "Problem Set 1 (Graph Proofs)",
        "type": "problem_set",
        "due_date": "2026-10-23",
        "due_time": "16:00:00",
        "weighting_pct": 20.0,
        "word_count": None,
        "estimated_hours": 25.0,
        "status": "in_progress",
        "progress_pct": 5.0,
        "source": "extracted",
        "confidence": "high",
        "source_quote": "Problem Set 1 is due on Friday 23 October 2026 at 4pm.",
        "notes": "Proof-heavy assignment; behind schedule.",
    },
    {
        "module_code": "COMP0008",
        "module_name": "Formal Languages",
        "title": "Automata & Grammars Midterm",
        "type": "coursework",
        "due_date": "2026-10-19",
        "due_time": "16:00:00",
        "weighting_pct": 25.0,
        "word_count": 1500,
        "estimated_hours": 15.0,
        "status": "in_progress",
        "progress_pct": 10.0,
        "source": "manual",
        "confidence": "high",
        "source_quote": "Manual entry from module moodle",
        "notes": "Due next week; insufficient time remaining.",
    },
    {
        "module_code": "COMP0002",
        "module_name": "Software Engineering",
        "title": "Coursework 1: Architecture Specification",
        "type": "coursework",
        "due_date": "2026-11-16",
        "due_time": "16:00:00",
        "weighting_pct": 30.0,
        "word_count": 2500,
        "estimated_hours": 25.0,
        "status": "in_progress",
        "progress_pct": 60.0,
        "source": "extracted",
        "confidence": "high",
        "source_quote": "Coursework 1 must be submitted by Monday 16 November at 16:00.",
        "notes": "System design and UML diagrams.",
    },
    {
        "module_code": "COMP0002",
        "module_name": "Software Engineering",
        "title": "Final Team Project & Peer Review",
        "type": "group_project",
        "due_date": "2026-12-14",
        "due_time": "17:00:00",
        "weighting_pct": 70.0,
        "word_count": 5000,
        "estimated_hours": 50.0,
        "status": "in_progress",
        "progress_pct": 20.0,
        "source": "extracted",
        "confidence": "high",
        "source_quote": "Final group deliverables due on the last week of Term 1, Monday 14 December.",
        "notes": "Web deployment and documentation.",
    },
    {
        "module_code": "COMP0004",
        "module_name": "Algorithms & Data Structures",
        "title": "Problem Set 2 (Dynamic Programming)",
        "type": "problem_set",
        "due_date": "2026-11-30",
        "due_time": "16:00:00",
        "weighting_pct": 15.0,
        "word_count": None,
        "estimated_hours": 20.0,
        "status": "not_started",
        "progress_pct": 0.0,
        "source": "extracted",
        "confidence": "low",
        "source_quote": "Problem Set 2 is due in Term 1 Week 10 (teaching schedule).",
        "notes": "Ambiguous week reference in handbook; check reading week offset.",
    },
    {
        "module_code": "COMP0010",
        "module_name": "Principles of Programming",
        "title": "Interpreter Implementation Project",
        "type": "coursework",
        "due_date": "2026-11-30",
        "due_time": "12:00:00",
        "weighting_pct": 40.0,
        "word_count": 3000,
        "estimated_hours": 35.0,
        "status": "not_started",
        "progress_pct": 0.0,
        "source": "manual",
        "confidence": "high",
        "source_quote": "Manual entry from syllabus",
        "notes": "Build abstract syntax tree and bytecode interpreter.",
    },
    {
        "module_code": "ENGF0001",
        "module_name": "Engineering Challenges",
        "title": "Team Technical Report 1",
        "type": "coursework",
        "due_date": "2026-10-09",
        "due_time": "16:00:00",
        "weighting_pct": 30.0,
        "word_count": 2000,
        "estimated_hours": 18.0,
        "status": "submitted",
        "progress_pct": 100.0,
        "source": "extracted",
        "confidence": "high",
        "source_quote": "Submission deadline: Friday 9 October 2026, 16:00.",
        "notes": "Submitted ahead of deadline.",
    },
    {
        "module_code": "COMP0012",
        "module_name": "Operating Systems",
        "title": "Concurrency Kernel Lab",
        "type": "coursework",
        "due_date": "2027-02-05",
        "due_time": "16:00:00",
        "weighting_pct": 35.0,
        "word_count": None,
        "estimated_hours": 25.0,
        "status": "not_started",
        "progress_pct": 0.0,
        "source": "manual",
        "confidence": "high",
        "source_quote": "Term 2 Syllabus",
        "notes": "Thread synchronization primitives.",
    },
    {
        "module_code": "COMP0014",
        "module_name": "Computer Architecture",
        "title": "Final Written Examination",
        "type": "exam",
        "due_date": "2027-05-14",
        "due_time": "10:00:00",
        "weighting_pct": 80.0,
        "word_count": None,
        "estimated_hours": 45.0,
        "status": "not_started",
        "progress_pct": 0.0,
        "source": "manual",
        "confidence": "high",
        "source_quote": "UCL Central Exam Timetable",
        "notes": "Term 3 exam hall paper.",
    },
]


def seed_demo_data(conn: sqlite3.Connection) -> int:
    """Populate database with representative UCL data exhibiting crunch and risk."""
    conn.execute("DELETE FROM progress_log")
    conn.execute("DELETE FROM assessments")
    ids = db.insert_assessments(conn, DEMO_ASSESSMENTS)

    # Key-based lookup to avoid index mismatch
    all_rows = db.list_assessments(conn)
    by_key = {(r["module_code"], r["title"]): r["id"] for r in all_rows}

    if ("COMP0004", "Problem Set 1 (Graph Proofs)") in by_key:
        p1_id = by_key[("COMP0004", "Problem Set 1 (Graph Proofs)")]
        db.log_progress(conn, p1_id, hours_spent=1.5, progress_pct_after=5.0, note="Solved Question 1 part a")

    if ("COMP0008", "Automata & Grammars Midterm") in by_key:
        p8_id = by_key[("COMP0008", "Automata & Grammars Midterm")]
        db.log_progress(conn, p8_id, hours_spent=1.5, progress_pct_after=10.0, note="Reviewed DFA minimization")

    if ("COMP0002", "Coursework 1: Architecture Specification") in by_key:
        se1_id = by_key[("COMP0002", "Coursework 1: Architecture Specification")]
        db.log_progress(conn, se1_id, hours_spent=5.0, progress_pct_after=30.0, note="Drafted system context diagrams")
        db.log_progress(conn, se1_id, hours_spent=8.0, progress_pct_after=60.0, note="Wrote detailed component architecture")

    if ("COMP0002", "Final Team Project & Peer Review") in by_key:
        se2_id = by_key[("COMP0002", "Final Team Project & Peer Review")]
        db.log_progress(conn, se2_id, hours_spent=10.0, progress_pct_after=20.0, note="Initial GitHub repo setup & scaffold")

    if ("ENGF0001", "Team Technical Report 1") in by_key:
        eng_id = by_key[("ENGF0001", "Team Technical Report 1")]
        db.log_progress(conn, eng_id, hours_spent=6.0, progress_pct_after=40.0, note="First draft compiled")
        db.log_progress(conn, eng_id, hours_spent=12.0, progress_pct_after=100.0, note="Final proofreading and Moodle submission")

    return len(ids)


# ---------------------------------------------------------------------------
# UI View 1: Dashboard
# ---------------------------------------------------------------------------
def render_dashboard(conn: sqlite3.Connection, today: date) -> None:
    st.header("🎯 Deadline Radar Dashboard")
    st.caption(f"Tracking upcoming deadlines against active reference date: **{today.strftime('%A, %d %B %Y')}**")

    assessments = db.list_assessments(conn)

    if not assessments:
        st.info("No assessments found in your radar yet. Upload module handbooks or load sample data to get started!")
        col1, col2 = st.columns([1, 4])
        with col1:
            if st.button("🌱 Load Demo UCL Data", key="dash_seed_btn"):
                count = seed_demo_data(conn)
                st.success(f"Loaded {count} demo assessments with progress history!")
                st.rerun()
        return

    # Compute risk and countdown metrics
    total_count = len(assessments)
    submitted_count = sum(1 for a in assessments if a["status"] == "submitted")
    next_14_days_count = 0
    at_risk_count = 0
    overdue_count = 0

    enriched = []
    for a in assessments:
        ar = to_analytics_row(a)
        due_d = ar["due_date"]
        days_left = (due_d - today).days
        risk_res = analytics.assess_risk(ar, today=today)

        if a["status"] != "submitted":
            if 0 <= days_left <= 14:
                next_14_days_count += 1
            if days_left < 0:
                overdue_count += 1
            if risk_res.at_risk:
                at_risk_count += 1

        enriched.append({
            "raw": a,
            "analytics_row": ar,
            "due_date": due_d,
            "days_left": days_left,
            "risk": risk_res,
            "remaining_hours": analytics.remaining_hours(ar),
        })

    # Fabricated rows look exactly like real ones once saved, so say so up front.
    fixture_rows = [a for a in assessments if a.get("source") == "fixture"]
    if fixture_rows:
        modules = ", ".join(sorted({f"{a['module_code']} {a['title']}" for a in fixture_rows}))
        st.error(
            f"🧪 **{len(fixture_rows)} tracked assessment(s) are fabricated fixture data, "
            "not real extractions.** Claude never read a handbook for these, so their dates, "
            f"weightings and quotes are invented: {modules}. Delete them under "
            "**Manage Assessments** once `ANTHROPIC_API_KEY` is configured."
        )

    # Risk is scored the same way whatever a row's provenance, so say when a flag
    # rests on invented estimated_hours rather than hiding the row from the count.
    fixture_at_risk = sum(
        1 for item in enriched
        if item["raw"].get("source") == "fixture"
        and item["risk"].at_risk
        and item["raw"]["status"] != "submitted"
    )
    risk_help = (
        f"{fixture_at_risk} of these flag(s) belong to fabricated fixture rows, scored "
        "from invented hours and dates." if fixture_at_risk else None
    )

    # Top KPI Metrics Cards
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total Tracked", f"{total_count} items")
    m2.metric("At Risk ⚠️", f"{at_risk_count} items", delta=-at_risk_count if at_risk_count else None, delta_color="inverse", help=risk_help)
    m3.metric("Due in 14 Days", f"{next_14_days_count} items")
    m4.metric("Overdue 🚨", f"{overdue_count} items")
    m5.metric("Submitted ✅", f"{submitted_count} items")

    st.divider()

    # Filter & Sort Controls
    f_col1, f_col2, f_col3, f_col4 = st.columns([2, 2, 2, 2])
    with f_col1:
        modules = sorted({a["module_code"] for a in assessments})
        selected_modules = st.multiselect("Filter by Module", options=modules, default=modules, key="dash_filter_modules")
    with f_col2:
        selected_statuses = st.multiselect("Filter by Status", options=STATUSES, default=["not_started", "in_progress"], key="dash_filter_statuses")
    with f_col3:
        only_risk = st.checkbox("Show At-Risk Only ⚠️", value=False, key="dash_filter_risk_only")
    with f_col4:
        sort_by = st.selectbox("Sort By", options=["Due Date (Earliest first)", "Risk First", "Estimated Hours (Highest)"], key="dash_sort_by")

    # Apply filters
    filtered = [
        item for item in enriched
        if item["raw"]["module_code"] in selected_modules
        and item["raw"]["status"] in selected_statuses
    ]

    if only_risk:
        filtered = [item for item in filtered if item["risk"].at_risk]

    # Apply sorting
    if sort_by == "Due Date (Earliest first)":
        filtered.sort(key=lambda x: (x["due_date"], x["raw"].get("due_time") or "00:00:00"))
    elif sort_by == "Risk First":
        filtered.sort(key=lambda x: (not x["risk"].at_risk, x["due_date"]))
    elif sort_by == "Estimated Hours (Highest)":
        filtered.sort(key=lambda x: x["remaining_hours"], reverse=True)

    st.subheader(f"Upcoming Deadlines ({len(filtered)} displayed)")

    if not filtered:
        st.write("No deadlines match your active filters.")
        return

    # Render assessment cards
    for item in filtered:
        a = item["raw"]
        risk = item["risk"]
        days_left = item["days_left"]
        due_d = item["due_date"]
        rem_hours = item["remaining_hours"]

        # Card container
        with st.container(border=True):
            head_col, status_col = st.columns([4, 2])
            with head_col:
                mod_title = f"**{a['module_code']}** — {a['title']}"
                if a.get("module_name"):
                    mod_title += f" *({a['module_name']})*"
                if a.get("source") == "fixture":
                    mod_title += " `🧪 FIXTURE`"
                st.markdown(f"### {mod_title}")
                st.caption(
                    f"Type: **{a['type'].replace('_', ' ').title()}** | "
                    f"Weighting: **{a['weighting_pct'] if a['weighting_pct'] is not None else 'N/A'}%** | "
                    f"Word count: **{a['word_count'] or 'N/A'}** | "
                    f"Est. hours: **{a['estimated_hours']}h** (Remaining: **{rem_hours:.1f}h**)"
                )
            with status_col:
                # Countdown badge logic
                if a["status"] == "submitted":
                    st.success("✅ Submitted")
                elif days_left < 0:
                    st.error(f"🚨 Overdue by {abs(days_left)} days\n({due_d.strftime('%d %b %Y')})")
                elif days_left == 0:
                    time_str = f" at {a['due_time']}" if a.get("due_time") else ""
                    st.warning(f"⚠️ Due Today{time_str}!")
                else:
                    time_str = f" at {a['due_time']}" if a.get("due_time") else ""
                    st.info(f"⏳ Due in **{days_left} days**\n({due_d.strftime('%a, %d %b')}{time_str})")

            # Risk warning banner
            if risk.at_risk and a["status"] != "submitted":
                reason_msgs = []
                if analytics.REASON_PROGRESS_BEHIND in risk.reasons:
                    exp_prog = analytics.expected_progress_pct(
                        today,
                        analytics.default_start_date(due_d, a["estimated_hours"]),
                        due_d
                    )
                    reason_msgs.append(
                        f"Actual progress ({a['progress_pct']:.0f}%) trails expected progress "
                        f"({exp_prog:.0f}%) by more than 20 percentage points."
                    )
                if analytics.REASON_INSUFFICIENT_TIME in risk.reasons:
                    weeks_left = max(0.1, days_left / 7.0)
                    reason_msgs.append(
                        f"{rem_hours:.1f} hours remaining exceeds capacity "
                        f"({weeks_left:.1f} weeks left at 10h/week safe study rate)."
                    )
                st.error(f"⚠️ **AT RISK OF SLIPPING**: {' '.join(reason_msgs)}")

            # Progress bar
            prog_col, action_col = st.columns([4, 2])
            with prog_col:
                st.progress(
                    int(a["progress_pct"]) / 100.0,
                    text=f"Progress: {a['progress_pct']:.0f}% completed"
                )
            with action_col:
                if a.get("source_quote"):
                    with st.popover("📖 Handbook Source Quote"):
                        if a.get("source") == "fixture":
                            st.error(
                                "🧪 This quote is fabricated fixture text, not from your "
                                "handbook. Claude was never called for this row."
                            )
                        st.markdown(f"> *\"{a['source_quote']}\"*")
                        st.caption(f"Extraction confidence: **{a['confidence'].upper()}**")


# ---------------------------------------------------------------------------
# UI View 2: Workload Heatmap & Analytics
# ---------------------------------------------------------------------------
def render_workload_heatmap(conn: sqlite3.Connection, today: date) -> None:
    st.header("📈 Term Workload Heatmap & Crunch Weeks")
    st.caption("Surfacing student study bottlenecks across UCL 2026/27 academic terms.")

    assessments = db.list_assessments(conn)
    if not assessments:
        st.info("No assessments in database. Seed sample data or upload PDFs to see your workload chart.")
        return

    term_weeks = get_all_term_weeks()
    analytics_rows = [to_analytics_row(a) for a in assessments]
    loads = analytics.weekly_workload(analytics_rows, term_weeks)

    # Fabricated hours stay in the totals but must not pass for real crunch.
    fabricated_total = sum(l.fabricated_hours for l in loads)
    if fabricated_total > 0:
        st.warning(
            f"🧪 **{fabricated_total:.1f}h of the workload below is fabricated fixture "
            "data**, not estimated from any handbook Claude read. Hatched bars contain "
            "invented hours, and hovering a bar breaks down how many. Delete those rows "
            "under **Manage Assessments** for a true picture."
        )

    # Term selection filter
    t_tab_all, t_tab_1, t_tab_2, t_tab_3 = st.tabs(["Full Academic Year", "Term 1", "Term 2", "Term 3"])

    def create_workload_figure(filtered_loads: list[analytics.WeekLoad]) -> go.Figure:
        x_labels = []
        y_hours = []
        colors = []
        patterns = []
        hover_texts = []

        for item in filtered_loads:
            w = item.week
            label = f"{w.term} W{w.week_number}<br>{w.start_date.strftime('%d %b')}"
            if w.is_reading_week:
                label += "<br>*(Reading)*"
            if item.fabricated_hours > 0:
                label += "<br>🧪"
            x_labels.append(label)
            y_hours.append(item.remaining_hours)
            patterns.append("/" if item.fabricated_hours > 0 else "")

            # Color classification
            if w.is_reading_week:
                c = "#6c757d"  # Muted grey for reading week
            elif item.remaining_hours >= 25.0:
                c = "#dc3545"  # Crimson crunch
            elif item.remaining_hours >= 15.0:
                c = "#fd7e14"  # Amber moderate
            elif item.remaining_hours > 0:
                c = "#0d6efd"  # Blue manageable
            else:
                c = "#dee2e6"  # Empty
            colors.append(c)

            rw_str = " (Reading Week)" if w.is_reading_week else ""
            hover = (
                f"<b>{w.term} Week {w.week_number}{rw_str}</b><br>"
                f"Commencing: {w.start_date.strftime('%A, %d %B %Y')}<br>"
                f"Remaining Work: <b>{item.remaining_hours:.1f} hours</b><br>"
                f"Deadlines Due: <b>{item.assessment_count}</b>"
            )
            if item.fabricated_hours > 0:
                hover += (
                    f"<br>🧪 Fabricated: <b>{item.fabricated_hours:.1f} hours</b> "
                    f"across <b>{item.fabricated_count}</b> fixture row(s)"
                )
            hover_texts.append(hover)

        fig = go.Figure(
            data=[
                go.Bar(
                    x=x_labels,
                    y=y_hours,
                    marker=dict(
                        color=colors,
                        pattern=dict(shape=patterns, solidity=0.35),
                        line=dict(color="#333333", width=1),
                    ),
                    hovertext=hover_texts,
                    hoverinfo="text",
                )
            ]
        )

        fig.add_hline(
            y=20.0,
            line_dash="dot",
            line_color="#dc3545",
            annotation_text="High Crunch Threshold (20h/wk)",
            annotation_position="top right",
        )

        fig.update_layout(
            title="Remaining Assessment Hours Due by Academic Week",
            xaxis_title="UCL Term Week & Start Date",
            yaxis_title="Remaining Hours (estimated)",
            template="plotly_white",
            height=480,
            margin=dict(l=40, r=40, t=60, b=80),
        )
        return fig

    with t_tab_all:
        st.plotly_chart(create_workload_figure(loads), use_container_width=True)

    with t_tab_1:
        t1_loads = [l for l in loads if l.week.term == "T1"]
        st.plotly_chart(create_workload_figure(t1_loads), use_container_width=True)

    with t_tab_2:
        t2_loads = [l for l in loads if l.week.term == "T2"]
        st.plotly_chart(create_workload_figure(t2_loads), use_container_width=True)

    with t_tab_3:
        t3_loads = [l for l in loads if l.week.term == "T3"]
        st.plotly_chart(create_workload_figure(t3_loads), use_container_width=True)

    st.divider()

    # Crunch Weeks Identification
    crunch_weeks = [l for l in loads if l.remaining_hours >= 20.0 and not l.week.is_reading_week]
    if crunch_weeks:
        st.subheader("🚨 Identified Crunch Weeks")
        for cw in crunch_weeks:
            w = cw.week
            message = (
                f"**{w.term} Week {w.week_number}** (Commencing {w.start_date.strftime('%d %B %Y')}): "
                f"**{cw.remaining_hours:.1f} remaining hours** across **{cw.assessment_count} deadlines**."
            )
            if cw.fabricated_hours > 0:
                genuine_hours = cw.remaining_hours - cw.fabricated_hours
                message += (
                    f" 🧪 Includes **{cw.fabricated_hours:.1f}h of fabricated fixture "
                    f"data** — only {genuine_hours:.1f}h of this crunch is real"
                )
                if genuine_hours < 20.0:
                    message += ", which would not be a crunch week on its own"
                message += "."
            st.error(message)
    else:
        st.success("✅ No extreme crunch weeks (>20h) detected with current progress levels.")


# ---------------------------------------------------------------------------
# UI View 3: PDF Upload & Editable Review Table (The Review Gate)
# ---------------------------------------------------------------------------
def _resolve_row_source(value: Any) -> str:
    """Trust a review row's own provenance, defaulting to manual.

    Rows appended in the data editor arrive with no source at all, which makes
    them the student's own entry; never promote an unknown value to 'extracted'.
    """
    if pd.isna(value):
        return "manual"
    candidate = str(value).strip()
    return candidate if candidate in SOURCES else "manual"


def _queue_extraction_notice(level: str, message: str) -> None:
    """Hold a notice in session state so it survives the st.rerun() that follows."""
    st.session_state.setdefault("extraction_notices", []).append((level, message))


def _drain_extraction_notices() -> None:
    """Render notices queued before the last rerun, then clear them."""
    notices = st.session_state.pop("extraction_notices", [])
    for level, message in notices:
        getattr(st, level)(message)


def render_upload_and_review(conn: sqlite3.Connection) -> None:
    st.header("📄 Handbook PDF Extraction & Review")
    st.caption("Extract deadlines from module handbooks. Confirm or edit rows before committing to the database.")

    if "review_queue" not in st.session_state:
        st.session_state.review_queue = []
    if "review_fixture_files" not in st.session_state:
        st.session_state.review_fixture_files = []

    _drain_extraction_notices()

    # File Uploader
    uploaded_files = st.file_uploader(
        "Upload UCL Handbook PDFs",
        type=["pdf"],
        accept_multiple_files=True,
        help="Upload one or more course syllabus/handbook PDFs.",
    )

    if uploaded_files:
        if st.button(f"🚀 Extract Assessments from {len(uploaded_files)} PDF(s) with Claude", type="primary"):
            extracted_items = []
            with st.spinner("Extracting assessments, resolving UCL term weeks, and proposing hours..."):
                for uploaded in uploaded_files:
                    try:
                        content = uploaded.read()
                        result = extract_from_pdf(content, uploaded.name)
                        if result.used_fixtures:
                            st.session_state.review_fixture_files.append(uploaded.name)
                            _queue_extraction_notice(
                                "warning",
                                f"⚠️ **{uploaded.name}: these rows are fabricated demo fixtures, "
                                f"not a real extraction.** Claude did not read this PDF — "
                                f"{result.reason}. Do not trust these dates.",
                            )
                        extracted_items.extend(result.assessments)
                    except Exception as exc:
                        _queue_extraction_notice("error", f"Failed to extract {uploaded.name}: {exc}")

            if extracted_items:
                st.session_state.review_queue.extend(extracted_items)
                _queue_extraction_notice(
                    "success",
                    f"Extracted {len(extracted_items)} candidate assessments! Review them below.",
                )
            st.rerun()

    st.divider()

    # The Review Gate
    queue = st.session_state.review_queue
    if not queue:
        st.info("The review table is currently empty. Upload handbooks above to extract assessments.")
        return

    st.subheader(f"Editable Review Gate ({len(queue)} candidate assessments)")
    st.warning("⚠️ **Nothing is saved to the database until you click 'Confirm & Save' below.**")

    # Persistent provenance badge: the banner above is one-shot, the queue is not.
    fixture_files = st.session_state.review_fixture_files
    if fixture_files:
        st.error(
            "🧪 **FIXTURE DATA IN THIS QUEUE — NOT EXTRACTED FROM YOUR PDFs.** "
            f"Rows sourced from fixture replay for: {', '.join(fixture_files)}. "
            "Every date, weighting and quote below is invented. Discard the queue and fix "
            "`ANTHROPIC_API_KEY` before relying on any of it."
        )

    # High / Low confidence summary
    low_conf_count = sum(1 for r in queue if r.get("confidence") == "low")
    if low_conf_count > 0:
        st.error(f"⚠️ **{low_conf_count} row(s) have LOW confidence.** Please verify their dates and source quotes!")

    # Convert queue to pandas DataFrame for st.data_editor
    df_queue = pd.DataFrame(queue)

    # Ensure required columns exist
    for col, default_val in [
        ("module_code", "COMP0001"),
        ("module_name", ""),
        ("title", "Assessment"),
        ("type", "coursework"),
        ("due_date", "2026-11-30"),
        ("due_time", "16:00:00"),
        ("weighting_pct", 50.0),
        ("word_count", None),
        ("estimated_hours", 20.0),
        ("confidence", "high"),
        ("source", "manual"),
        ("source_quote", ""),
        ("notes", ""),
    ]:
        if col not in df_queue.columns:
            df_queue[col] = default_val

    edited_df = st.data_editor(
        df_queue,
        use_container_width=True,
        num_rows="dynamic",
        column_config={
            "module_code": st.column_config.TextColumn("Module Code", required=True),
            "module_name": st.column_config.TextColumn("Module Name"),
            "title": st.column_config.TextColumn("Title", required=True),
            "type": st.column_config.SelectboxColumn("Type", options=ASSESSMENT_TYPES, required=True),
            "due_date": st.column_config.TextColumn("Due Date (YYYY-MM-DD)", required=True),
            "due_time": st.column_config.TextColumn("Due Time (HH:MM:SS)"),
            "weighting_pct": st.column_config.NumberColumn("Weighting %", min_value=0.0, max_value=100.0),
            "word_count": st.column_config.NumberColumn("Word Count", min_value=0),
            "estimated_hours": st.column_config.NumberColumn("Estimated Hours", min_value=0.0),
            "confidence": st.column_config.SelectboxColumn("Confidence", options=CONFIDENCES, required=True),
            "source": st.column_config.TextColumn(
                "Provenance",
                disabled=True,
                help="How this row was produced. 'fixture' means the app invented it "
                     "because Claude was never called — not a real extraction.",
            ),
            "source_quote": st.column_config.TextColumn("Source Quote (Handbook)"),
            "notes": st.column_config.TextColumn("Notes"),
        },
        key="review_data_editor",
    )

    # Display inspectable low-confidence quotes
    with st.expander("🔍 Inspect Source Quotes & Low Confidence Details", expanded=(low_conf_count > 0)):
        for idx, row in edited_df.iterrows():
            c_badge = "🔴 LOW" if row.get("confidence") == "low" else "🟢 " + str(row.get("confidence")).upper()
            st.markdown(
                f"- **Row {idx + 1}**: `{row.get('module_code')}` **{row.get('title')}** | Confidence: {c_badge}\n"
                f"  - **Due Date:** `{row.get('due_date')}`\n"
                f"  - **Source Quote:** *\"{row.get('source_quote') or 'No quote provided'}\"*"
            )

    # Action Buttons
    c_btn1, c_btn2, _ = st.columns([2, 2, 4])
    with c_btn1:
        if st.button("✅ Confirm & Save to Radar", type="primary"):
            # Validate and persist to DB
            to_save = []
            errors = []
            for idx, r in edited_df.iterrows():
                row_dict = r.to_dict()
                # Required checks
                m_code = str(row_dict.get("module_code") or "").strip()
                t_title = str(row_dict.get("title") or "").strip()
                d_date = str(row_dict.get("due_date") or "").strip()
                a_type = str(row_dict.get("type") or "coursework").strip().lower()

                if not m_code or not t_title or not d_date:
                    errors.append(f"Row {idx + 1}: Module Code, Title, and Due Date are mandatory.")
                    continue
                try:
                    date.fromisoformat(d_date)
                except ValueError:
                    errors.append(f"Row {idx + 1}: Invalid ISO date format '{d_date}'. Use YYYY-MM-DD.")
                    continue

                w_pct = row_dict.get("weighting_pct")
                if pd.isna(w_pct) or w_pct is None or w_pct == "":
                    w_pct = None
                else:
                    w_pct = float(w_pct)

                w_cnt = row_dict.get("word_count")
                if pd.isna(w_cnt) or w_cnt is None or w_cnt == "":
                    w_cnt = None
                else:
                    w_cnt = int(w_cnt)

                d_time = row_dict.get("due_time")
                if pd.isna(d_time) or d_time is None or str(d_time).strip() == "":
                    d_time = None
                else:
                    d_time = str(d_time).strip()
                    if len(d_time) == 5:  # HH:MM -> HH:MM:SS
                        d_time += ":00"

                row_source = _resolve_row_source(row_dict.get("source"))

                quote = str(row_dict.get("source_quote") or "").strip()
                if row_source in db.MACHINE_SOURCES and not quote:
                    errors.append(
                        f"Row {idx + 1}: a '{row_source}' row must keep its source quote. "
                        "Clear the quote only if you are entering the deadline yourself."
                    )
                    continue

                to_save.append({
                    "module_code": m_code,
                    "module_name": str(row_dict.get("module_name") or ""),
                    "title": t_title,
                    "type": a_type if a_type in ASSESSMENT_TYPES else "coursework",
                    "due_date": d_date,
                    "due_time": d_time,
                    "weighting_pct": w_pct,
                    "word_count": w_cnt,
                    "estimated_hours": float(row_dict.get("estimated_hours") or 0.0),
                    "status": "not_started",
                    "progress_pct": 0.0,
                    "source": row_source,
                    "confidence": str(row_dict.get("confidence") or "high"),
                    "source_quote": quote,
                    "notes": str(row_dict.get("notes") or ""),
                })

            if errors:
                for e in errors:
                    st.error(e)
            elif to_save:
                inserted_ids = db.insert_assessments(conn, to_save)
                fixture_saved = sum(1 for r in to_save if r["source"] == "fixture")
                st.session_state.review_queue = []
                st.session_state.review_fixture_files = []
                _queue_extraction_notice(
                    "success",
                    f"Successfully saved {len(inserted_ids)} assessment(s) to Deadline Radar!",
                )
                if fixture_saved:
                    _queue_extraction_notice(
                        "warning",
                        f"⚠️ **{fixture_saved} of those row(s) were saved as fabricated "
                        "fixture data**, and stay labelled `source = fixture` in the "
                        "database. The Dashboard flags them so you can delete them once "
                        "`ANTHROPIC_API_KEY` is configured.",
                    )
                st.rerun()

    with c_btn2:
        if st.button("🗑️ Discard Review Queue"):
            st.session_state.review_queue = []
            st.session_state.review_fixture_files = []
            _queue_extraction_notice("info", "Discarded review queue.")
            st.rerun()


# ---------------------------------------------------------------------------
# UI View 4: Manual Assessment CRUD
# ---------------------------------------------------------------------------
def _assessment_label(a: Mapping[str, Any]) -> str:
    marker = " 🧪 FIXTURE" if a.get("source") == "fixture" else ""
    return f"[{a['module_code']}] {a['title']} (ID: {a['id']}){marker}"


def render_assessment_management(conn: sqlite3.Connection) -> None:
    st.header("✏️ Manual Assessment Management")
    st.caption("Create, edit, or remove assessments manually.")

    tab_add, tab_edit, tab_delete = st.tabs(["Add Assessment", "Edit Assessment", "Delete Assessment"])

    assessments = db.list_assessments(conn)

    # 1. Add Form
    with tab_add:
        st.subheader("Add New Assessment")
        with st.form("manual_add_form", clear_on_submit=True):
            col1, col2 = st.columns(2)
            with col1:
                module_code = st.text_input("Module Code *", placeholder="e.g. COMP0002")
                title = st.text_input("Assessment Title *", placeholder="e.g. Midterm Coursework")
                a_type = st.selectbox("Assessment Type *", options=ASSESSMENT_TYPES, index=2)
                due_date_val = st.date_input("Due Date *", value=REFERENCE_TODAY + timedelta(days=14))
                due_time_str = st.text_input("Due Time (HH:MM:SS or HH:MM)", placeholder="16:00:00")
            with col2:
                module_name = st.text_input("Module Name", placeholder="e.g. Software Engineering")
                weighting = st.number_input("Weighting %", min_value=0.0, max_value=100.0, value=50.0, step=5.0)
                word_count = st.number_input("Word Count (optional)", min_value=0, value=0, step=100)
                estimated_hours = st.number_input("Estimated Hours to Complete *", min_value=0.0, value=20.0, step=1.0)
                notes = st.text_area("Notes", placeholder="Submission instructions, repository links, etc.")

            submitted = st.form_submit_button("➕ Save Assessment", type="primary")
            if submitted:
                if not module_code.strip():
                    st.error("Module Code is required.")
                elif not title.strip():
                    st.error("Assessment Title is required.")
                else:
                    time_val = due_time_str.strip() if due_time_str.strip() else None
                    if time_val and len(time_val) == 5:
                        time_val += ":00"

                    data = {
                        "module_code": module_code.strip(),
                        "module_name": module_name.strip(),
                        "title": title.strip(),
                        "type": a_type,
                        "due_date": due_date_val.isoformat(),
                        "due_time": time_val,
                        "weighting_pct": weighting if weighting > 0 else None,
                        "word_count": word_count if word_count > 0 else None,
                        "estimated_hours": float(estimated_hours),
                        "status": "not_started",
                        "progress_pct": 0.0,
                        "source": "manual",
                        "confidence": "high",
                        "source_quote": "Manual student entry",
                        "notes": notes.strip(),
                    }
                    try:
                        new_id = db.insert_assessment(conn, data)
                        st.success(f"Assessment '{title}' created successfully (ID: {new_id})!")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Failed to save: {exc}")

    # 2. Edit Form
    with tab_edit:
        st.subheader("Edit Existing Assessment")
        if not assessments:
            st.info("No assessments to edit.")
        else:
            options = {_assessment_label(a): a["id"] for a in assessments}
            selected_label = st.selectbox("Select Assessment to Edit", options=list(options.keys()))
            selected_id = options[selected_label]
            current = db.get_assessment(conn, selected_id)

            if current:
                with st.form("manual_edit_form"):
                    col1, col2 = st.columns(2)
                    with col1:
                        e_code = st.text_input("Module Code", value=current["module_code"])
                        e_title = st.text_input("Title", value=current["title"])
                        e_type = st.selectbox(
                            "Type",
                            options=ASSESSMENT_TYPES,
                            index=ASSESSMENT_TYPES.index(current["type"]) if current["type"] in ASSESSMENT_TYPES else 0
                        )
                        curr_date = parse_date(current["due_date"]) or REFERENCE_TODAY
                        e_due_date = st.date_input("Due Date", value=curr_date)
                        e_due_time = st.text_input("Due Time", value=current["due_time"] or "")
                        e_status = st.selectbox(
                            "Status",
                            options=STATUSES,
                            index=STATUSES.index(current["status"]) if current["status"] in STATUSES else 0
                        )
                    with col2:
                        e_name = st.text_input("Module Name", value=current["module_name"] or "")
                        e_weight = st.number_input(
                            "Weighting %",
                            min_value=0.0,
                            max_value=100.0,
                            value=float(current["weighting_pct"] or 0.0)
                        )
                        e_word = st.number_input(
                            "Word Count",
                            min_value=0,
                            value=int(current["word_count"] or 0)
                        )
                        e_hours = st.number_input(
                            "Estimated Hours",
                            min_value=0.0,
                            value=float(current["estimated_hours"] or 0.0)
                        )
                        e_prog = st.slider(
                            "Progress %",
                            min_value=0.0,
                            max_value=100.0,
                            value=float(current["progress_pct"] or 0.0)
                        )
                        e_notes = st.text_area("Notes", value=current["notes"] or "")

                    save_edit = st.form_submit_button("💾 Save Changes", type="primary")
                    if save_edit:
                        time_val = e_due_time.strip() if e_due_time.strip() else None
                        if time_val and len(time_val) == 5:
                            time_val += ":00"

                        changes = {
                            "module_code": e_code.strip(),
                            "module_name": e_name.strip(),
                            "title": e_title.strip(),
                            "type": e_type,
                            "due_date": e_due_date.isoformat(),
                            "due_time": time_val,
                            "status": e_status,
                            "weighting_pct": e_weight if e_weight > 0 else None,
                            "word_count": e_word if e_word > 0 else None,
                            "estimated_hours": float(e_hours),
                            "progress_pct": float(e_prog),
                            "notes": e_notes.strip(),
                        }
                        try:
                            db.update_assessment(conn, selected_id, changes)
                            st.success(f"Assessment {selected_id} updated successfully!")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Failed to update: {exc}")

    # 3. Delete Form
    with tab_delete:
        st.subheader("Delete Assessment")
        if not assessments:
            st.info("No assessments to delete.")
        else:
            del_options = {_assessment_label(a): a["id"] for a in assessments}
            del_label = st.selectbox("Select Assessment to Delete", options=list(del_options.keys()), key="del_sel")
            del_id = del_options[del_label]

            st.warning("⚠️ **Cascade Delete Warning:** Deleting this assessment will also permanently remove all logged progress sessions.")
            confirm_del = st.checkbox("I understand and confirm deletion", key="confirm_del_cb")

            if st.button("🗑️ Permanently Delete", type="primary", disabled=not confirm_del):
                deleted = db.delete_assessment(conn, del_id)
                if deleted:
                    st.success(f"Assessment {del_id} deleted successfully.")
                    st.rerun()
                else:
                    st.error("Could not delete assessment.")


# ---------------------------------------------------------------------------
# UI View 5: Progress Tracking & History
# ---------------------------------------------------------------------------
def render_progress_tracker(conn: sqlite3.Connection) -> None:
    st.header("⏱️ Progress Tracking & Work Sessions")
    st.caption("Log hours spent and track your completion momentum toward each deadline.")

    assessments = db.list_assessments(conn)
    if not assessments:
        st.info("No assessments to log progress for. Add assessments or load sample data first.")
        return

    # Assessment picker
    options = {f"[{a['module_code']}] {a['title']} ({a['progress_pct']:.0f}%)": a["id"] for a in assessments}
    sel_label = st.selectbox("Select Assessment to Log Progress For", options=list(options.keys()))
    sel_id = options[sel_label]
    current = db.get_assessment(conn, sel_id)

    if not current:
        return

    # Assessment status glance
    col_info1, col_info2, col_info3 = st.columns(3)
    total_spent = db.total_hours_spent(conn, sel_id)
    rem_h = current["estimated_hours"] * (1 - current["progress_pct"] / 100.0)

    col_info1.metric("Current Progress", f"{current['progress_pct']:.0f}%")
    col_info2.metric("Total Hours Spent", f"{total_spent:.1f} hrs")
    col_info3.metric("Remaining Hours", f"{rem_h:.1f} hrs", f"Est. {current['estimated_hours']}h")

    st.divider()

    # Log new progress session
    st.subheader("Log New Work Session")
    with st.form("log_progress_form"):
        col1, col2 = st.columns(2)
        with col1:
            hours_spent = st.number_input("Hours spent this session *", min_value=0.25, max_value=80.0, value=2.0, step=0.5)
            note = st.text_input("Session Notes / Accomplishments", placeholder="e.g. Wrote Section 2 and debugged test suite")
        with col2:
            new_progress = st.slider(
                "New Total Progress Percentage After Session *",
                min_value=0.0,
                max_value=100.0,
                value=min(100.0, float(current["progress_pct"]) + 10.0),
                step=5.0
            )
            mark_submitted = False
            if new_progress >= 100.0:
                mark_submitted = st.checkbox("Mark assessment status as 'Submitted' ✅", value=True)

        submitted = st.form_submit_button("💾 Record Work Session", type="primary")
        if submitted:
            try:
                db.log_progress(
                    conn,
                    assessment_id=sel_id,
                    hours_spent=float(hours_spent),
                    progress_pct_after=float(new_progress),
                    note=note.strip(),
                )
                if mark_submitted:
                    db.update_assessment(conn, sel_id, {"status": "submitted"})
                st.success(f"Logged {hours_spent} hours! Updated progress to {new_progress:.0f}%.")
                st.rerun()
            except Exception as exc:
                st.error(f"Failed to log progress: {exc}")

    st.divider()

    # Session History Timeline
    st.subheader(f"Progress Session History for [{current['module_code']}] {current['title']}")
    history = db.get_progress_history(conn, sel_id)

    if not history:
        st.write("No progress logged for this assessment yet.")
    else:
        df_hist = pd.DataFrame(history)
        df_hist = df_hist.rename(columns={
            "logged_at": "Logged At (UTC)",
            "hours_spent": "Hours Spent",
            "progress_pct_after": "Progress After %",
            "note": "Note / Accomplishment",
        })
        st.dataframe(
            df_hist[["Logged At (UTC)", "Hours Spent", "Progress After %", "Note / Accomplishment"]],
            use_container_width=True,
            hide_index=True,
        )


# ---------------------------------------------------------------------------
# Main Application Shell & Navigation
# ---------------------------------------------------------------------------
def main() -> None:
    st.set_page_config(
        page_title="Deadline Radar | UCL Assessment Tracker",
        page_icon="🎯",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    conn = get_connection()

    # Sidebar Navigation & Settings
    st.sidebar.title("🎯 Deadline Radar")
    st.sidebar.caption("Claude Campus Hackathon — UCL Assessment Tracker")

    nav_page = st.sidebar.radio(
        "Navigation",
        [
            "📊 Dashboard",
            "📈 Workload Heatmap",
            "📄 Upload & Review",
            "✏️ Manage Assessments",
            "⏱️ Progress Tracking",
        ],
        index=0,
        key="main_nav_radio",
    )

    st.sidebar.divider()

    # Calendar Reference Date Switcher
    st.sidebar.subheader("📅 Academic Simulation Date")
    simulated_today = st.sidebar.date_input(
        "Reference Date ('Today')",
        value=REFERENCE_TODAY,
        help="Simulate today's date to evaluate crunch weeks and risk thresholds dynamically.",
        key="simulated_today_picker",
    )

    st.sidebar.divider()

    # Export & Data Utilities
    st.sidebar.subheader("🛠️ Quick Actions")
    assessments = db.list_assessments(conn)

    exportable, fabricated = export.split_fabricated(assessments)

    if exportable:
        try:
            ics_bytes = export.export_calendar(exportable)
            st.sidebar.download_button(
                label="📥 Export Calendar (.ics)",
                data=ics_bytes,
                file_name="deadline-radar.ics",
                mime="text/calendar",
                help="Export real deadlines to Google Calendar, Apple Calendar, or "
                     "Outlook. Fabricated fixture rows are never exported.",
            )
        except Exception as exc:
            st.sidebar.caption(f"ICS Export unavailable: {exc}")
        if fabricated:
            st.sidebar.warning(
                f"🧪 The .ics holds {len(exportable)} real deadline(s) and leaves out "
                f"{len(fabricated)} fabricated fixture row(s). An imported calendar "
                "event cannot be recalled, so invented dates stay inside the app."
            )
    elif fabricated:
        st.sidebar.warning(
            f"🧪 Nothing to export: all {len(fabricated)} tracked assessment(s) are "
            "fabricated fixture data, which never goes into your calendar. Configure "
            "`ANTHROPIC_API_KEY`, re-extract your handbooks, then export."
        )

    if st.sidebar.button("🌱 Load Demo UCL Data", key="side_seed"):
        count = seed_demo_data(conn)
        st.sidebar.success(f"Seeded {count} demo assessments!")
        st.rerun()

    if st.sidebar.button("🗑️ Reset All Data", key="side_reset"):
        conn.execute("DELETE FROM progress_log")
        conn.execute("DELETE FROM assessments")
        conn.commit()
        st.sidebar.info("All assessments and logs wiped.")
        st.rerun()

    # Render selected view
    if nav_page == "📊 Dashboard":
        render_dashboard(conn, simulated_today)
    elif nav_page == "📈 Workload Heatmap":
        render_workload_heatmap(conn, simulated_today)
    elif nav_page == "📄 Upload & Review":
        render_upload_and_review(conn)
    elif nav_page == "✏️ Manage Assessments":
        render_assessment_management(conn)
    elif nav_page == "⏱️ Progress Tracking":
        render_progress_tracker(conn)


if __name__ == "__main__":
    main()
