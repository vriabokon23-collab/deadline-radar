# Deadline Radar — build sessions

Work plan for the Claude Campus Hackathon demo on **Friday 16 October 2026**.
Written Monday 12 October 2026. Four build days: Mon, Tue, Wed, Thu.

## How to use this file

Say **"begin session A5"** in chat. Claude reads this file, finds that session, and
works only on that session's scope. Report bugs in chat as they appear.

Rules that apply to every session:

- Read `CLAUDE.md` first. It is the source of truth for stack, schema and conventions.
  If a session here contradicts `CLAUDE.md`, `CLAUDE.md` wins — say so rather than guessing.
- Business logic goes in `radar/`, never inside a Streamlit callback.
- Type hints on every function. Small functions.
- Run `pytest` before finishing. A session is not done while tests are red.
- Finish each session with one commit using the message given. Do not push.
- Stay inside the session's scope. If you spot work that belongs to a later session,
  note it in chat instead of doing it.

## Sequencing

| Day | Sessions | Theme |
|-----|----------|-------|
| Mon 12 Oct | A1, A2, A3 | Scaffold, calendar, database |
| Tue 13 Oct | A4, A5, A6 | Sample PDFs, extraction, review and confirm |
| Wed 14 Oct | A7, A8, A9, A10 | Manual CRUD, dashboard, progress, analytics |
| Thu 15 Oct | A11, A12 | Export, seed data, rehearsal, freeze |
| Thu evening | A13, A14 | Stretch only if A1–A12 are all green |

A1–A12 are the MVP and must all land. A13 and A14 are optional.

## Known risks

1. **Model ID.** `CLAUDE.md` specifies `claude-sonnet-5-5`. This has not been verified
   against the API. If it does not resolve, extraction fails and there is no demo.
   A1 settles this empirically before anything depends on it.
2. **No API key yet.** No `.env` exists and `ANTHROPIC_API_KEY` is not in the shell.
   Also handled in A1.
3. **Live API call on stage.** A network or rate-limit failure during the demo ends the
   demo. A5 builds a fixture replay path so the whole flow can run offline, and A12
   rehearses that path.
4. **Empty dashboard demos badly.** Heatmap and risk flags need populated data to mean
   anything. A12 seeds a database that deliberately shows a crunch week and at-risk work.

---

# A1 — Scaffold and environment

**Day:** Mon · **Depends on:** nothing

**Goal:** a runnable skeleton, and a definite answer on the model ID.

**Files:** `requirements.txt`, `.env.example`, `radar/__init__.py`, `radar/*.py` (stubs),
`app.py`, `tests/__init__.py`, `.gitignore`

**Steps**

1. Create a venv at `.venv` and write `requirements.txt`: `streamlit`, `anthropic`,
   `plotly`, `icalendar`, `python-dotenv`, `pytest`. Pin major versions. Install.
2. Confirm `.gitignore` already covers `.env`, `data/` and `.venv/`. Add any that are missing.
3. Write `.env.example` with `ANTHROPIC_API_KEY=` and nothing else. Ask the user to create
   the real `.env`. Never commit it.
4. Create empty stub modules for `db`, `extract`, `calendar`, `analytics`, `export` under
   `radar/`, each with a module docstring only.
5. `app.py`: minimal Streamlit page with the title and a placeholder, so `streamlit run app.py` works.
6. **Settle the model ID.** Once the key is in `.env`, make the smallest possible API call
   with `claude-sonnet-5-5`. If it 404s, try `claude-sonnet-4-6`. Record whichever works as
   a single `MODEL` constant in `radar/extract.py`. Report in chat which one resolved — do
   not quietly substitute a different model.

**Done when:** `streamlit run app.py` serves a page, `pytest` exits cleanly with no tests
collected, and the working model ID is recorded in `radar/extract.py`.

**Commit:** `Scaffold project structure and pin dependencies`

---

# A2 — UCL term calendar

**Day:** Mon · **Depends on:** A1

**Goal:** turn handbook week references into real dates, honestly flagging ambiguity.

**Files:** `radar/calendar.py`, `tests/test_calendar.py`

Write the tests first — `CLAUDE.md` requires it for this module.

**Term data (verified Monday dates, all confirmed to be Mondays)**

- Term 1: 2026-09-28 to 2026-12-18, 12 weeks, reading week is **week 7** (2026-11-09)
- Term 2: 2027-01-11 to 2027-03-24, 11 weeks, reading week is **week 6** (2027-02-15)
- Term 3: 2027-04-26 to 2027-06-11, 7 weeks, no reading week

**The ambiguity that matters.** Handbooks say "Week 10" without stating whether reading
week counts. For Term 1, "Week 10" is either 2026-11-30 (calendar weeks, reading week
counted) or 2026-12-07 (teaching weeks, reading week skipped). Resolve to the most likely
date, set confidence to `low`, and never discard the source quote. Same pattern applies to
UCL timetable week numbers, which are offset from term week numbers.

**Steps**

1. Model terms as a hardcoded structure with start, end and reading week.
2. `week_to_date(term, week_number, scheme)` returning the Monday of that week.
3. A resolver that takes a raw string such as `"Term 1 Week 10"`, `"Week 7"` or
   `"week commencing 9 November"` and returns a date plus a confidence level.
4. Helpers the later analytics work needs: `date_to_week(d)`, `is_reading_week(d)`,
   and an ordered list of term weeks for bucketing.

**Test cases to cover:** Term 1 week 1 is 2026-09-28; Term 1 week 7 is the reading week;
both readings of Term 1 week 10 (2026-11-30 and 2026-12-07) with the ambiguous input
returning low confidence; Term 2 week 6 is the reading week; a week number past the end of
term raises rather than returning a silently wrong date; round-tripping a date through
`date_to_week` and back.

**Done when:** `pytest tests/test_calendar.py` is green and every ambiguous input returns
a confidence level rather than a bare date.

**Commit:** `Add UCL term calendar with week resolution and tests`

---

# A3 — Database layer

**Day:** Mon · **Depends on:** A1

**Goal:** schema and CRUD, built to the `CLAUDE.md` data model exactly.

**Files:** `radar/db.py`, `tests/test_db.py`

**Steps**

1. Schema for `assessments` and `progress_log` with the exact columns listed in
   `CLAUDE.md`. Use CHECK constraints for the enum-like columns (`type`, `status`,
   `source`, `confidence`) so bad extraction output cannot land in the database.
2. `progress_log.assessment_id` is a foreign key to `assessments.id` with cascade delete.
   Remember `PRAGMA foreign_keys = ON` per connection — sqlite3 does not enable it by default.
3. Connection helper that creates `data/radar.db` and applies the schema idempotently on
   first run. Accept a path argument so tests can use a temporary file.
4. CRUD: insert one, insert many (for a confirmed extraction batch), list, get, update,
   delete. `updated_at` maintained on every update.
5. `log_progress(assessment_id, hours_spent, progress_pct_after, note)` writes to
   `progress_log` and updates the parent row's `progress_pct` in one transaction.

**Test cases:** schema applies twice without error; enum constraints reject a bad `type`
and a bad `confidence`; deleting an assessment removes its progress rows; `log_progress`
updates both tables; `updated_at` actually changes on update.

**Done when:** `pytest tests/test_db.py` is green and no test writes to `data/radar.db`.

**Commit:** `Add SQLite schema, CRUD and progress logging with tests`

---

# A4 — Sample handbooks

**Day:** Tue · **Depends on:** A1

**Goal:** PDFs to extract from, including deliberately awkward ones.

**Files:** `sample_data/*.pdf`, `sample_data/README.md`

**Ask the user first:** do they have real UCL handbook PDFs? Real ones expose messy
phrasing that makes the extraction genuinely impressive. If not, generate synthetic ones.

**Steps**

1. Produce three handbooks, each a few pages of realistic prose with an assessment
   section — not a bare table. Invent module codes; no real personal data.
2. Spread the date phrasings across them so extraction has to work for all of these:
   an explicit date ("submit by 4 December 2026, 16:00"); a plain term week
   ("due in Week 5"); the ambiguous form ("Term 1 Week 10"); a UCL timetable week number;
   and a relative reference ("two weeks after the reading week").
3. Cover a range of assessment types and include weightings and word counts for some
   but not all, so the nullable columns get exercised.
4. Make the third handbook the hard case: an assessment mentioned only in passing in a
   paragraph, and one date that is genuinely unresolvable. That is what `confidence: low`
   exists for, and it is the most honest thing to show a judge.
5. `sample_data/README.md` lists what each file contains and the expected extraction count,
   so later sessions can check extraction against a known answer.

**Done when:** three PDFs open correctly and the README states the expected assessment
count per file.

**Commit:** `Add sample handbook PDFs covering varied deadline phrasing`

---

# A5 — Claude extraction

**Day:** Tue · **Depends on:** A1, A2, A4

**Goal:** PDF in, validated assessment list out. The centrepiece of the demo.

**Files:** `radar/extract.py`, `tests/test_extract.py`, `tests/fixtures/*.json`

**Steps**

1. One API call per PDF, sent as a document content block (not pasted text).
2. Prompt asks for JSON only, matching the `assessments` columns. Every item must carry a
   `source_quote` taken verbatim from the handbook and a `confidence` of high, medium or low.
3. Validate the response against a schema: required keys present, `type` and `confidence`
   in their allowed sets, dates parseable as ISO. On invalid JSON retry exactly once, then
   raise a clear error for the UI to surface. No third attempt.
4. Resolve week references through `radar/calendar.py` rather than asking Claude to do the
   date arithmetic. Claude extracts the phrase, our tested code maps it to a date. When the
   calendar returns low confidence, the assessment inherits it.
5. `estimated_hours`: have Claude propose a figure from type, weighting and word count. The
   user overrides it in A6.
6. **Fixture replay.** Save each real response to `tests/fixtures/`. Add an env flag
   (`DEADLINE_RADAR_FIXTURES=1`) that replays fixtures instead of calling the API. This is
   the demo-day safety net and A12 depends on it.

**Test cases, all offline against fixtures:** a valid payload parses; a missing
`source_quote` is rejected; a bad `type` is rejected; malformed JSON triggers exactly one
retry; a second failure raises; a week reference resolves to the right date with confidence
carried through. Mock the client — tests must never hit the network.

**Done when:** extraction on each `sample_data/` PDF returns the count recorded in A4's
README, `pytest tests/test_extract.py` is green offline, and `DEADLINE_RADAR_FIXTURES=1`
reproduces a full run with no network.

**Commit:** `Add Claude PDF extraction with schema validation and fixture replay`

---

# A6 — Upload, review, confirm

**Day:** Tue · **Depends on:** A3, A5

**Goal:** the review gate. Nothing reaches the database until the user clicks Confirm.

**Files:** `app.py`, possibly `radar/ui/` if `app.py` is getting long

**Steps**

1. Multi-file PDF uploader with a spinner during extraction and a readable error if
   extraction raises.
2. Extracted rows land in an editable table held in session state — not the database.
   Use `st.data_editor`. Every field from the data model is editable, `estimated_hours`
   included.
3. Show `confidence` and `source_quote` per row. Visually mark low confidence, because
   those are the rows a student must actually check. The source quote is what makes the
   extraction trustworthy, so keep it visible rather than hiding it behind a click.
4. Let the user drop a row before confirming (false positives happen).
5. Confirm writes the batch via `radar/db.py` with `source` set to `extracted`, clears the
   review state, and shows a success message with the count written.

**Done when:** uploading a `sample_data/` PDF, editing a date, dropping a row and clicking
Confirm results in exactly the expected rows in the database, and a cancelled review
writes nothing.

**Commit:** `Add handbook upload with review table and confirm step`

---

# A7 — Manual assessment CRUD

**Day:** Wed · **Depends on:** A3

**Goal:** add, edit and delete assessments by hand. MVP feature 2.

**Files:** `app.py`

**Steps**

1. An add form covering the data model, with `source` set to `manual` and `confidence`
   set to `high`. Required: module code, title, type, due date.
2. Edit an existing assessment in place, extraction-sourced rows included.
3. Delete with a confirmation step, since cascade removes progress history too.
4. Validate at the form boundary: due date present and parseable, `weighting_pct` within
   0–100, `progress_pct` within 0–100.

**Done when:** a manually added assessment appears on the dashboard alongside extracted
ones and survives a restart.

**Commit:** `Add manual assessment create, edit and delete`

---

# A8 — Dashboard

**Day:** Wed · **Depends on:** A3

**Goal:** the first thing a judge sees. MVP feature 3.

**Files:** `app.py`, small read helpers in `radar/db.py` if needed

**Steps**

1. Upcoming deadlines sorted by due date, with days remaining computed against today.
2. A progress bar per assessment, plus module code, type, weighting and status.
3. Overdue and submitted items handled distinctly — do not show "-3 days remaining" as if
   it were upcoming.
4. Summary line at the top: total assessments, how many due in the next 14 days, how many
   at risk once A10 lands.
5. Keep it skimmable. A judge reads this for about ten seconds.

**Done when:** the dashboard renders correctly with a populated database, with no items,
and with a single item.

**Commit:** `Add deadline dashboard with progress and days remaining`

---

# A9 — Progress logging

**Day:** Wed · **Depends on:** A3, A8

**Goal:** record work done. MVP feature 4, and it feeds risk scoring.

**Files:** `app.py`

**Steps**

1. Per assessment: log hours spent, set the new progress percentage, add an optional note.
2. Write through `db.log_progress` so both tables stay consistent.
3. Show that assessment's history, most recent first, with cumulative hours.
4. Setting progress to 100 offers to mark the status `submitted` — do not do it silently,
   since finished work is not always submitted work.

**Done when:** logging progress updates the dashboard bar immediately and the history
persists across a restart.

**Commit:** `Add progress logging with history view`

---

# A10 — Workload heatmap and risk scoring

**Day:** Wed · **Depends on:** A2, A3, A9

**Goal:** MVP features 5 and 6, the two that make this more than a to-do list.

**Files:** `radar/analytics.py`, `tests/test_analytics.py`, `app.py`

Write the risk-scoring tests first — `CLAUDE.md` requires it.

**Steps**

1. **Weekly workload.** Bucket remaining estimated hours by term week via
   `radar/calendar.py`. Remaining hours means `estimated_hours * (1 - progress_pct/100)`.
   Mark reading weeks. A Plotly heatmap or bar chart across the term; whichever reads more
   clearly at a glance wins.
2. **Risk scoring**, exactly as `CLAUDE.md` defines it. An assessment is at risk when
   either condition holds:
   - actual progress trails expected progress by more than 20 percentage points, where
     expected is linear from a suggested start date to the due date;
   - remaining hours exceed available weeks at 10 hours per week per assessment.
3. Return the reason alongside the flag. "At risk" with no explanation is not actionable.
4. Surface risk on the dashboard from A8 and in the summary line.

**Test cases:** on-track work is not flagged; work 25 points behind is flagged on the
progress rule; work with 40 remaining hours and 2 weeks left is flagged on the hours rule;
work exactly 20 points behind is not flagged (boundary is strictly greater than);
a submitted assessment is never flagged; an overdue incomplete assessment is flagged;
hours bucket into the right weeks and reading weeks are identified.

**Done when:** `pytest tests/test_analytics.py` is green, the chart renders across a full
term, and every flag carries a reason.

**Commit:** `Add weekly workload chart and risk scoring with tests`

---

# A11 — Calendar export

**Day:** Thu · **Depends on:** A3

**Goal:** MVP feature 7.

**Files:** `radar/export.py`, `tests/test_export.py`, `app.py`

**Steps**

1. Build a VCALENDAR with `icalendar`, one VEVENT per assessment.
2. All-day event when `due_time` is null, timed event when it is set. Europe/London.
3. Summary carries module code and title; description carries weighting, estimated hours
   and the source quote, so the context travels into the student's calendar.
4. Stable UIDs derived from the assessment id, so re-importing updates rather than duplicates.
5. Streamlit download button producing `deadline-radar.ics`.

**Test cases:** output parses back with `icalendar`; event count matches assessment count;
an all-day and a timed event both serialise correctly; UIDs are stable across two exports.

**Done when:** the downloaded file imports into a real calendar app with correct dates.
Actually import it — malformed .ics files fail quietly.

**Commit:** `Add .ics export for all deadlines`

---

# A12 — Demo seed data and rehearsal

**Day:** Thu · **Depends on:** A1–A11

**Goal:** a demo that works on stage. Feature freeze at the end of this session.

**Files:** `sample_data/seed.py`, `README.md`

**Steps**

1. A seed script building a database that tells a story: around 10 assessments across 4
   modules, a visible crunch week, two assessments at risk for different reasons, some
   progress history, and one low-confidence extraction to talk about honestly.
2. Make it idempotent and clearly separate from real user data.
3. Full end-to-end rehearsal: fresh database, upload a handbook, review and edit, confirm,
   log progress, view the heatmap, hit a risk flag, export .ics.
4. **Verify the offline path.** Run the whole demo with `DEADLINE_RADAR_FIXTURES=1` and no
   network. If the venue wifi fails, this is the demo.
5. Write the demo script into `README.md`: a three-minute narrative, the order of clicks,
   and the single sentence that explains why review-before-write matters.
6. Fix only what the rehearsal breaks. Then freeze. New features after Thursday night are
   how demos die.

**Done when:** the full flow runs twice without a stumble, once offline, and the demo
script is written down rather than held in someone's head.

**Commit:** `Add demo seed data and rehearsal script`

---

# A13 — Stretch: plan my week

**Day:** Thu evening, only if A1–A12 are green · **Depends on:** A10

Claude reads current assessments, progress and the workload chart, then proposes a
prioritised weekly plan with hours per assessment. Needs its own fixture for offline replay.
Must not write to the database. If it is not solid, cut it — a crisp MVP beats a shaky extra.

**Commit:** `Add plan my week suggestions`

---

# A14 — Stretch: suggested start dates

**Day:** Thu evening, only if A1–A13 are green · **Depends on:** A2, A10

Backward-plan a start date per assessment from the due date, estimated hours and the
workload in surrounding weeks, avoiding reading weeks where possible. Feeds the expected
progress line in the A10 risk rule, which currently needs a start date anyway — so if A10
is using a crude assumption, this replaces it. Needs tests.

**Commit:** `Add backward-planned suggested start dates`
