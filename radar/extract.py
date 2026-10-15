"""Claude extraction: handbook PDF in, validated assessment rows out.

One API call per PDF, with exactly one retry on an unusable response and no
third attempt. The model is asked for JSON only and is never asked to do date
arithmetic: it reports the phrase it found ("Term 1 Week 10") and
``radar.calendar`` maps that to a date, so week-number reasoning stays in tested
code. When the calendar is unsure, the assessment inherits that low confidence.

Nothing here touches Streamlit. Fixture fallbacks are reported through the
returned :class:`ExtractionResult` so the caller can surface them, and every row
carries a ``source`` of ``extracted`` or ``fixture`` from the moment it is built.
A fabricated row must never reach the database claiming to be a real extraction.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import date, time
import math
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from radar import calendar
from radar.db import ASSESSMENT_TYPES, CONFIDENCES
from radar.response import response_text

MODEL = 'claude-sonnet-5-5'
MAX_ATTEMPTS = 2  # the first call plus the single retry CLAUDE.md allows
MAX_TOKENS = 4096
API_TIMEOUT_SECONDS = 30.0

FIXTURES_ENV = 'DEADLINE_RADAR_FIXTURES'
FIXTURES_DIR = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures'
DEFAULT_FIXTURE = '_default'

REQUIRED_KEYS = ('module_code', 'title', 'type', 'due_date', 'confidence', 'source_quote')

# A date the model gave us but neither ISO parsing nor the calendar could pin
# down. The row still needs a date because `assessments.due_date` is NOT NULL,
# so it gets this placeholder, confidence `low`, and a note saying so. It is
# meant to be obviously wrong: the review gate exists so the student fixes it.
UNRESOLVED_DUE_DATE = '2026-11-30'
UNRESOLVED_NOTE = 'Due date could not be resolved from {phrase!r} - placeholder, correct before confirming.'

# Rough hours when the model omits `estimated_hours` entirely. The student
# overrides this in the review editor; it only has to be defensible, not right.
BASE_HOURS_BY_TYPE = {
    'essay': 25.0, 'exam': 30.0, 'coursework': 20.0, 'presentation': 12.0,
    'problem_set': 10.0, 'group_project': 30.0, 'other': 15.0,
}
WORDS_PER_HOUR = 100.0
MAX_ESTIMATED_HOURS = 120.0

PROMPT = (
    'You are an assessment extraction assistant for UCL students.\n'
    'Read the uploaded handbook PDF and extract EVERY assessment, coursework, exam, '
    'presentation and problem set it describes, including any mentioned only in '
    'passing in a paragraph rather than listed in an assessment table.\n\n'
    'Return ONLY a valid JSON array of objects. Each object must have these exact keys:\n'
    '- "module_code": string, e.g. "COMP0002"\n'
    '- "module_name": string, e.g. "Software Engineering"\n'
    '- "title": string, e.g. "Coursework 1: Architecture Specification"\n'
    '- "type": one of "essay", "exam", "coursework", "presentation", "problem_set", '
    '"group_project", "other"\n'
    '- "due_date": an ISO date "YYYY-MM-DD" if the handbook gives one, otherwise the '
    'week phrase exactly as written, e.g. "Term 1 Week 10" or "Week 6". Do NOT convert '
    'a week number into a date yourself; quote the phrase and we will resolve it.\n'
    '- "due_time": local time "HH:MM:SS" or null if the handbook gives no time\n'
    '- "weighting_pct": number 0-100, or null if the handbook does not state one\n'
    '- "word_count": integer, or null if the handbook does not state one\n'
    '- "estimated_hours": realistic float hours of work, proposed from the type, '
    'weighting and word count\n'
    '- "confidence": "high" if the handbook is explicit, "medium" if you inferred part '
    'of it, "low" if the date is genuinely unclear from the document\n'
    '- "source_quote": a verbatim quote from the PDF containing the deadline. Required; '
    'never invent or paraphrase it.\n'
    '- "notes": string, may be empty\n\n'
    'Do not guess a weighting or word count that is not in the document - use null. '
    'Do not include markdown formatting or commentary. Output raw JSON only.'
)


class ExtractionError(ValueError):
    """The model's response could not be used: bad JSON, or a row failing schema."""


@dataclass
class ExtractionResult:
    """Outcome of one handbook extraction, including how the rows were produced."""

    assessments: list[dict[str, Any]]
    source: str  # "api" or "fixture"
    reason: str | None = None

    def __post_init__(self) -> None:
        # Stamp provenance on every row here so no return path can produce rows
        # that disagree with the result, and so the label survives the review
        # queue into the database rather than being reassigned at save time.
        row_source = 'fixture' if self.used_fixtures else 'extracted'
        for row in self.assessments:
            row['source'] = row_source

    @property
    def used_fixtures(self) -> bool:
        return self.source == 'fixture'


def propose_estimated_hours(
    assessment_type: str,
    weighting_pct: float | None,
    word_count: int | None,
) -> float:
    """Fallback workload estimate for a row the model left without one."""
    if word_count:
        hours = word_count / WORDS_PER_HOUR
    else:
        hours = BASE_HOURS_BY_TYPE.get(assessment_type, 20.0)
    if weighting_pct:
        # A component worth 40% of a module should not be estimated at 10 hours
        # just because it has no word count.
        hours = max(hours, weighting_pct * 0.5)
    return round(min(hours, MAX_ESTIMATED_HOURS), 1)


def _resolve_due_date(raw: str, confidence: str) -> tuple[str, str, str | None]:
    """Map a model-supplied date or week phrase to (iso_date, confidence, note).

    An ISO date is taken at face value. Anything else goes to the calendar, and a
    low-confidence resolution drags the assessment's confidence down with it. A
    phrase the calendar cannot parse is quarantined rather than rejected, because
    a handbook genuinely can leave a date unresolvable.
    """
    try:
        return date.fromisoformat(raw).isoformat(), confidence, None
    except ValueError:
        pass

    try:
        resolution = calendar.resolve_week_reference(raw)
    except (ValueError, KeyError):
        return UNRESOLVED_DUE_DATE, 'low', UNRESOLVED_NOTE.format(phrase=raw)

    if resolution.confidence == 'low':
        confidence = 'low'
    return resolution.date.isoformat(), confidence, None


def _optional_number(item: Mapping[str, Any], key: str, cast, label: str):
    value = item.get(key)
    if value is None or value == '':
        return None
    try:
        if isinstance(value, bool) or not math.isfinite(float(value)):
            raise ValueError
        if cast is int and float(value) != int(value):
            raise ValueError
        return cast(value)
    except (TypeError, ValueError, OverflowError):
        raise ExtractionError(f'{label} {key!r} is not a number: {value!r}') from None


def validate_row(item: Any, index: int = 0) -> dict[str, Any]:
    """Validate and normalise one extracted assessment, or raise ExtractionError."""
    label = f'assessment {index}'
    if not isinstance(item, Mapping):
        raise ExtractionError(f'{label} is {type(item).__name__}, expected an object')

    missing = [k for k in REQUIRED_KEYS if not str(item.get(k) or '').strip()]
    if missing:
        raise ExtractionError(f'{label} is missing required field(s): {", ".join(missing)}')

    assessment_type = str(item['type']).strip().lower()
    if assessment_type not in ASSESSMENT_TYPES:
        raise ExtractionError(f'{label} has unknown type {assessment_type!r}')

    confidence = str(item['confidence']).strip().lower()
    if confidence not in CONFIDENCES:
        raise ExtractionError(f'{label} has unknown confidence {confidence!r}')

    weighting = _optional_number(item, 'weighting_pct', float, label)
    if weighting is not None and not 0 <= weighting <= 100:
        raise ExtractionError(f'{label} has weighting_pct outside 0-100: {weighting}')

    words = _optional_number(item, 'word_count', int, label)
    if words is not None and words < 0:
        raise ExtractionError(f'{label} has a negative word_count: {words}')

    due_date, confidence, note = _resolve_due_date(
        str(item['due_date']).strip(), confidence)

    hours = _optional_number(item, 'estimated_hours', float, label)
    if hours is None or hours <= 0:
        hours = propose_estimated_hours(assessment_type, weighting, words)

    notes = str(item.get('notes') or '').strip()
    if note:
        notes = f'{note} {notes}'.strip()

    due_time = item.get('due_time')
    if due_time:
        try:
            parsed_time = time.fromisoformat(str(due_time).strip())
            if parsed_time.tzinfo is not None or parsed_time.microsecond:
                raise ValueError
            due_time = parsed_time.isoformat(timespec='seconds')
        except ValueError:
            raise ExtractionError(f'{label} has invalid local due_time: {due_time!r}') from None
    return {
        'module_code': str(item['module_code']).strip(),
        'module_name': str(item.get('module_name') or '').strip(),
        'title': str(item['title']).strip(),
        'type': assessment_type,
        'due_date': due_date,
        'due_time': str(due_time).strip() if due_time else None,
        'weighting_pct': weighting,
        'word_count': words,
        'estimated_hours': hours,
        'confidence': confidence,
        'source_quote': str(item['source_quote']).strip(),
        'notes': notes,
    }


def validate_batch(items: Any) -> list[dict[str, Any]]:
    """Validate a whole extracted payload, or raise ExtractionError."""
    if not isinstance(items, list):
        raise ExtractionError(
            f'expected a JSON array of assessments, got {type(items).__name__}')
    return [validate_row(item, i) for i, item in enumerate(items)]


def _strip_code_fence(text: str) -> str:
    """Unwrap ```json ... ``` fencing the model sometimes adds despite the prompt."""
    stripped = text.strip()
    if not stripped.startswith('```'):
        return stripped
    body = stripped.split('```')
    if len(body) < 2:
        return stripped
    inner = body[1]
    if inner.lower().startswith('json'):
        inner = inner[4:]
    return inner.strip()


def parse_response(text: str) -> list[dict[str, Any]]:
    """Turn raw model text into validated rows, or raise ExtractionError."""
    try:
        payload = json.loads(_strip_code_fence(text))
    except json.JSONDecodeError as err:
        raise ExtractionError(f'response was not valid JSON: {err}') from err
    return validate_batch(payload)


def _call_model(client: Any, file_bytes: bytes) -> str:
    """Send the PDF as a document content block and return the raw reply text."""
    pdf_b64 = base64.b64encode(file_bytes).decode('utf-8')
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{
            'role': 'user',
            'content': [
                {
                    'type': 'document',
                    'source': {
                        'type': 'base64',
                        'media_type': 'application/pdf',
                        'data': pdf_b64,
                    },
                },
                {'type': 'text', 'text': PROMPT},
            ],
        }],
    )
    return response_text(response)


def fixture_path(filename: str) -> Path:
    """The fixture file standing in for `filename`, by module code in the name."""
    lower = Path(filename).stem.lower()
    for candidate in sorted(FIXTURES_DIR.glob('*.json')):
        if candidate.stem.startswith('_'):
            continue
        code = candidate.stem.split('_')[0]
        if code and code in lower:
            return candidate
    return FIXTURES_DIR / f'{DEFAULT_FIXTURE}.json'


def load_fixture(filename: str) -> list[dict[str, Any]]:
    """Replay a saved response for `filename` through the real validation path.

    Fixtures are validated exactly like a live response, so a malformed fixture
    fails loudly in the test suite rather than quietly feeding the demo rows the
    schema would have rejected.
    """
    path = fixture_path(filename)
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise ExtractionError(f'no fixture available for {filename!r} at {path}') from None
    except json.JSONDecodeError as err:
        raise ExtractionError(f'fixture {path.name} is not valid JSON: {err}') from err

    # Accept a bare array (a response saved verbatim) or an object wrapping one,
    # so a captured response can be dropped in beside the hand-written fixtures.
    if isinstance(raw, Mapping):
        raw = raw.get('assessments')
    return validate_batch(raw)


def extract_from_pdf(
    file_bytes: bytes,
    filename: str,
    *,
    client: Any = None,
    fixtures_mode: bool | None = None,
    api_key: str | None = None,
) -> ExtractionResult:
    """Extract assessments from one handbook PDF.

    Falls back to replaying a fixture when there is no API key, when
    ``DEADLINE_RADAR_FIXTURES=1`` is set, or when both attempts fail. The caller
    gets the reason on the result so it can tell the student the rows are
    fabricated; every fallback row is stamped ``source = fixture``.
    """
    if fixtures_mode is None:
        fixtures_mode = os.environ.get(FIXTURES_ENV, '0').strip() == '1'
    if api_key is None:
        api_key = os.environ.get('ANTHROPIC_API_KEY', '').strip()

    if fixtures_mode:
        return ExtractionResult(
            load_fixture(filename), 'fixture',
            f'{FIXTURES_ENV}=1 forces offline fixture mode')

    if client is None and not api_key:
        return ExtractionResult(
            load_fixture(filename), 'fixture',
            'ANTHROPIC_API_KEY is not set, so Claude was never called')

    if client is None:
        import anthropic
        client = anthropic.Anthropic(
            api_key=api_key, timeout=API_TIMEOUT_SECONDS, max_retries=0)

    last_error: Exception | None = None
    for _attempt in range(MAX_ATTEMPTS):
        try:
            return ExtractionResult(parse_response(_call_model(client, file_bytes)), 'api')
        except Exception as err:  # noqa: BLE001 - any failure costs us one attempt
            last_error = err

    return ExtractionResult(
        load_fixture(filename), 'fixture',
        f'every Claude attempt failed ({last_error})')
