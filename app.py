"""Deadline Radar - Streamlit Frontend.

UCL assessment tracker built for the Claude Campus Hackathon.
Surfaces crunch weeks, tracks progress, and flags assessments at risk of slipping.
"""

from __future__ import annotations

from copy import deepcopy
from contextlib import closing
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
import radar.plan as plan

# Re-exported so the upload flow and its tests can keep calling these unqualified;
# the implementation lives in radar/extract.py, never in a Streamlit callback.
from radar.extract import ExtractionResult, extract_from_pdf

from sample_data.seed import clear_demo_data, seed_demo_data

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Constants & Configurations
# ---------------------------------------------------------------------------
DEFAULT_DB_FILE = Path(os.environ.get("RADAR_DB_PATH", _CURRENT_DIR / "data" / "radar.db"))
# Demo day. sample_data/seed.py is tuned to this date, so move the two together.
REFERENCE_TODAY = date(2026, 10, 16)

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


def suggested_start_dates(
    rows: list[dict], term_weeks: list[analytics.TermWeek]
) -> dict[int, date]:
    """Backward-planned start date per assessment id.

    Each row is planned against every other row, so a week already carrying
    deadlines offers less capacity and pushes its neighbours' start dates
    earlier. Feeds `assess_risk`, which otherwise assumes an empty term.
    """
    return {
        row["id"]: analytics.suggested_start_date(
            row, [other for other in rows if other["id"] != row["id"]], term_weeks
        )
        for row in rows
    }


# ---------------------------------------------------------------------------
# UI View 1: Dashboard
# ---------------------------------------------------------------------------
STATUS_LABELS = {"not_started": "Not started", "in_progress": "In progress", "submitted": "Submitted"}


def page_header(title: str, subtitle: str) -> None:
    st.title(title)
    st.caption(subtitle)


def show_notice() -> None:
    notice = st.session_state.pop("ui_notice", None)
    if notice:
        st.success(notice)


def select_assessment(assessment_id: int, view: str) -> None:
    st.session_state.selected_assessment = assessment_id
    st.session_state.assessment_view = view
    for key in ("edit_selection", "progress_selection", "del_sel"):
        st.session_state.pop(key, None)


def risk_context(rows: list[dict[str, Any]], today: date) -> tuple[dict[int, analytics.RiskResult], dict[int, date]]:
    shaped = [to_analytics_row(row) for row in rows]
    starts = suggested_start_dates(shaped, get_all_term_weeks())
    risks = {row["id"]: analytics.assess_risk(row, today, start_date=starts[row["id"]])
             for row in shaped}
    return risks, starts


def fixture_notice(rows: list[dict[str, Any]]) -> None:
    count = sum(row.get("source") == "fixture" for row in rows)
    if count:
        st.warning(f"{count} assessment(s) use fabricated fixture data. Dates and source quotes are invented; calendar export excludes them.")


def create_workload_figure(loads: list[analytics.WeekLoad], compact: bool = False) -> go.Figure:
    labels = [load.week.start_date.strftime("%d %b") + (" (R)" if load.week.is_reading_week else "") for load in loads]
    fig = go.Figure(go.Bar(
        x=labels, y=[load.remaining_hours for load in loads],
        marker=dict(color=["#bc8140" if load.remaining_hours > 20 else "#577baf" for load in loads],
                    pattern=dict(shape=["/" if load.fabricated_hours else "" for load in loads], solidity=0.25)),
        customdata=[[load.assessment_count, load.fabricated_hours, load.week.term,
                     load.week.week_number, "Reading week" if load.week.is_reading_week else "Teaching week"]
                    for load in loads],
        hovertemplate="Week of %{x} · %{customdata[2]} W%{customdata[3]}<br>%{y:.1f} hours · %{customdata[0]} deadlines<br>%{customdata[1]:.1f} fixture hours · %{customdata[4]}<extra></extra>",
    ))
    fig.add_hline(y=20, line_dash="dot", line_color="#9ca3af", annotation_text="20h / week",
                  annotation_position="top right")
    fig.update_layout(height=240 if compact else 340, margin=dict(l=0, r=12, t=24, b=0),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(size=12), showlegend=False, bargap=0.42,
                      xaxis=dict(title=None, fixedrange=True, showgrid=False),
                      yaxis=dict(title="Hours", fixedrange=True, gridcolor="rgba(128,128,128,0.15)", zeroline=False))
    return fig


def assessment_row(row: dict[str, Any], today: date, risk: analytics.RiskResult,
                   start_date: date, quick_actions: bool = False, prefix: str = "list") -> None:
    due = date.fromisoformat(row["due_date"])
    days = (due - today).days
    with st.container(border=False, gap="xsmall"):
        title_col, date_col, progress_col = st.columns([5, 2, 2], vertical_alignment="center")
        with title_col:
            st.markdown(f"**{row['title']}**")
            module = row.get("module_name") or row["module_code"]
            st.caption(f"{row['module_code']} · {module}")
        with date_col:
            if row["status"] == "submitted":
                status = ":green[Submitted]"
            elif days < 0:
                status = f":red[{abs(days)} days overdue]"
            elif days == 0:
                status = ":orange[Due today]"
            elif risk.at_risk:
                status = f":orange[At risk · {days} days left]"
            else:
                status = f"{days} days left"
            st.markdown(status)
            st.caption(due.strftime("%d %b %Y") + (f" · {row['due_time'][:5]}" if row.get("due_time") else ""))
        with progress_col:
            st.caption(f"{row['progress_pct']:.0f}% complete")
            st.progress(float(row["progress_pct"]) / 100)
        with st.container(horizontal=True, gap="small", vertical_alignment="center"):
            if quick_actions:
                st.button("Edit", key=f"{prefix}_edit_{row['id']}", type="tertiary",
                          on_click=select_assessment, args=(row["id"], "Edit"))
                st.button("Log progress", key=f"{prefix}_progress_{row['id']}", type="tertiary",
                          on_click=select_assessment, args=(row["id"], "Progress"))
            if row.get("source") == "fixture":
                st.caption("FIXTURE · invented deadline")
            elif row.get("confidence") == "low":
                st.caption("Date needs verification")
        with st.expander("Assessment details"):
            shaped = to_analytics_row(row)
            st.caption(f"{STATUS_LABELS[row['status']]} · {row['type'].replace('_', ' ')} · {analytics.remaining_hours(shaped):.1f}h remaining · Suggested start {start_date:%d %b}")
            if risk.at_risk:
                expected = analytics.expected_progress_pct(today, start_date, due)
                reasons = {analytics.REASON_PROGRESS_BEHIND: f"Progress is {row['progress_pct']:.0f}% against {expected:.0f}% expected.",
                           analytics.REASON_INSUFFICIENT_TIME: "Remaining work exceeds the time available at 10 hours per week."}
                st.warning(" ".join(reasons[reason] for reason in risk.reasons))
            weighting = "Not specified" if row.get("weighting_pct") is None else f"{row['weighting_pct']:g}%"
            st.caption(f"Weighting: {weighting} · Word count: {row.get('word_count') or 'Not specified'}")
            quote = row.get("source_quote")
            if quote:
                st.caption("Source quote" + (" — fabricated fixture text" if row.get("source") == "fixture" else ""))
                st.write(quote)
            if row.get("notes"):
                st.write(row["notes"])


def render_dashboard(conn: sqlite3.Connection, today: date) -> None:
    page_header("Overview", f"Your deadlines at a glance · {today:%A, %d %B %Y}")
    rows = db.list_assessments(conn)
    if not rows:
        st.subheader("A clearer view of your term")
        st.caption("Import a module handbook or add your first assessment to get started.")
        with st.container(horizontal=True):
            st.page_link("app_pages/import_handbooks.py", label="Import a handbook", icon=":material/upload_file:")
            st.page_link("app_pages/assessments.py", label="Add an assessment")
        if st.button("Load demo data", key="dash_seed_btn"):
            seed_demo_data(conn)
            st.session_state.ui_notice = "Loaded 10 demo assessments. Your own work is unchanged."
            st.rerun()
        return
    risks, starts = risk_context(rows, today)
    active = [row for row in rows if row["status"] != "submitted"]
    fixture_notice(rows)
    cols = st.columns(4)
    cols[0].metric("Tracked", len(rows))
    cols[1].metric("Due in 14 days", sum(0 <= (date.fromisoformat(r["due_date"]) - today).days <= 14 for r in active))
    cols[2].metric("At risk", sum(risks[r["id"]].at_risk for r in active),
                   help="Includes fixture assessments, where present. Open assessment details for the reason.")
    cols[3].metric("Remaining work", f"{sum(analytics.remaining_hours(r) for r in active):.0f}h")
    with st.container(horizontal=True, horizontal_alignment="distribute"):
        st.subheader("Workload ahead")
        st.page_link("app_pages/workload.py", label="View full term")
    loads = analytics.weekly_workload([to_analytics_row(r) for r in rows], get_all_term_weeks())
    ahead = [load for load in loads if load.week.start_date + timedelta(days=6) >= today][:8]
    st.plotly_chart(create_workload_figure(ahead, compact=True), key="overview_workload",
                    config={"displayModeBar": False}, alt="Estimated remaining hours due over the next eight term weeks")
    with st.container(horizontal=True, horizontal_alignment="distribute"):
        st.subheader("Upcoming deadlines")
        st.page_link("app_pages/assessments.py", label="View all assessments")
    if not active:
        st.caption("Everything is submitted. Your calendar and progress history are still available.")
    for row in active[:6]:
        assessment_row(row, today, risks[row["id"]], starts[row["id"]], prefix="overview")


def render_assessments(conn: sqlite3.Connection, today: date) -> None:
    page_header("Assessments", "All your deadlines, with room to make progress.")
    mode = st.segmented_control("Assessment view", ["List", "Add", "Edit", "Progress", "Delete"],
                                default="List", required=True, key="assessment_view", label_visibility="collapsed")
    if mode == "Progress":
        render_progress_tracker(conn)
        return
    if mode != "List":
        render_assessment_management(conn, mode)
        return
    rows = db.list_assessments(conn)
    if not rows:
        st.subheader("No assessments yet")
        st.caption("Choose Add above, or import deadlines from a handbook.")
        st.page_link("app_pages/import_handbooks.py", label="Import a handbook")
        return
    fixture_notice(rows)
    with st.container(horizontal=True, vertical_alignment="bottom"):
        query = st.text_input("Search assessments", placeholder="Title or module…", key="assessment_search", width=220)
        selected_modules = st.multiselect("Modules", sorted({r["module_code"] for r in rows}), key="assessment_modules", width=160)
        statuses = st.multiselect("Status", STATUSES, default=["not_started", "in_progress"],
                                  format_func=STATUS_LABELS.get, key="assessment_statuses", width=180)
        only_risk = st.checkbox("At risk only", key="assessment_risk")
        order = st.selectbox("Sort", ["Due date", "Risk first", "Remaining hours"], key="assessment_sort", width=160)
    risks, starts = risk_context(rows, today)
    filtered = [r for r in rows if r["status"] in statuses
                and (not selected_modules or r["module_code"] in selected_modules)
                and query.casefold() in f"{r['title']} {r['module_code']} {r['module_name']}".casefold()
                and (not only_risk or risks[r["id"]].at_risk)]
    if order == "Risk first":
        filtered.sort(key=lambda r: (not risks[r["id"]].at_risk, r["due_date"]))
    elif order == "Remaining hours":
        filtered.sort(key=analytics.remaining_hours, reverse=True)
    st.caption(f"{len(filtered)} assessments · {sum(r['status'] == 'submitted' for r in rows)} submitted")
    if not filtered:
        st.caption("No assessments match these filters. Clear the search or adjust status and modules.")
    for row in filtered:
        assessment_row(row, today, risks[row["id"]], starts[row["id"]], quick_actions=True)


def render_workload_heatmap(conn: sqlite3.Connection, today: date) -> None:
    page_header("Workload", "See the busy weeks before they arrive.")
    rows = db.list_assessments(conn)
    if not rows:
        st.subheader("Your workload starts here")
        st.caption("Add an assessment or import a handbook to see your term.")
        st.page_link("app_pages/import_handbooks.py", label="Import a handbook")
        return
    loads = analytics.weekly_workload([to_analytics_row(r) for r in rows], get_all_term_weeks())
    term = st.segmented_control("Term", ["All terms", "Term 1", "Term 2", "Term 3"],
                                default="Term 1", required=True, key="workload_term")
    shown = loads if term == "All terms" else [load for load in loads if load.week.term == f"T{term[-1]}"]
    cols = st.columns(3)
    busiest = max(shown, key=lambda load: load.remaining_hours)
    cols[0].metric("Remaining hours", f"{sum(l.remaining_hours for l in shown):.0f}h")
    cols[1].metric("Busiest week", busiest.week.start_date.strftime("%d %b"), help=f"{busiest.remaining_hours:.1f} hours")
    cols[2].metric("Busy weeks", sum(l.remaining_hours > 20 for l in shown))
    fixture_hours = sum(l.fabricated_hours for l in shown)
    if fixture_hours:
        st.warning(f"{fixture_hours:.1f}h are fabricated fixture data. Hatched bars and tooltips identify the invented share.")
    st.plotly_chart(create_workload_figure(shown), key="term_workload", config={"displayModeBar": False},
                    alt="Remaining assessment hours by week, with the 20-hour threshold marked")
    st.caption("Hours are grouped by deadline week, not a study timetable. (R) marks reading week; hatched bars contain fixture hours.")
    st.subheader("Weeks to plan around")
    busy = [load for load in shown if load.remaining_hours > 20]
    if not busy:
        st.caption("No weeks exceed 20 remaining hours in this view.")
    for load in busy:
        st.markdown(f"**{load.week.start_date:%d %b}** · {load.remaining_hours:.1f}h · {load.assessment_count} deadlines")
        if load.fabricated_hours:
            st.caption(f"Includes {load.fabricated_hours:.1f} fixture hours; {load.remaining_hours - load.fabricated_hours:.1f}h from genuine rows.")


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
    page_header("Import", "Turn module handbooks into deadlines you can trust.")
    st.caption("1 · Upload    /    2 · Review    /    3 · Save")

    if "review_queue" not in st.session_state:
        st.session_state.review_queue = []
    if "review_fixture_files" not in st.session_state:
        st.session_state.review_fixture_files = []

    _drain_extraction_notices()

    with st.expander("1. Upload handbooks", expanded=not bool(st.session_state.review_queue)):
        # File Uploader
        uploaded_files = st.file_uploader(
            "Module handbooks (PDF)",
            type=["pdf"],
            accept_multiple_files=True,
            help="Upload one or more course syllabus/handbook PDFs.",
        )

        if uploaded_files:
            extraction_method = (
                "offline fixtures" if os.environ.get(extract.FIXTURES_ENV, "0").strip() == "1"
                or not os.environ.get("ANTHROPIC_API_KEY", "").strip() else "Claude")
            if st.session_state.review_queue:
                st.caption("Confirm or discard the current review queue before extracting more handbooks.")
            if st.button(f"Extract deadlines from {len(uploaded_files)} PDF(s) with {extraction_method}", type="primary",
                         disabled=bool(st.session_state.review_queue)):
                extracted_items = []
                with st.spinner("Extracting assessments, resolving UCL term weeks, and proposing hours..."):
                    for uploaded in uploaded_files:
                        try:
                            content = uploaded.getvalue()
                            result = extract.extract_from_pdf(content, uploaded.name)
                            if result.used_fixtures:
                                st.session_state.review_fixture_files.append(uploaded.name)
                                _queue_extraction_notice(
                                    "warning",
                                    f"**{uploaded.name}: these rows are fabricated demo fixtures, "
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

    # The Review Gate
    if "review_data_editor" not in st.session_state and "review_editor_draft" in st.session_state:
        st.session_state.review_queue = deepcopy(st.session_state.review_editor_draft)
    queue = st.session_state.review_queue
    if not queue:
        st.caption("Your review queue is empty. Upload a handbook to continue.")
        return

    st.subheader(f"2. Review {len(queue)} deadlines")
    st.caption("Edit cells or select a row to remove it. Verify low-confidence dates against the source quote.")

    fixture_count = sum(r.get("source") == "fixture" for r in queue)
    if fixture_count:
        st.warning(f"Fixture data in this queue: {fixture_count} fabricated deadlines. Their dates, weightings and quotes are invented, and will not be exported.")
    low_conf_count = sum(r.get("confidence") == "low" for r in queue)
    if low_conf_count:
        st.caption(f":orange[{low_conf_count} date(s) need verification.] Open the source quotes below before saving.")

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
        width="stretch",
        num_rows="dynamic",
        column_config={
            "module_code": st.column_config.TextColumn("Module code", required=True),
            "module_name": st.column_config.TextColumn("Module name"),
            "title": st.column_config.TextColumn("Title", required=True),
            "type": st.column_config.SelectboxColumn("Type", options=ASSESSMENT_TYPES, required=True),
            "due_date": st.column_config.TextColumn("Due date (YYYY-MM-DD)", required=True),
            "due_time": st.column_config.TextColumn("Due time (HH:MM:SS)"),
            "weighting_pct": st.column_config.NumberColumn("Weighting %", min_value=0.0, max_value=100.0),
            "word_count": st.column_config.NumberColumn("Word count", min_value=0),
            "estimated_hours": st.column_config.NumberColumn("Estimated hours", min_value=0.0),
            "confidence": st.column_config.SelectboxColumn("Confidence", options=CONFIDENCES, required=True),
            "source": st.column_config.TextColumn(
                "Provenance",
                disabled=True,
                help="How this row was produced. 'fixture' means the app invented it "
                     "because Claude was never called — not a real extraction.",
            ),
            "source_quote": st.column_config.TextColumn("Source quote"),
            "notes": st.column_config.TextColumn("Notes"),
        },
        key="review_data_editor",
        column_order=["module_code", "title", "due_date", "due_time", "estimated_hours",
                      "confidence", "source", "source_quote", "type", "weighting_pct",
                      "word_count", "module_name", "notes"],
        alt="Review and edit extracted deadlines before saving",
    )
    # Widget state is removed when its page is hidden. Restore the edited
    # records as the next editor's input, rather than writing its read-only key.
    st.session_state.review_editor_draft = edited_df.to_dict(orient="records")

    with st.expander("Source quotes and date confidence", expanded=low_conf_count > 0):
        for idx, row in edited_df.iterrows():
            st.markdown(f"**{row.get('title')}** · {row.get('module_code')}")
            st.caption(f"{row.get('due_date')} · {row.get('confidence')} confidence")
            st.write(row.get("source_quote") or "No source quote supplied.")

    st.subheader("3. Save to your radar")
    st.caption("Only the reviewed rows are saved. Nothing is written until you confirm.")
    # Action Buttons
    existing_deadlines = {
        (a["module_code"].strip().casefold(), a["title"].strip().casefold(), a["due_date"])
        for a in db.list_assessments(conn)
    }
    seen_deadlines = set(existing_deadlines)
    duplicate_count = 0
    for record in edited_df.to_dict(orient="records"):
        identity = (str(record.get("module_code") or "").strip().casefold(),
                    str(record.get("title") or "").strip().casefold(),
                    str(record.get("due_date") or ""))
        if identity in seen_deadlines:
            duplicate_count += 1
        seen_deadlines.add(identity)
    allow_duplicates = False
    if duplicate_count:
        st.warning(f"{duplicate_count} review row(s) repeat a module, title and date already tracked or in this queue.")
        allow_duplicates = st.checkbox("Save duplicate deadlines anyway", key="review_allow_duplicates")
    actions = st.container(horizontal=True)
    with actions:
        if st.button("Discard review"):
            st.session_state.review_queue = []
            st.session_state.review_fixture_files = []
            st.session_state.pop("review_editor_draft", None)
            _queue_extraction_notice("info", "Discarded review queue.")
            st.rerun()

    with actions:
        if st.button("Confirm & save", type="primary"):
            if duplicate_count and not allow_duplicates:
                st.error("Nothing was saved. Remove the duplicate rows or explicitly choose to save them anyway.")
                return
            # Validate and persist to DB
            to_save = []
            errors = []
            for idx, r in edited_df.iterrows():
                try:
                    row_dict = {k: None if pd.isna(v) else v for k, v in r.to_dict().items()}
                    # Required checks
                    m_code = str(row_dict.get("module_code") or "").strip()
                    t_title = str(row_dict.get("title") or "").strip()
                    d_date = str(row_dict.get("due_date") or "").strip()
                    a_type = str(row_dict.get("type") or "coursework").strip().lower()

                    notes = str(row_dict.get("notes") or "")
                    unresolved = " - placeholder, correct before confirming." in notes
                    if unresolved and d_date == extract.UNRESOLVED_DUE_DATE:
                        errors.append(
                            f"Row {idx + 1}: the deadline is an unresolved placeholder. "
                            "Correct its date from the module schedule or remove the row before saving.")
                        continue
                    if unresolved:
                        notes = notes.replace(
                            " - placeholder, correct before confirming.",
                            f" - corrected during review to {d_date}.")

                    if not m_code or not t_title or not d_date:
                        errors.append(f"Row {idx + 1}: Module code, Title, and Due date are mandatory.")
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
                        if float(w_cnt) != int(w_cnt):
                            raise ValueError("Word count must be an integer")
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
                        "notes": notes,
                    })
                except (ValueError, TypeError, OverflowError) as exc:
                    errors.append(f"Row {idx + 1}: {exc}")

            if errors:
                for e in errors:
                    st.error(e)
            elif to_save:
                try:
                    inserted_ids = db.insert_assessments(conn, to_save)
                except (ValueError, sqlite3.Error) as exc:
                    st.error(f"Nothing was saved. Correct the review entries and retry: {exc}")
                    return
                fixture_saved = sum(1 for r in to_save if r["source"] == "fixture")
                st.session_state.review_queue = []
                st.session_state.review_fixture_files = []
                st.session_state.pop("review_editor_draft", None)
                _queue_extraction_notice(
                    "success",
                    f"Successfully saved {len(inserted_ids)} assessment(s) to Deadline Radar!",
                )
                if fixture_saved:
                    _queue_extraction_notice(
                        "warning",
                        f"**{fixture_saved} of those row(s) were saved as fabricated "
                        "fixture data**. They stay labelled and are excluded from calendar export.",
                    )
                st.rerun()



# ---------------------------------------------------------------------------
# UI View 4: Manual Assessment CRUD
# ---------------------------------------------------------------------------
def _assessment_label(a: Mapping[str, Any]) -> str:
    marker = " · FIXTURE" if a.get("source") == "fixture" else ""
    return f"[{a['module_code']}] {a['title']} (ID: {a['id']}){marker}"


def render_assessment_management(conn: sqlite3.Connection, mode: str = "Add") -> None:

    assessments = db.list_assessments(conn)

    # 1. Add Form
    if mode == "Add":
        st.subheader("Add assessment")
        with st.form("manual_add_form", clear_on_submit=True):
            col1, col2 = st.columns(2)
            with col1:
                module_code = st.text_input("Module code *", placeholder="e.g. COMP0002")
                title = st.text_input("Assessment title *", placeholder="e.g. Midterm Coursework")
                a_type = st.selectbox("Assessment type *", options=ASSESSMENT_TYPES, index=2)
                due_date_val = st.date_input("Due date *", value=st.session_state.get("reference_today", REFERENCE_TODAY) + timedelta(days=14))
                due_time_str = st.text_input("Due time (HH:MM:SS or HH:MM)", placeholder="16:00:00")
            with col2:
                module_name = st.text_input("Module name", placeholder="e.g. Software Engineering")
                weighting = st.number_input("Weighting %", min_value=0.0, max_value=100.0, value=50.0, step=5.0)
                word_count = st.number_input("Word count (optional)", min_value=0, value=0, step=100)
                estimated_hours = st.number_input("Estimated hours *", min_value=0.0, value=20.0, step=1.0)
                notes = st.text_area("Notes", placeholder="Submission instructions, repository links, etc.")

            submitted = st.form_submit_button("Save assessment", type="primary")
            if submitted:
                if not module_code.strip():
                    st.error("Module code is required.")
                elif not title.strip():
                    st.error("Assessment title is required.")
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
                        st.session_state.ui_notice = f"Added {title}."
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Failed to save: {exc}")

    # 2. Edit Form
    if mode == "Edit":
        st.subheader("Edit assessment")
        if not assessments:
            st.info("No assessments to edit.")
        else:
            options = {_assessment_label(a): a["id"] for a in assessments}
            selected_label = st.selectbox("Assessment to edit", options=list(options.keys()),
                                          index=selection_index(options), key="edit_selection")
            selected_id = options[selected_label]
            current = db.get_assessment(conn, selected_id)

            if current:
                with st.form(f"manual_edit_form_{selected_id}"):
                    col1, col2 = st.columns(2)
                    with col1:
                        e_code = st.text_input("Module code", value=current["module_code"])
                        e_title = st.text_input("Title", value=current["title"])
                        e_type = st.selectbox(
                            "Type",
                            options=ASSESSMENT_TYPES,
                            index=ASSESSMENT_TYPES.index(current["type"]) if current["type"] in ASSESSMENT_TYPES else 0
                        )
                        curr_date = parse_date(current["due_date"]) or REFERENCE_TODAY
                        e_due_date = st.date_input("Due date", value=curr_date)
                        e_due_time = st.text_input("Due time", value=current["due_time"] or "")
                        e_status = st.selectbox(
                            "Status",
                            options=STATUSES, format_func=STATUS_LABELS.get,
                            index=STATUSES.index(current["status"]) if current["status"] in STATUSES else 0
                        )
                    with col2:
                        e_name = st.text_input("Module name", value=current["module_name"] or "")
                        e_weight = st.number_input(
                            "Weighting %",
                            min_value=0.0,
                            max_value=100.0,
                            value=float(current["weighting_pct"] or 0.0)
                        )
                        e_word = st.number_input(
                            "Word count",
                            min_value=0,
                            value=int(current["word_count"] or 0)
                        )
                        e_hours = st.number_input(
                            "Estimated hours",
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

                    save_edit = st.form_submit_button("Save changes", type="primary")
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
                            st.session_state.ui_notice = "Assessment updated."
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Failed to update: {exc}")

    # 3. Delete Form
    if mode == "Delete":
        st.subheader("Delete assessment")
        if not assessments:
            st.info("No assessments to delete.")
        else:
            del_options = {_assessment_label(a): a["id"] for a in assessments}
            del_label = st.selectbox("Assessment to delete", options=list(del_options.keys()), index=selection_index(del_options), key="del_sel")
            del_id = del_options[del_label]

            st.warning("Deleting this assessment also permanently removes its progress history.")
            confirm_del = st.checkbox("I understand and confirm deletion", key=f"confirm_del_cb_{del_id}")

            if st.button("Delete assessment", type="primary", disabled=not confirm_del):
                deleted = db.delete_assessment(conn, del_id)
                if deleted:
                    st.session_state.ui_notice = "Assessment and its progress history deleted."
                    st.rerun()
                else:
                    st.error("Could not delete assessment.")


# ---------------------------------------------------------------------------
# UI View 5: Progress Tracking & History
# ---------------------------------------------------------------------------
def render_progress_tracker(conn: sqlite3.Connection) -> None:
    st.subheader("Log progress")

    assessments = db.list_assessments(conn)
    if not assessments:
        st.info("No assessments to log progress for. Add assessments or load sample data first.")
        return

    # Assessment picker
    options = {f"{_assessment_label(a)} ({a['progress_pct']:.0f}%)": a["id"] for a in assessments}
    sel_label = st.selectbox("Assessment to update", options=list(options.keys()),
                             index=selection_index(options), key="progress_selection")
    sel_id = options[sel_label]
    current = db.get_assessment(conn, sel_id)

    if not current:
        return

    # Assessment status glance
    col_info1, col_info2, col_info3 = st.columns(3)
    total_spent = db.total_hours_spent(conn, sel_id)
    rem_h = current["estimated_hours"] * (1 - current["progress_pct"] / 100.0)

    col_info1.metric("Progress", f"{current['progress_pct']:.0f}%")
    col_info2.metric("Hours logged", f"{total_spent:.1f} hrs")
    col_info3.metric("Hours remaining", f"{rem_h:.1f} hrs", f"Est. {current['estimated_hours']}h")


    # Log new progress session
    st.subheader("Work session")
    with st.form(f"log_progress_form_{sel_id}"):
        col1, col2 = st.columns(2)
        with col1:
            hours_spent = st.number_input("Hours spent this session", min_value=0.25, max_value=80.0, value=2.0, step=0.5)
            note = st.text_input("Session notes", placeholder="e.g. Wrote Section 2 and debugged test suite")
        with col2:
            new_progress = st.slider(
                "Total progress after this session",
                min_value=0.0,
                max_value=100.0,
                value=min(100.0, float(current["progress_pct"]) + 10.0),
                step=5.0
            )
            mark_submitted = st.checkbox("Mark as Submitted", value=False)
            st.caption("Select this only when you have handed in the assessment.")

        submitted = st.form_submit_button("Record progress", type="primary")
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
                st.session_state.ui_notice = f"Logged {hours_spent:g} hours. Progress is now {new_progress:.0f}%."
                st.rerun()
            except Exception as exc:
                st.error(f"Failed to log progress: {exc}")


    # Session History Timeline
    st.subheader(f"History for [{current['module_code']}] {current['title']}")
    history = db.get_progress_history(conn, sel_id)

    if not history:
        st.write("No progress logged for this assessment yet.")
    else:
        df_hist = pd.DataFrame(history)
        df_hist = df_hist.rename(columns={
            "logged_at": "Logged at (UTC)",
            "hours_spent": "Hours spent",
            "progress_pct_after": "Progress after %",
            "note": "Note",
        })
        st.dataframe(
            df_hist[["Logged at (UTC)", "Hours spent", "Progress after %", "Note"]],
            width="stretch",
            hide_index=True,
            alt="Progress history for the selected assessment",
        )


# ---------------------------------------------------------------------------
# UI View 6: Plan My Week
# ---------------------------------------------------------------------------
def render_plan_my_week(conn: sqlite3.Connection, today: date) -> None:
    page_header("Planner", "A focused plan for the next seven days.")
    st.subheader(f"{today:%d %b} – {today + timedelta(days=6):%d %b %Y}")
    assessments = db.list_assessments(conn)
    outstanding = [a for a in assessments if a["status"] != "submitted"
                   and analytics.remaining_hours(a) > 0]
    if not outstanding:
        st.caption("No study work remains. Add an assessment to start planning.")
        st.page_link("app_pages/assessments.py", label="View assessments")
        return

    fixture_notice(assessments)
    rows = [to_analytics_row(a) for a in assessments]
    term_weeks = get_all_term_weeks()
    week_loads = analytics.weekly_workload(rows, term_weeks)
    starts = suggested_start_dates(rows, term_weeks)

    ctrl_col, btn_col = st.columns([3, 2], vertical_alignment="bottom")
    with ctrl_col:
        hours_available = st.slider(
            "Study hours available over the next 7 days",
            min_value=5.0, max_value=60.0, value=plan.DEFAULT_WEEKLY_HOURS, step=2.5,
            format="%.1f",
            help="Your realistic budget. The plan is asked to fit inside it.",
            key="plan_budget",
        )
    with btn_col:
        asked = st.button("Generate plan", type="primary", key="plan_run")

    plan_context = (today, repr(rows))
    if st.session_state.get("week_plan_context") != plan_context:
        st.session_state.week_plan = None
        st.session_state.week_plan_context = plan_context

    # Held in session state so a slider nudge or a rerun does not re-bill the API.
    if asked:
        with st.spinner("Preparing your weekly plan..."):
            try:
                st.session_state.week_plan = plan.plan_week(
                    rows, week_loads, today, hours_available=hours_available)
            except plan.PlanError as exc:
                st.session_state.week_plan = None
                st.error(f"Could not build a plan: {exc}")

    result = st.session_state.get("week_plan")
    if result is None:
        st.caption("Choose a realistic hours budget, then generate your priorities for the week.")
        return

    if result.hours_available != hours_available:
        st.caption(
            f"Showing a plan built for a {result.hours_available:g}h week. "
            "Press **Generate plan** again to replan for "
            f"{hours_available:g}h."
        )

    if result.used_fixtures:
        st.warning("Offline fixture plan — not Claude's advice. These priorities and hours are synthetic.")
        with st.expander("Plan source"):
            st.caption(result.reason or "Offline fixture replay")

    if not result.items:
        st.warning(
            ("No suggested work remains after matching this plan to your assessments."
             if result.unmatched else "The planner returned no study allocations for this week.")
            + (f" It referred to: {', '.join(result.unmatched)}." if result.unmatched else "")
        )
        return

    m1, m2, m3 = st.columns(3)
    m1.metric("Planned", f"{result.total_hours:.1f}h")
    m2.metric("Priorities", len(result.items))
    m3.metric("Available", f"{result.hours_available:g}h")
    st.progress(min(1.0, result.total_hours / result.hours_available),
                text=f"{result.total_hours:.1f} of {result.hours_available:g} hours allocated")
    st.subheader("Your priorities")


    if result.over_budget_hours:
        st.warning(
            f" This plan needs {result.total_hours:.1f} hours but you have "
            f"{result.hours_available:g}. The work does not fit in one week — consider a "
            "conversation with your module lead about priorities or extension options."
        )

    if result.unmatched:
        st.info(
            f"{len(result.unmatched)} suggestion(s) named work you are not tracking and "
            f"were left out: {', '.join(result.unmatched)}."
        )


    for item in result.items:
        row = next((r for r in rows if r["id"] == item.assessment_id), None)
        with st.container(border=False, gap="xsmall"):
            title_col, hours_col = st.columns([5, 1], vertical_alignment="center")
            title_col.markdown(f"**{item.priority}. {item.title}**")
            hours_col.markdown(f"**{item.hours:.1f}h**")
            if row is not None:
                risk = analytics.assess_risk(row, today, start_date=starts[row["id"]])
                st.caption(f"{item.module_code} · Due {row['due_date']:%d %b} · {row['progress_pct']:.0f}% complete"
                           + (" · At risk" if risk.at_risk else ""))
            st.write(item.reason)
    st.caption("Suggestions only. Record completed work in Assessments → Progress.")
    st.page_link("app_pages/assessments.py", label="Open assessments")


# ---------------------------------------------------------------------------
# Main Application Shell & Navigation
# ---------------------------------------------------------------------------
def selection_index(options: Mapping[str, int]) -> int:
    selected = st.session_state.get("selected_assessment")
    return next((i for i, value in enumerate(options.values()) if value == selected), 0)


def render_calendar_export(conn: sqlite3.Connection) -> None:
    rows = db.list_assessments(conn)
    genuine, fabricated = export.split_fabricated(rows)
    if genuine:
        try:
            st.sidebar.download_button("Export calendar", export.export_calendar(genuine),
                                       file_name="deadline-radar.ics", mime="text/calendar",
                                       icon=":material/download:", width="stretch")
        except (ValueError, OverflowError, sqlite3.Error) as exc:
            st.sidebar.warning(f"Calendar export unavailable: {exc}")
    if fabricated:
        st.sidebar.caption(f"Calendar excludes {len(fabricated)} invented fixture deadline(s).")
    if rows and not genuine:
        st.sidebar.caption("Nothing to export until genuine deadlines are added.")


def change_reference_date() -> None:
    st.session_state.reference_today = st.session_state.simulated_today_picker


def render_settings(conn: sqlite3.Connection) -> None:
    page_header("Settings", "Appearance, calendar preferences and demo data.")
    st.subheader("Appearance")
    st.caption("Light is the default. Choose Light, Dark or System in the app menu at the top right → Settings → Theme.")
    st.subheader("Reference date")
    st.date_input("Treat this date as today", value=st.session_state.reference_today,
                  key="simulated_today_picker", on_change=change_reference_date)
    st.caption("The demo uses 16 October 2026. This changes countdowns, risk and planning, not your deadlines.")
    st.subheader("Import and planning")
    forced = os.environ.get(extract.FIXTURES_ENV, "0").strip() == "1"
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    if forced or not has_key:
        st.warning("Offline fixture mode. Imports and plans are synthetic and labelled; imported fixture deadlines are excluded from calendar export.")
        st.caption("To enable Claude, configure ANTHROPIC_API_KEY in your local .env and disable DEADLINE_RADAR_FIXTURES.")
    else:
        st.caption("Claude API is configured. Any failed request falls back to clearly labelled fixtures.")
    st.subheader("Demo data")
    st.caption("Ten sample assessments across four modules. Loading them preserves your own assessments.")
    with st.container(horizontal=True):
        if st.button("Load demo data", key="side_seed"):
            count = seed_demo_data(conn)
            st.session_state.ui_notice = f"Loaded {count} demo assessments."
            st.rerun()
        if st.button("Remove demo data", key="side_clear_demo"):
            removed = clear_demo_data(conn)
            st.session_state.ui_notice = f"Removed {removed} demo assessments. Your own work is unchanged."
            st.rerun()
    with st.expander("Delete all data"):
        st.caption("Permanently removes every assessment and progress entry from this database.")
        confirmed = st.checkbox("I understand that all assessments and progress will be deleted", key="confirm_reset")
        if st.button("Delete all data", key="side_reset", disabled=not confirmed):
            with conn:
                conn.execute("DELETE FROM assessments")
            st.session_state.pop("week_plan", None)
            st.session_state.ui_notice = "All assessments and progress entries deleted."
            st.rerun()


def main() -> None:
    st.set_page_config(page_title="Deadline Radar", page_icon=":material/radar:",
                       layout="centered", initial_sidebar_state="expanded")
    st.session_state.setdefault("reference_today", REFERENCE_TODAY)
    st.sidebar.markdown("### Deadline Radar")
    st.sidebar.caption("UCL · Academic year 2026–27")
    pages = [
        st.Page("app_pages/overview.py", title="Overview", icon=":material/space_dashboard:", default=True),
        st.Page("app_pages/assessments.py", title="Assessments", icon=":material/checklist:"),
        st.Page("app_pages/planner.py", title="Planner", icon=":material/calendar_today:"),
        st.Page("app_pages/workload.py", title="Workload", icon=":material/bar_chart:"),
        st.Page("app_pages/import_handbooks.py", title="Import", icon=":material/upload_file:", url_path="import"),
        st.Page("app_pages/settings.py", title="Settings", icon=":material/settings:"),
    ]
    current = st.navigation(pages, position="sidebar")
    st.sidebar.caption(f"Reference date · {st.session_state.reference_today:%d %b %Y}")
    if os.environ.get(extract.FIXTURES_ENV, "0").strip() == "1" or not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        st.sidebar.caption("Offline fixtures")
    with closing(get_connection()) as conn:
        render_calendar_export(conn)
    show_notice()
    current.run()


if __name__ == "__main__":
    main()
