# Integration notes — radar/analytics.py

Written by the integrator while `radar/calendar.py` (A2) and `radar/db.py` (A3) did
not yet exist on any branch. This is the contract the rest of the app needs to honor
for analytics to wire in without changes. Update this file, don't let it drift.

**Status: wired up.** Both modules now exist and `app.py` bridges them to analytics
exactly as anticipated below — see `app.get_all_term_weeks()` (builds
`list[analytics.TermWeek]` from `calendar.TERMS` / `calendar.term_weeks` /
`calendar.is_reading_week`, since `radar/calendar.py` itself exposes dates and a
separate `Term` dataclass rather than `analytics.TermWeek` directly) and
`app.to_analytics_row()` (converts a `sqlite3.Row` into the plain dict shape below).
`tests/test_frontend.py` exercises the full `db -> analytics -> calendar` path,
closing the "Follow-up" item that used to be at the bottom of this file.

## What's here

- `radar/analytics.py` — weekly workload bucketing (heatmap data) and risk scoring,
  pure functions, no I/O, no imports of `radar.db` or `radar.calendar`.
- `tests/test_analytics.py` — covers every A10 test case from SESSIONS.md.
- `requirements.txt` — provisional dependency pins (see caveat below).

## Why analytics.py doesn't import radar.calendar or radar.db

Both were unbuilt when this work started. Rather than block on them, analytics.py
defines its own minimal `TermWeek` dataclass (`term`, `week_number`, `start_date`,
`is_reading_week`) and takes plain dict-like assessment rows. This keeps the module
fully unit-testable right now and gives the backend dev a concrete target instead of
a guess.

## Interface radar.calendar needs to expose

`analytics.weekly_workload(assessments, term_weeks)` expects `term_weeks` to be a
sequence of objects with these four attributes (a list of `analytics.TermWeek`, or
any object duck-typing the same attribute names):

```
term: str              # e.g. "T1", "T2", "T3"
week_number: int       # 1-indexed within the term
start_date: date        # the Monday of that week
is_reading_week: bool
```

Suggested wiring once `radar/calendar.py` exists: either have it return
`list[analytics.TermWeek]` directly, or return its own type with matching field
names — `weekly_workload` only ever accesses `.term`, `.week_number`, `.start_date`,
`.is_reading_week`, so attribute-compatible duck typing is enough, no need for
`radar.calendar` to import from `radar.analytics`.

`app.py` is expected to call something like:

```python
analytics.weekly_workload(assessments, calendar.all_term_weeks())
```

If `radar/calendar.py` lands with a different function name or return shape, either
rename to match the above or add a one-line adapter in `app.py` — don't change
`analytics.TermWeek`'s field names without rerunning `tests/test_analytics.py`.

## Interface radar.db / app.py needs to provide

Every `analytics` function takes assessment rows as `Mapping[str, Any]` (a plain
dict or `sqlite3.Row`) with these keys, in these Python types — not raw strings:

```
due_date: datetime.date   # convert on the way out of db.py, not a string
estimated_hours: float
progress_pct: float | int  # 0-100
status: str                # "not_started" | "in_progress" | "submitted"
source: str                # optional; only "fixture" changes anything
```

`assess_risk` reads the first four fields; `weekly_workload` also reads `source` via
`.get`, so it can report the fabricated share of each week in
`WeekLoad.fabricated_hours` and `fabricated_count`. A row with no `source` key counts
as genuine, so pass a dict (which is what `db.list_assessments` returns). Both
ignore `weighting_pct` and `word_count` entirely — CLAUDE.md only mentions those for
the `estimated_hours` proposal step in extraction, not for risk scoring.

## Decisions made here that the team should sanity-check

1. **Suggested start date default.** CLAUDE.md's risk rule needs a "suggested start
   date," which A14 (stretch) backward-plans properly. Until then, `assess_risk`
   defaults to `due_date - (estimated_hours / hours_per_week)` weeks via
   `default_start_date()`, but accepts an explicit `start_date=` override. When A14
   ships, pass its result in — keep the default as a fallback for assessments A14
   hasn't computed yet, don't delete it.
2. **Hours rule doesn't discount reading weeks.** "Remaining hours exceed available
   weeks at 10h/week" is computed from literal calendar days between today and the
   due date, with no reduction for reading weeks even though students realistically
   get less assessment work done then. Flagging as a product question, not resolved
   silently.
3. **Submitted assessments are dropped from the workload heatmap entirely**, not
   just zeroed — on the assumption that submitted work isn't "remaining load" even
   if `progress_pct` was never updated to 100. Confirm this matches what the
   dashboard (A8) wants to show.
4. **Due dates between terms.** `week_containing()` has no "gap" state: a date after
   a term's last modeled week gets attributed to that last week rather than returned
   as unbucketed. Only matters for a due date that falls in an inter-term break
   (e.g. over the winter holidays) — worth revisiting once real sample data exists.
5. **A due date before the first modeled week is dropped, not bucketed.** This is
   the asymmetric case of #4: there's no earlier week to attribute it to, so
   `week_containing` returns `None` and `weekly_workload` silently excludes the
   row's hours from every bucket (see
   `test_weekly_workload_silently_drops_a_due_date_before_the_first_week`). In
   practice this only bites if `get_all_term_weeks()` is called with a narrower
   window than the data actually spans — worth a guard in `app.py` if that ever
   happens, but out of scope for `analytics.py` itself.

## Dependency management caveat

Hackathon reliability review (14 October 2026): the frontend now requires
Streamlit 1.65+, matching the verified local runtime and its native upload tests.
The planner uses the same backward-planned start dates as the dashboard, rather
than the flat fallback when term weeks are supplied. SDK retries are disabled
so extraction and planning each retain the application's two-attempt limit;
requests use a 30-second timeout. Live account access is still a rehearsal check.

`requirements.txt` pins are provisional major-version ranges, written without a real
`pip install` against the live `anthropic` SDK. A1 owns settling the exact
`claude-sonnet-5-5` vs. fallback model ID empirically — re-pin exact versions once
that smoke test runs, rather than trusting these ranges blindly.

## Follow-up once calendar.py and db.py exist

- ~~Add an integration test (not a unit test) exercising
  `app.py` → `db.py` → `analytics.py` → `calendar.py` end to end with real data.~~
  Done: `tests/test_frontend.py::test_workload_heatmap_and_crunch`,
  `test_risk_indicators` and `test_heatmap_separates_fabricated_hours` seed a real
  SQLite db, round-trip through `to_analytics_row`/`get_all_term_weeks`, and assert
  on `weekly_workload`/`assess_risk` output.
- ~~Re-check assumption 3 above against the actual dashboard summary line from A8.~~
  Confirmed: `app.render_dashboard` and `app.render_workload_heatmap` both treat
  submitted assessments as fully excluded from remaining-hours totals (see the
  "Submitted" metric being counted separately from "At Risk"/heatmap totals in
  `app.py`), matching assumption 3.
- Assumption 4 (no inter-term gap handling in `week_containing`) is still
  unaddressed — no caller currently passes a due date that falls in a break
  between terms, so it hasn't mattered in practice. Worth a test once a handbook
  with a holiday deadline shows up in `sample_data/`.
