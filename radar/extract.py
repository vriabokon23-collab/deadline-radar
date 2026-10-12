"""Claude extraction: handbook PDF in, validated assessment rows out.

This module never imports Streamlit. Callers inspect the returned
ExtractionResult and render their own messaging.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import date
import json
import os
from pathlib import Path
from typing import Any, Literal

from radar import calendar
from radar.db import ASSESSMENT_TYPES, CONFIDENCES

MODEL = 'claude-sonnet-5-5'
FALLBACK_MODELS = ('claude-3-7-sonnet-20250219', 'claude-3-5-sonnet-20241022')
MAX_ATTEMPTS = 2
MAX_TOKENS = 4096

# Stands in when a week reference resolves to nothing; the row is forced to low
# confidence so the user reconciles it against source_quote before confirming.
UNRESOLVED_DUE_DATE = '2026-11-30'

DEFAULT_MODULE_CODE = 'UCL0001'
DEFAULT_TITLE = 'Assessment'
DEFAULT_TYPE = 'coursework'
DEFAULT_CONFIDENCE = 'medium'
DEFAULT_ESTIMATED_HOURS = 20.0

PROMPT = (
    "You are an assessment extraction assistant for UCL students.\n"
    "Read the uploaded handbook PDF and extract ALL assessments, coursework, exams, presentations, "
    "and problem sets described in the document.\n\n"
    "Return ONLY a valid JSON array of objects. Each object must have these exact keys:\n"
    '- "module_code": string (e.g. "COMP0002")\n'
    '- "module_name": string (e.g. "Software Engineering")\n'
    '- "title": string (e.g. "Coursework 1: Architecture Specification")\n'
    '- "type": one of "essay", "exam", "coursework", "presentation", "problem_set", "group_project", "other"\n'
    '- "due_date": ISO date "YYYY-MM-DD" or explicit term week phrase like "Term 1 Week 10" or "Week 6"\n'
    '- "due_time": local time "HH:MM:SS" or null\n'
    '- "weighting_pct": number (0-100) or null\n'
    '- "word_count": integer or null\n'
    '- "estimated_hours": realistic float hours required (propose based on type, weighting and word count)\n'
    '- "confidence": "high", "medium", or "low"\n'
    '- "source_quote": verbatim quote from the text where the deadline was found\n'
    '- "notes": string notes\n\n'
    "Do NOT include markdown formatting or commentary. Output raw JSON only."
)


@dataclass(frozen=True)
class ExtractionResult:
    """Extracted rows plus provenance, so the UI can warn on fixture replay."""

    assessments: list[dict[str, Any]]
    source: Literal['api', 'fixture']
    error: str | None = None

    @property
    def used_fixtures(self) -> bool:
        return self.source == 'fixture'


def extract_from_pdf(
    file_bytes: bytes,
    filename: str,
    *,
    api_key: str | None = None,
    client: Any | None = None,
    fixtures_mode: bool | None = None,
) -> ExtractionResult:
    """Extract assessments from a handbook PDF, falling back to fixture replay.

    Passing `client` injects a prepared Anthropic client and bypasses the
    environment checks, which is how the tests exercise the API path.
    """
    if fixtures_mode is None:
        fixtures_mode = os.environ.get('DEADLINE_RADAR_FIXTURES', '0').strip() == '1'
    if api_key is None:
        api_key = os.environ.get('ANTHROPIC_API_KEY', '').strip()

    if client is None:
        if fixtures_mode or not api_key:
            return ExtractionResult(generate_fixture_extractions(filename), 'fixture')
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

    message = [{
        'role': 'user',
        'content': [
            {
                'type': 'document',
                'source': {
                    'type': 'base64',
                    'media_type': 'application/pdf',
                    'data': base64.b64encode(file_bytes).decode('utf-8'),
                },
            },
            {'type': 'text', 'text': PROMPT},
        ],
    }]

    last_error: Exception | None = None
    for model in (MODEL, *FALLBACK_MODELS):
        for _ in range(MAX_ATTEMPTS):
            try:
                response = client.messages.create(
                    model=model, max_tokens=MAX_TOKENS, messages=message,
                )
                data = json.loads(_strip_code_fence(response.content[0].text.strip()))
                if isinstance(data, list):
                    return ExtractionResult(normalize_extracted_batch(data), 'api')
            except Exception as err:
                last_error = err

    reason = str(last_error) if last_error else 'response was not a JSON array'
    return ExtractionResult(generate_fixture_extractions(filename), 'fixture', reason)


def _strip_code_fence(text: str) -> str:
    """Unwrap a ```json fenced block, which models emit despite instructions."""
    if not text.startswith('```'):
        return text
    inner = text.split('```')[1]
    return inner[4:].strip() if inner.startswith('json') else inner


def normalize_extracted_batch(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Coerce raw model output into rows matching the assessments schema."""
    return [_normalize_one(item) for item in items]


def _normalize_one(item: dict[str, Any]) -> dict[str, Any]:
    raw_date = str(item.get('due_date', '')).strip()
    confidence = str(item.get('confidence', 'high')).lower()
    if confidence not in CONFIDENCES:
        confidence = DEFAULT_CONFIDENCE

    due_date, confidence = _resolve_due_date(raw_date, confidence)

    assessment_type = str(item.get('type', DEFAULT_TYPE)).lower()
    if assessment_type not in ASSESSMENT_TYPES:
        assessment_type = DEFAULT_TYPE

    weighting = item.get('weighting_pct')
    word_count = item.get('word_count')
    return {
        'module_code': str(item.get('module_code') or DEFAULT_MODULE_CODE).strip(),
        'module_name': str(item.get('module_name') or '').strip(),
        'title': str(item.get('title') or DEFAULT_TITLE).strip(),
        'type': assessment_type,
        'due_date': due_date,
        'due_time': item.get('due_time'),
        'weighting_pct': float(weighting) if weighting is not None else None,
        'word_count': int(word_count) if word_count is not None else None,
        'estimated_hours': float(item.get('estimated_hours') or DEFAULT_ESTIMATED_HOURS),
        'confidence': confidence,
        'source_quote': str(item.get('source_quote') or '').strip(),
        'notes': str(item.get('notes') or '').strip(),
    }


def _resolve_due_date(raw_date: str, confidence: str) -> tuple[str, str]:
    """Map an ISO date or a handbook week phrase onto a concrete due date."""
    try:
        date.fromisoformat(raw_date)
        return raw_date, confidence
    except ValueError:
        pass
    try:
        resolution = calendar.resolve_week_reference(raw_date)
    except Exception:
        return UNRESOLVED_DUE_DATE, 'low'
    return resolution.date.isoformat(), 'low' if resolution.confidence == 'low' else confidence


def generate_fixture_extractions(filename: str) -> list[dict[str, Any]]:
    """Provide realistic offline extractions keyed off the handbook filename."""
    lower = filename.lower()
    if 'algo' in lower or 'comp0004' in lower:
        return [
            {
                'module_code': 'COMP0004',
                'module_name': 'Algorithms & Data Structures',
                'title': 'Problem Set 1',
                'type': 'problem_set',
                'due_date': '2026-10-30',
                'due_time': '16:00:00',
                'weighting_pct': 15.0,
                'word_count': None,
                'estimated_hours': 15.0,
                'confidence': 'high',
                'source_quote': 'Problem Set 1 is due on Friday 30 October 2026 at 4pm.',
                'notes': 'Submit via Moodle',
            },
            {
                'module_code': 'COMP0004',
                'module_name': 'Algorithms & Data Structures',
                'title': 'Problem Set 2',
                'type': 'problem_set',
                'due_date': '2026-11-30',
                'due_time': '16:00:00',
                'weighting_pct': 15.0,
                'word_count': None,
                'estimated_hours': 20.0,
                'confidence': 'low',
                'source_quote': 'Problem Set 2 is due in Term 1 Week 10.',
                'notes': 'Low confidence due to calendar vs teaching week ambiguity.',
            },
        ]
    if 'eng' in lower or 'se' in lower or 'comp0002' in lower:
        return [
            {
                'module_code': 'COMP0002',
                'module_name': 'Software Engineering',
                'title': 'Coursework 1: Architecture Specification',
                'type': 'coursework',
                'due_date': '2026-11-16',
                'due_time': '16:00:00',
                'weighting_pct': 30.0,
                'word_count': 2500,
                'estimated_hours': 25.0,
                'confidence': 'high',
                'source_quote': 'Coursework 1 must be submitted by Monday 16 November at 16:00.',
                'notes': 'Individual assignment',
            },
            {
                'module_code': 'COMP0002',
                'module_name': 'Software Engineering',
                'title': 'Final Project & Team Review',
                'type': 'group_project',
                'due_date': '2026-12-14',
                'due_time': '17:00:00',
                'weighting_pct': 70.0,
                'word_count': 5000,
                'estimated_hours': 50.0,
                'confidence': 'high',
                'source_quote': 'Final deliverables due on Monday 14 December by 5pm.',
                'notes': 'Requires video demo link',
            },
        ]
    stem = Path(filename).stem.replace('_', ' ').upper()
    module_code = stem[:8] if len(stem) >= 8 else 'COMP0099'
    return [
        {
            'module_code': module_code,
            'module_name': stem,
            'title': 'Midterm Coursework Assignment',
            'type': 'coursework',
            'due_date': '2026-11-20',
            'due_time': '16:00:00',
            'weighting_pct': 40.0,
            'word_count': 2000,
            'estimated_hours': 22.0,
            'confidence': 'high',
            'source_quote': f'Extracted from {filename}: Midterm assessment due Friday 20 Nov.',
            'notes': 'Standard submission rules apply',
        },
        {
            'module_code': module_code,
            'module_name': stem,
            'title': 'Final Term Assessment',
            'type': 'exam',
            'due_date': '2026-12-11',
            'due_time': '14:00:00',
            'weighting_pct': 60.0,
            'word_count': None,
            'estimated_hours': 30.0,
            'confidence': 'low',
            'source_quote': f'Extracted from {filename}: End-of-term assessment in Week 11.',
            'notes': 'Check whether reading week is included',
        },
    ]
