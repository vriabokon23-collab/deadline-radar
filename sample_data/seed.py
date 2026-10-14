"""Demo seed data for the Deadline Radar stage demo.

Builds a database that tells a story on the demo reference date
(``app.REFERENCE_TODAY``, Friday 16 October 2026):

- 10 assessments across 4 modules;
- a crunch week commencing Monday 30 November 2026, carrying roughly three
  times the remaining hours of any other week;
- exactly two assessments at risk, each for a *different* reason, so the
  dashboard can show both halves of the rule;
- progress history on five of them, including a submitted one;
- one low-confidence extraction whose handbook quote is genuinely ambiguous.

Run it with::

    .venv/bin/python -m sample_data.seed            # seed data/radar.db
    .venv/bin/python -m sample_data.seed --clear    # remove just the demo rows
    .venv/bin/python -m sample_data.seed --db /tmp/demo.db

Seeding is keyed on ``(module_code, title)``: a run removes the demo rows it
owns and re-inserts them, so running twice leaves the same state and any real
assessments the student entered are never touched.

``source`` is ``extracted``/``manual`` rather than ``fixture`` on purpose.
``fixture`` means "the offline fallback invented this row while posing as an
extraction", which the UI must warn about and keep out of the .ics file. Demo
rows arrive only when someone deliberately clicks Load Demo Data, so they are
not that kind of lie, and marking them ``fixture`` would empty the export.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import sys
from typing import Any, Iterable, Mapping

if __package__ in (None, ""):  # direct `python sample_data/seed.py` run
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from radar import db

# Keep in step with app.REFERENCE_TODAY: the numbers below are tuned to it.
DEMO_TODAY_ISO = "2026-10-16"
CRUNCH_WEEK_MONDAY_ISO = "2026-11-30"


DEMO_ASSESSMENTS: list[dict[str, Any]] = [
    # Submitted, and dated before the reference day: proves the dashboard keeps
    # finished work out of the countdown, the heatmap and the risk count.
    {
        "module_code": "COMP0012",
        "module_name": "Operating Systems",
        "title": "Scheduling Case Study",
        "type": "coursework",
        "due_date": "2026-10-09",
        "due_time": "16:00:00",
        "weighting_pct": 15.0,
        "word_count": 2000,
        "estimated_hours": 18.0,
        "status": "submitted",
        "progress_pct": 100.0,
        "source": "extracted",
        "confidence": "high",
        "source_quote": "Submission deadline: Friday 9 October 2026, 16:00.",
        "notes": "Submitted via Moodle a day early.",
    },
    # Risk 1 of 2 — flags on BOTH rules: progress has stalled at 5% and there
    # are 23.75h left against one week of capacity.
    {
        "module_code": "COMP0004",
        "module_name": "Algorithms and Data Structures",
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
        "notes": "Proof-heavy. One question attempted in three weeks.",
    },
    # Healthy counter-example: small, half done, plenty of runway.
    {
        "module_code": "COMP0010",
        "module_name": "Principles of Programming",
        "title": "Lab Exercises 1-4",
        "type": "problem_set",
        "due_date": "2026-10-30",
        "due_time": "12:00:00",
        "weighting_pct": 10.0,
        "word_count": None,
        "estimated_hours": 8.0,
        "status": "in_progress",
        "progress_pct": 50.0,
        "source": "manual",
        "confidence": "high",
        "source_quote": "Added by hand from the module Moodle page.",
        "notes": "Two of four labs signed off.",
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
        "notes": "Context and component diagrams done; deployment view left.",
    },
    # The low-confidence row. "Term 1 Week 10" is the ambiguity A2 exists for:
    # 2026-11-30 counting reading week, 2026-12-07 skipping it.
    {
        "module_code": "COMP0004",
        "module_name": "Algorithms and Data Structures",
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
        "source_quote": "Problem Set 2 is due in Term 1 Week 10.",
        "notes": (
            "Week 10 is 30 November counting reading week, 7 December skipping it. "
            "Resolved to the earlier date; confirm with the module lead."
        ),
    },
    # Risk 2 of 2 — flags on the HOURS rule only. 70h left, 45 days of runway,
    # and the suggested start date has only just passed, so progress is not yet
    # judged behind. Different reason, different conversation.
    {
        "module_code": "COMP0010",
        "module_name": "Principles of Programming",
        "title": "Interpreter Implementation Project",
        "type": "coursework",
        "due_date": "2026-11-30",
        "due_time": "12:00:00",
        "weighting_pct": 40.0,
        "word_count": 3000,
        "estimated_hours": 70.0,
        "status": "not_started",
        "progress_pct": 0.0,
        "source": "manual",
        "confidence": "high",
        "source_quote": "Added by hand from the printed syllabus.",
        "notes": "Lexer, parser, AST and a bytecode VM. The big one.",
    },
    {
        "module_code": "COMP0012",
        "module_name": "Operating Systems",
        "title": "Concurrency Kernel Lab",
        "type": "coursework",
        "due_date": "2026-11-30",
        "due_time": "16:00:00",
        "weighting_pct": 35.0,
        "word_count": None,
        "estimated_hours": 25.0,
        "status": "not_started",
        "progress_pct": 0.0,
        "source": "extracted",
        "confidence": "medium",
        "source_quote": "The kernel lab is handed in at the end of week 10.",
        "notes": "Thread synchronisation primitives.",
    },
    {
        "module_code": "COMP0012",
        "module_name": "Operating Systems",
        "title": "Systems Design Report",
        "type": "essay",
        "due_date": "2026-12-08",
        "due_time": "16:00:00",
        "weighting_pct": 25.0,
        "word_count": 1800,
        "estimated_hours": 12.0,
        "status": "not_started",
        "progress_pct": 0.0,
        "source": "extracted",
        "confidence": "high",
        "source_quote": "Your report is due on Tuesday 8 December 2026 by 16:00.",
        "notes": "Compare two schedulers from the lab.",
    },
    {
        "module_code": "COMP0004",
        "module_name": "Algorithms and Data Structures",
        "title": "Term 1 Class Test",
        "type": "exam",
        "due_date": "2026-12-11",
        "due_time": "10:00:00",
        "weighting_pct": 25.0,
        "word_count": None,
        "estimated_hours": 15.0,
        "status": "not_started",
        "progress_pct": 0.0,
        "source": "extracted",
        "confidence": "high",
        "source_quote": "An in-class test will be held on Friday 11 December 2026.",
        "notes": "Covers everything up to network flow.",
    },
    {
        "module_code": "COMP0002",
        "module_name": "Software Engineering",
        "title": "Final Team Project and Peer Review",
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
        "source_quote": "Final group deliverables are due on Monday 14 December.",
        "notes": "Word count is for the whole team's report.",
    },
]


# (module_code, title) -> ordered (hours_spent, progress_pct_after, note).
# The last entry must match that row's progress_pct, since log_progress writes
# progress_pct_after straight onto the parent row.
DEMO_PROGRESS: dict[tuple[str, str], list[tuple[float, float, str]]] = {
    ("COMP0012", "Scheduling Case Study"): [
        (6.0, 40.0, "First draft of the comparison table"),
        (12.0, 100.0, "Proofread and submitted on Moodle"),
    ],
    ("COMP0004", "Problem Set 1 (Graph Proofs)"): [
        (1.5, 5.0, "Read the questions, sketched Q1(a)"),
    ],
    ("COMP0010", "Lab Exercises 1-4"): [
        (2.0, 25.0, "Lab 1 signed off"),
        (1.5, 50.0, "Lab 2 signed off"),
    ],
    ("COMP0002", "Coursework 1: Architecture Specification"): [
        (5.0, 30.0, "Drafted the system context diagram"),
        (8.0, 60.0, "Wrote up the component architecture"),
    ],
    ("COMP0002", "Final Team Project and Peer Review"): [
        (10.0, 20.0, "Repo scaffolded, CI green, work split four ways"),
    ],
}


def demo_keys() -> set[tuple[str, str]]:
    """The ``(module_code, title)`` pairs this script owns."""
    return {(row["module_code"], row["title"]) for row in DEMO_ASSESSMENTS}


def clear_demo_data(conn: sqlite3.Connection) -> int:
    """Delete only the demo rows, leaving real assessments alone.

    Returns the number of assessments removed. Progress history goes with them
    through the foreign key cascade.
    """
    owned = demo_keys()
    removed = 0
    for row in db.list_assessments(conn):
        if (row["module_code"], row["title"]) in owned:
            if db.delete_assessment(conn, row["id"]):
                removed += 1
    return removed


def seed_demo_data(conn: sqlite3.Connection) -> int:
    """Replace the demo rows with a fresh copy and return how many were written."""
    clear_demo_data(conn)
    db.insert_assessments(conn, DEMO_ASSESSMENTS)

    ids = {
        (row["module_code"], row["title"]): row["id"]
        for row in db.list_assessments(conn)
    }
    for key, entries in DEMO_PROGRESS.items():
        for hours_spent, progress_after, note in entries:
            db.log_progress(conn, ids[key], hours_spent, progress_after, note)

    return len(DEMO_ASSESSMENTS)


def _summarise(rows: Iterable[Mapping[str, Any]]) -> str:
    rows = list(rows)
    modules = sorted({row["module_code"] for row in rows})
    return f"{len(rows)} assessments across {len(modules)} modules ({', '.join(modules)})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=db.DEFAULT_DB_PATH,
                        help=f"database path (default: {db.DEFAULT_DB_PATH})")
    parser.add_argument("--clear", action="store_true",
                        help="remove the demo rows instead of writing them")
    args = parser.parse_args(argv)

    args.db.parent.mkdir(parents=True, exist_ok=True)
    conn = db.connect(args.db)
    try:
        if args.clear:
            print(f"removed {clear_demo_data(conn)} demo assessments from {args.db}")
        else:
            seed_demo_data(conn)
            print(f"seeded {_summarise(DEMO_ASSESSMENTS)} into {args.db}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
