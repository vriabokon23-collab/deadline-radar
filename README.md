# Deadline Radar

UCL assessment tracker. Upload a module handbook, Claude extracts every
assessment, you confirm or correct it, then track progress against the
deadlines. The app surfaces crunch weeks and work at risk of slipping.

Requires Python 3.11+. Install, test, run:

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pytest
.venv/bin/python -m streamlit run app.py
```

Extraction needs `ANTHROPIC_API_KEY` in `.env`. Without a key, set
`DEADLINE_RADAR_FIXTURES=1` to replay the recorded extractions in
`tests/fixtures/` instead — the demo below runs end to end either way.

## Three-minute demo script

Load the story first: **🌱 Load Demo UCL Data** in the sidebar seeds ten
assessments across four modules. Re-seeding replaces those rows rather than
duplicating them, and **🧹 Remove Demo Data Only** takes them away again
without touching anything you entered yourself. The seed is tuned to
`app.REFERENCE_TODAY` (16 October 2026); move the two together or the
narrative drifts.

**0:00 — 📊 Dashboard. The problem.** Ten assessments, two flagged at risk,
two due inside a fortnight. Open the risk banner on *Problem Set 1 (Graph
Proofs)*: progress at 5% against 61% expected, and 23.8 hours of work left
with one week to do it in. Contrast *Interpreter Implementation Project* —
untouched but not yet late, flagged purely because 70 hours will not fit in
the weeks remaining. Two different ways to be in trouble, caught by the same
rule.

**0:45 — 📈 Workload Heatmap.** Week commencing 30 November carries three
deadlines and 115 hours, nearly three times the next worst week and far over
the 20 hours-per-week line. That week is the thing you plan around, and
nothing in the raw handbooks tells you it exists.

**1:15 — 📄 Upload & Review.** Upload
`sample_data/ANTH0045_digital_anthropology.pdf`. Three assessments come back,
each carrying the handbook sentence its date was read from and a confidence
level. *Seminar presentation* is flagged **low**: the handbook only says
"presentations run in the second half of term", so the date is a placeholder
and the note says so in as many words. The app shows its guess instead of
hiding it.

**2:00 — Review, then confirm.** Fix that placeholder date in the review
table, select and delete a row you do not want, then hit **✅ Confirm & Save
to Radar**. Only now does anything reach the database. **Nothing Claude
extracts is written until you confirm it, because a wrong deadline you trust
is worse than no deadline at all.**

**2:30 — ⏱️ Progress Tracking, then export.** Log hours against the row you
just confirmed; the dashboard, the heatmap and the risk flags all move
together. Finish on **📥 Export Calendar (.ics)** in the sidebar and the
whole term lands in your real calendar.

Offline rehearsal: `DEADLINE_RADAR_FIXTURES=1 .venv/bin/python -m streamlit
run app.py`. Fixture-sourced rows are marked as such and are deliberately
withheld from the `.ics`, so a demo never exports an assessment Claude did
not really read.

## Backend reference

### Database and export

```python
from contextlib import closing
from radar import db
from radar.export import export_calendar

with closing(db.connect()) as conn:  # defaults to ignored data/radar.db
    assessment_id = db.insert_assessment(conn, {
        "module_code": "COMP0001", "title": "Essay", "type": "essay",
        "due_date": "2026-12-04", "estimated_hours": 20,
    })
    db.log_progress(conn, assessment_id, 2.5, 25, "Outline complete")
    deadlines = db.list_assessments(conn)
    ics_bytes = export_calendar(deadlines)
```

Use `insert_assessments(conn, rows)` only after review is confirmed; the whole
batch rolls back if any row fails. Read with `get_assessment`, `list_assessments`,
`get_progress_history` (newest first) and `total_hours_spent`. Edit with
`update_assessment(conn, id, changes)` and delete with `delete_assessment(conn, id)`;
deletion cascades to progress history. Reads return dictionaries; insertions return
IDs, missing gets return `None`, missing deletes return `False`, and missing
updates or progress logs raise `KeyError`. Invalid input raises `ValueError` or
`sqlite3.IntegrityError`. Mutations commit or roll back their transaction; do not
combine them with unrelated pending SQL. Set `status` explicitly: reaching 100%
progress does not automatically mean submitted.

The exporter returns `.ics` bytes for a download named `deadline-radar.ics`.
It includes submitted work, stable IDs, source quotes and context, but never
fabricated fixture rows — use `split_fabricated` to report what was left out.
Null due times produce all-day events; local times produce Europe/London
deadlines with embedded timezone definitions. Missing or ambiguous times at
daylight-saving transitions raise an error so the deadline can be clarified.

### Week references

`week_to_date(term, week_number, scheme)` accepts `calendar` (reading weeks
included), `teaching` (reading weeks skipped), or `timetable` (academic-year
numbering). `date_to_week(date)` returns `(term, calendar_week)` or `None` outside
term dates. `term_weeks(term=None)` supplies ordered Mondays, and
`is_reading_week(date)` identifies reading-week buckets.

`resolve_week_reference(raw, term=None, scheme=None)` returns a `WeekResolution`
with `date`, `confidence`, `source_quote`, `reason` and `alternatives`. Supported
phrases include `Term 1 Week 10`, `Week 7`, `Term 1 teaching week 10`,
`UCL timetable week 14` and `week commencing 9 November`. Omitted schemes prefer
calendar numbering with low confidence; omitted terms default to Term 1 with
low confidence. Explicit timetable weeks infer the term. Unsupported references,
conflicts and out-of-term weeks raise `ValueError`. Preserve the returned quote
and confidence when integrating extraction.

UCL timetable week 1 starts 31 August 2026, verified against the
[official academic calendar](https://www.ucl.ac.uk/srs/sites/srs/files/ucl_calendar_2026_27v2.pdf).

