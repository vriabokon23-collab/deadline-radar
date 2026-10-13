"""RFC 5545 calendar export for persisted assessment deadlines."""

from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event, Timezone

from radar.db import FABRICATED_SOURCE

LONDON = ZoneInfo('Europe/London')


def split_fabricated(
    assessments: Iterable[Mapping[str, Any]],
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    """Partition rows into (genuine, fabricated) on the `source` column.

    Rows with no `source` at all count as genuine, matching the review editor's
    treatment of hand-typed rows. Callers use the fabricated list to tell the
    student what the export left out.
    """
    genuine: list[Mapping[str, Any]] = []
    fabricated: list[Mapping[str, Any]] = []
    for row in assessments:
        bucket = fabricated if row.get('source') == FABRICATED_SOURCE else genuine
        bucket.append(row)
    return genuine, fabricated


def _timestamp(value: str | datetime | None, fallback: datetime) -> datetime:
    if value is None:
        return fallback
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _local_deadline(day: date, value: str | time) -> datetime:
    clock = time.fromisoformat(value) if isinstance(value, str) else value
    if clock.tzinfo is not None:
        raise ValueError('due_time must be a local Europe/London time')
    naive = datetime.combine(day, clock)
    first = naive.replace(tzinfo=LONDON, fold=0)
    second = naive.replace(tzinfo=LONDON, fold=1)
    if first.astimezone(timezone.utc).astimezone(LONDON).replace(tzinfo=None) != naive:
        raise ValueError('deadline falls in the missing hour at the DST transition')
    if first.utcoffset() != second.utcoffset():
        raise ValueError('deadline falls in the ambiguous hour at the DST transition')
    return first


def export_calendar(assessments: Iterable[Mapping[str, Any]]) -> bytes:
    """Return downloadable .ics bytes, with stable UIDs from database IDs.

    All-day deadlines span their due date only. Timed deadlines are instantaneous
    events in Europe/London, with timezone definitions embedded for importers.
    No status is inferred from progress; submitted assessments are included.

    Fabricated fixture rows are dropped rather than labelled: importing an .ics is
    a one-way copy, so an invented deadline would outlive the row it came from and
    keep firing reminders in a calendar this app can no longer reach.
    """
    assessments, _ = split_fabricated(assessments)
    calendar = Calendar()
    calendar.add('prodid', '-//Deadline Radar//UCL Assessment Deadlines//EN')
    calendar.add('version', '2.0')
    calendar.add('calscale', 'GREGORIAN')
    calendar.add('x-wr-calname', 'Deadline Radar')
    now = datetime.now(timezone.utc)
    seen_ids = set()
    timed_dates = []
    events = []
    for row in assessments:
        assessment_id = row['id']
        if type(assessment_id) is not int or assessment_id < 1:
            raise ValueError('calendar export requires a positive persisted assessment id')
        if assessment_id in seen_ids:
            raise ValueError('duplicate assessment id in calendar export')
        seen_ids.add(assessment_id)
        value = row['due_date']
        day = date.fromisoformat(value) if isinstance(value, str) else value
        if type(day) is not date:
            raise ValueError('due_date must be an ISO date or date object')
        event = Event()
        event.add('uid', f'assessment-{assessment_id}@deadline-radar')
        event.add('dtstamp', _timestamp(row.get('updated_at'), now))
        event.add('created', _timestamp(row.get('created_at'), now))
        event.add('last-modified', _timestamp(row.get('updated_at'), now))
        event.add('summary', f"{row['module_code']}: {row['title']}")
        weighting = row.get('weighting_pct')
        hours = row.get('estimated_hours')
        description = [
            f"Module: {row.get('module_name', '')}",
            f"Weighting: {weighting}%" if weighting is not None else 'Weighting: not specified',
            f"Estimated hours: {hours}" if hours is not None else 'Estimated hours: not specified',
            f"Source quote: {row.get('source_quote') or ''}",
            f"Notes: {row.get('notes') or ''}",
        ]
        event.add('description', '\n'.join(description))
        event.add('transp', 'TRANSPARENT')
        if row.get('due_time') is None:
            event.add('dtstart', day)
            event.add('dtend', day + timedelta(days=1))
        else:
            event.add('dtstart', _local_deadline(day, row['due_time']))
            timed_dates.append(day)
        events.append(event)
    if timed_dates:
        calendar.add_component(Timezone.from_tzinfo(
            LONDON, first_date=min(timed_dates) - timedelta(days=366),
            last_date=max(timed_dates) + timedelta(days=366),
        ))
    for event in events:
        calendar.add_component(event)
    return calendar.to_ical()
