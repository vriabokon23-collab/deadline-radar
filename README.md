# Deadline Radar backend

Requires Python 3.11+. Install and test:

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pytest
```

`requirements.txt` covers these backend modules and tests. UI and extraction
dependencies can be added by their owners when those modules land.

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
It includes submitted work, stable IDs, source quotes and context. Null due times
produce all-day events; local times produce Europe/London deadlines with embedded
timezone definitions. Missing or ambiguous times at daylight-saving transitions
raise an error so the deadline can be clarified.

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

