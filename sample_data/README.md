# Sample handbooks

Three synthetic UCL-style module handbooks for testing extraction. Module codes,
staff names, rooms and deadlines are invented; nothing here is a real UCL document
and there is no personal data. Each is two pages of prose with an assessment
section, not a bare table.

Regenerate after editing the prose in `generate_handbooks.py`:

```sh
.venv/bin/python sample_data/generate_handbooks.py
```

**Expected extraction: 10 assessments across the three files.** The resolved dates
below were checked against `radar.calendar.resolve_week_reference`, so they are what
the tested code returns, not a guess. Week references resolve to the **Monday** of
the week — none of the week-based deadlines name a day, so that is the convention,
not a reading of the handbook.

## COMP0197_applied_machine_learning_systems.pdf — 4 assessments

The straightforward one. Term 1, coursework only, every date resolvable.

| Title | Type | Weight | Words | Phrasing | Due | Confidence |
|---|---|---|---|---|---|---|
| Problem Set 1 | `problem_set` | 15 | — | "Submit by 4 December 2026, 16:00" | 2026-12-04 16:00 | high |
| Coursework 1: model card report | `coursework` | 35 | 2500 | "due in Week 5" | 2026-10-26 | low |
| Group project: deployment case study | `group_project` | 20 | 4000 | "due in UCL timetable week 14" | 2026-11-30 | high |
| Showcase presentation | `presentation` | 30 | — | "Thursday 10 December 2026" | 2026-12-10, no time | high |

- "Week 5" is low confidence because the handbook gives no numbering scheme. Term 1
  is implied by the module, not stated beside the week number; the alternative
  reading (timetable week 5) is 2026-09-28.
- The timetable week is high confidence because the scheme is named outright.
- The group project word count is for the whole team, so 4000 is the figure for the
  single shared row, not per student.

## STAT0023_statistical_computing.pdf — 3 assessments

Carries the ambiguous week reference and the relative one, plus a component whose
weighting is deliberately absent so the nullable column gets exercised.

| Title | Type | Weight | Words | Phrasing | Due | Confidence |
|---|---|---|---|---|---|---|
| In-course assessment 1 | `coursework` | 30 | — | "The deadline is Term 1 Week 10" | 2026-11-30 | low |
| In-course assessment 2: data communication report | `coursework` | **null** | 1500 | "two weeks after the reading week" | 2026-11-23 | low |
| Take-home examination | `exam` | 50 | — | "Monday 11 January 2027 between 10:00 and 13:00" | 2027-01-11 13:00 | medium |

- "Term 1 Week 10" resolves to 2026-11-30 under calendar numbering; teaching
  numbering (reading week skipped) gives 2026-12-07 and timetable numbering gives
  2026-11-02. The surrounding paragraph rules out the timetable reading but not the
  other two, which is why the answer is low confidence with the source quote kept.
- The second assessment states no weighting at all ("confirmed on Moodle at the
  start of term"), so `weighting_pct` must come out null rather than guessed.
- **Known gap:** `resolve_week_reference` raises `ValueError: unsupported week
  reference` for "two weeks after the reading week" today. The reading week
  commences 2026-11-09, so the answer is the week commencing 2026-11-23. Either
  teach the resolver the relative form or have extraction hand it the resolved week.
- The exam window ends at 13:00 with thirty further minutes allowed for submission,
  so 13:30 is also defensible — medium confidence either way.

## ANTH0045_digital_anthropology.pdf — 3 assessments

The hard case. One assessment is never listed, only mentioned mid-paragraph, and
one date cannot be resolved from the document at all.

| Title | Type | Weight | Words | Phrasing | Due | Confidence |
|---|---|---|---|---|---|---|
| Research essay | `essay` | 60 | 4000 | "by 12 noon on Friday 5 March 2027" | 2027-03-05 12:00 | high |
| Fieldwork notebook | `coursework` | **null** | 2000 | "handed in alongside the essay" | 2027-03-05 | medium |
| Seminar presentation | `presentation` | 40 | — | "Presentations run in the second half of term" | unresolvable | low |

- The notebook appears only inside a paragraph about what students produce, with no
  heading and no entry in the assessment section. Missing it is the most likely
  failure on this file. It is marked pass/fail, so `weighting_pct` is null, and its
  date is inherited from the essay rather than stated — hence medium.
- The presentation date is genuinely unresolvable: slots are allocated in the first
  seminar and the schedule changes during term. `due_date` is `NOT NULL`, so a row
  still has to carry a date. Any Monday from 2027-02-22 to 2027-03-22 is an
  acceptable guess; what the review table is actually being tested on is
  `confidence: low` plus the source quote, so a student can see the guess and fix it.

## Dates that must not become assessments

Each file seeds non-assessment deadlines. An extraction run that returns more than
the counts above has most likely picked one of these up:

- COMP0197: teams formed in the third laboratory session; running order published
  the preceding Friday; feedback returned within one calendar month.
- STAT0023: module registration closes 9 October 2026; past papers on Moodle.
- ANTH0045: ethics form approved before fieldwork begins; reading list sign-up opens
  in Week 2; seminar participation explicitly not assessed.
