# Deadline Radar

UCL assessment tracker built for the Claude Campus Hackathon (Better Student Life track, demo Friday 16 October 2026). Students upload module handbooks, Claude extracts every assessment, the student confirms or edits the results, then tracks progress against deadlines. The app surfaces crunch weeks and assessments at risk of slipping.

## Stack

- Python 3.11+, Streamlit for the UI
- SQLite via the standard library `sqlite3` module (single file `data/radar.db`, created on first run)
- Anthropic Python SDK, model `claude-sonnet-5-5`, PDFs sent as document content blocks
- Plotly for charts, `icalendar` for .ics export, `python-dotenv` for the API key
- No other frameworks. Keep dependencies in `requirements.txt`.

## Project layout

```
app.py              Streamlit entry point and page routing
radar/db.py         Schema, CRUD, progress logging
radar/extract.py    Claude extraction: PDF in, validated list of assessments out
radar/calendar.py   UCL term calendar, week-number to date mapping
radar/analytics.py  Weekly workload, risk scoring
radar/export.py     .ics generation
tests/              pytest tests, including fixtures of extracted JSON
sample_data/        Demo handbooks and seed data (no real personal data)
```

## Data model

Table `assessments`: id, module_code, module_name, title, type (essay, exam, coursework, presentation, problem_set, group_project, other), due_date (ISO date), due_time (nullable), weighting_pct (nullable), word_count (nullable), estimated_hours, status (not_started, in_progress, submitted), progress_pct (0 to 100), source (extracted, manual or fixture), confidence (high, medium, low), source_quote (the handbook text the date came from), notes, created_at, updated_at.

Table `progress_log`: id, assessment_id, logged_at, hours_spent, progress_pct_after, note.

`source` records provenance and is never reassigned at save time:

- `extracted` — Claude read the uploaded PDF and returned this row.
- `manual` — the student typed it in, either on the Add form or as a new row in the review editor.
- `fixture` — the app replayed a saved response from `tests/fixtures/*.json` via `radar.extract.load_fixture`, because `ANTHROPIC_API_KEY` was missing, `DEADLINE_RADAR_FIXTURES=1` was set, or both API attempts failed. Those files are currently hand-written rather than captured, so the dates, weightings and quotes are invented.

`extracted` and `manual` are the only values a trustworthy row can have. Fixture rows must stay labelled all the way into the database so the dashboard can flag them; never relabel one as `extracted`. `source` is read-only in the review editor and is preserved by the edit form. SQLite enforces the enum, and machine sources (`extracted`, `fixture`) must carry a non-empty `source_quote`. Because the schema uses `CREATE TABLE IF NOT EXISTS`, widening this enum needs a table rebuild — see `_migrate_source_check` in `radar/db.py`. `db.FABRICATED_SOURCE` holds the `fixture` string for the `radar/` modules that branch on provenance.

Every consumer of a fixture row must either exclude it or say what it is:

- **.ics export excludes them.** `export_calendar` drops them and `split_fabricated` (both in `radar/export.py`) gives the UI the counts. Importing an .ics is a one-way copy: the event outlives the row it came from, keeps firing reminders, and sits in a calendar this app can never reach, so a marker in the SUMMARY is not enough. The sidebar always reports how many rows were left out, and offers no download at all when every row is fabricated — an export must never be quietly empty.
- **The workload heatmap includes their hours but breaks them out.** `WeekLoad.fabricated_hours` and `fabricated_count` are a breakdown of the totals, not a deduction; bars containing invented hours are hatched and labelled 🧪, the hover shows the split, and a crunch week says how much of itself is real. Omitting them would make demo mode look broken and hide hours the student can see on the dashboard anyway.
- **Risk scoring ignores provenance.** The rule is about progress and time, and each card already carries a `🧪 FIXTURE` badge beside its risk banner, so a fixture row is scored like any other; the dashboard's At Risk metric notes how many flags rest on invented figures.

## UCL 2026/27 calendar (hardcode in radar/calendar.py)

- Term 1: Monday 28 September 2026 to Friday 18 December 2026, reading week commencing Monday 9 November 2026
- Term 2: Monday 11 January 2027 to Wednesday 24 March 2027, reading week commencing Monday 15 February 2027
- Term 3: Monday 26 April 2027 to Friday 11 June 2027

Handbooks refer to deadlines as "Week 7", "Term 1 Week 10", or by UCL timetable week numbers, which differ from term week numbers. Never guess silently: if the numbering scheme is ambiguous, resolve to the most likely date, set confidence to low, and keep the source_quote so the user can check it.

## Extraction rules

- One Claude call per uploaded PDF. Ask for JSON only, validate against a schema, retry once on invalid JSON, then surface an error in the UI. `radar.extract.MAX_ATTEMPTS` is 2 and there is no third attempt; the model is `MODEL` and is never silently swapped for an older one.
- Every extracted assessment must carry a source_quote and a confidence level. A row missing either, or carrying an unknown `type` or `confidence`, is rejected rather than coerced — that costs one of the two attempts.
- Claude reports the date phrase it found and never does the arithmetic: `radar/calendar.py` maps `Term 1 Week 10` to a date, and a low-confidence resolution drags the assessment down to low. A phrase the calendar cannot parse is quarantined on `UNRESOLVED_DUE_DATE` with confidence low and a note naming the phrase, because a handbook can genuinely leave a date open.
- Extracted rows go to a review table first. Nothing is written to the database until the user clicks Confirm.
- estimated_hours: Claude proposes a figure from type, weighting and word count; `propose_estimated_hours` fills in a rough figure when it omits one, and the user can override either.
- The offline fixture fallback must never pass itself off as a real extraction. `ExtractionResult` stamps `source` on every row it carries, the review gate badges the queue, and Confirm persists that label verbatim.

## Core features (MVP, must work by Thursday night)

1. Upload one or more handbook PDFs, review and edit extracted rows, confirm into the database.
2. Add, edit and delete assessments manually.
3. Dashboard: upcoming deadlines sorted by date, days remaining, progress bar per assessment.
4. Progress logging: update progress_pct and hours spent; history stored in progress_log.
5. Workload heatmap: remaining estimated hours bucketed by week across the term, with reading weeks marked and fabricated fixture hours hatched and broken out.
6. Risk flag: an assessment is at risk when actual progress trails expected progress (linear from a suggested start date to the due date) by more than 20 percentage points, or when remaining hours exceed available weeks at 10 hours per week per assessment.
7. Export all genuine deadlines as a .ics file; fabricated fixture rows are excluded and the sidebar says how many.

## Stretch (only after MVP is stable)

- "Plan my week" button: Claude reads current state and proposes a prioritised weekly plan.
- Suggested start date per assessment, backward-planned from the due date and the workload in surrounding weeks.

## Conventions

- Read the API key from `ANTHROPIC_API_KEY` in `.env`. Never hardcode or commit keys. `.env` and `data/` stay in `.gitignore`.
- Small, focused functions with type hints. Business logic lives in `radar/`, never inside Streamlit callbacks.
- Write a pytest test for calendar mapping and risk scoring before wiring them into the UI.
- After each working feature, run the tests and commit with a clear message.
- Run locally with `streamlit run app.py`.
- Build work is broken into numbered sessions in `SESSIONS.md`. When asked to "begin session Ax", read that file and work only on that session's scope.
