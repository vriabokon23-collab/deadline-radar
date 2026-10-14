# Deadline Radar frontend redesign

The plan was to use native six-page navigation, neutral light/dark themes,
compact overview metrics and a workload preview, a searchable assessment list
with quick actions, ranked weekly priorities, and a three-step import flow.
Settings now owns demo controls and the reference date. Backend modules and
SQLite schema are unchanged.

Visual direction: restrained list density and hierarchy inspired by
[Linear's UI](https://linear.app/changelog/2026-03-12-ui-refresh), calendar-focused
navigation inspired by [Notion Calendar](https://www.notion.com/en-gb/product/calendar),
and plain task rows inspired by [Things](https://culturedcode.com/things/features/).
The result uses native Streamlit elements rather than recreating those apps.

## Implementation

- `.streamlit/config.toml`: white and soft grey surfaces, muted blue accent,
  consistent 14px body type, a restrained heading scale, 6px control radius,
  readable status colours, and a matching dark palette. Fonts stay local.
- `app_pages/`: Overview, Assessments, Planner, Workload, Import and Settings.
  Direct page scripts open and close their own SQLite connections.
- Overview: four summary metrics, an eight-week chart, and six nearest open
  deadlines. Details retain risk reasons, backward-planned starts and quotes.
- Assessments: full list, search, module/status filters, sorting, progress bars,
  due dates and quick actions that target the correct row. Forms and progress
  history remain available in the same page.
- Planner: seven-day horizon, hours budget, allocation progress, ranked priorities
  and reasons. Synthetic plans and assessments retain provenance warnings.
- Import: collapsible upload step, review grid, source quotes and explicit save.
  Validation, duplicate protection and navigation-safe drafts are preserved.
- Settings: appearance guidance, persistent reference date, API mode, demo controls
  and a confirmed destructive action in a collapsed section.

## Streamlit limits and verification

The supported theme switch lives in Streamlit's app menu; the app cannot provide
an in-page theme toggle through a public API. Native columns stack and toolbars
wrap at narrow widths. The review editor is a horizontally scrollable grid on
small screens. Pixel-identical desktop-app spacing and hover behaviour would
require custom components or fragile CSS; neither was introduced.

The planner remains a ranked hours allocation, not a draggable calendar schedule:
the existing backend does not assign sessions to individual days.

Computer use was declined, so no rendered-browser screenshot or viewport checks
were performed. AppTest exercises actual page routing and interactions, with
empty and populated databases; tests verify rendering does not mutate existing
assessments or history. The offline and mocked-API demo journeys still run from
upload through reviewed saving, planning and calendar-byte generation.
