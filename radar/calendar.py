"""UCL 2026/27 calendar and auditable handbook week resolution.

Calendar weeks include reading weeks; teaching weeks skip them. Timetable
week 1 starts 31 August 2026, per UCL's academic timetable calendar:
https://www.ucl.ac.uk/srs/sites/srs/files/ucl_calendar_2026_27v2.pdf
"""

from dataclasses import dataclass
from datetime import date, timedelta
import re
from typing import Literal

Scheme = Literal['calendar', 'teaching', 'timetable']
TIMETABLE_START = date(2026, 8, 31)


@dataclass(frozen=True)
class Term:
    start: date
    end: date
    reading_week: date | None = None


TERMS = {
    1: Term(date(2026, 9, 28), date(2026, 12, 18), date(2026, 11, 9)),
    2: Term(date(2027, 1, 11), date(2027, 3, 24), date(2027, 2, 15)),
    3: Term(date(2027, 4, 26), date(2027, 6, 11)),
}


@dataclass(frozen=True)
class WeekResolution:
    date: date
    confidence: Literal['high', 'medium', 'low']
    source_quote: str
    reason: str
    alternatives: tuple[date, ...] = ()


def _term(number: int) -> Term:
    if type(number) is not int or number not in TERMS:
        raise ValueError('term must be 1, 2 or 3')
    return TERMS[number]


def term_weeks(term: int | None = None) -> list[date]:
    """Ordered Mondays, including reading weeks and final partial weeks."""
    selected = [_term(term)] if term is not None else list(TERMS.values())
    return [t.start + timedelta(weeks=i) for t in selected
            for i in range((t.end - t.start).days // 7 + 1)]


def week_to_date(term: int, week_number: int, scheme: Scheme = 'calendar') -> date:
    """Return the Monday for an explicit numbering scheme; reject overflow."""
    t = _term(term)
    if type(week_number) is not int or week_number < 1:
        raise ValueError('week_number must be a positive integer')
    if scheme == 'timetable':
        monday = TIMETABLE_START + timedelta(weeks=week_number - 1)
        if not t.start <= monday <= t.end:
            raise ValueError('timetable week is outside the requested term')
        return monday
    if scheme not in ('calendar', 'teaching'):
        raise ValueError('unknown week numbering scheme')
    weeks = term_weeks(term)
    if scheme == 'teaching':
        weeks = [w for w in weeks if w != t.reading_week]
    if week_number > len(weeks):
        raise ValueError('week is past the end of term')
    return weeks[week_number - 1]


def date_to_week(d: date) -> tuple[int, int] | None:
    """Return (term, calendar week), or None outside stated term dates."""
    for number, t in TERMS.items():
        if t.start <= d <= t.end:
            return number, (d - t.start).days // 7 + 1
    return None


def is_reading_week(d: date) -> bool:
    return any(t.reading_week is not None and
               0 <= (d - t.reading_week).days < 7 for t in TERMS.values())


def resolve_week_reference(
    raw: str, term: int | None = None, scheme: Scheme | None = None,
) -> WeekResolution:
    """Resolve supported week phrases while retaining the original quote.

    Bare weeks default to Term 1 with low confidence. Unspecified schemes
    prefer calendar weeks, falling back to timetable numbering only when the
    calendar week is out of range, with low confidence for either assumption.
    Explicit timetable weeks infer their term from the academic-year anchor.
    Unsupported or conflicting references raise rather than invent a date.
    """
    if term is not None:
        _term(term)
    if scheme is not None and scheme not in ('calendar', 'teaching', 'timetable'):
        raise ValueError('unknown week numbering scheme')
    text = raw.strip().lower()
    term_refs = re.findall(r'\bterm\s*([123])\b', text)
    if len(term_refs) > 1:
        raise ValueError('multiple term references')
    if term_refs:
        explicit_term = int(term_refs[0])
        if term is not None and term != explicit_term:
            raise ValueError('conflicting term references')
        term = explicit_term

    commencing = re.fullmatch(
        r'(?:week commencing|w/c)\s+(\d{1,2})\s+([a-z]+)(?:\s+(\d{4}))?', text,
    )
    if commencing:
        day, month, year = commencing.groups()
        months = ('january february march april may june july august september '
                  'october november december').split()
        if month not in months:
            raise ValueError('unknown month')
        month_number = months.index(month) + 1
        inferred_year = 2026 if month_number >= 9 else 2027
        monday = date(int(year) if year else inferred_year, month_number, int(day))
        mapped = date_to_week(monday)
        if monday.weekday() != 0 or mapped is None or (term and mapped[0] != term):
            raise ValueError('week commencing date must be a Monday within the term')
        return WeekResolution(monday, 'high', raw, 'Explicit week commencing date')

    match = re.fullmatch(
        r'(?:term\s*[123]\s+)?(?:(ucl\s+)?(calendar|teaching|timetable)\s+)?'
        r'week\s+(\d+)', text,
    )
    if match is None:
        raise ValueError('unsupported week reference')
    _, stated_scheme, number = match.groups()
    if stated_scheme and scheme and stated_scheme != scheme:
        raise ValueError('conflicting numbering schemes')
    chosen_scheme = stated_scheme or scheme
    week = int(number)
    assumed_term = term is None and chosen_scheme != 'timetable'
    if term is None and chosen_scheme == 'timetable':
        mapped = date_to_week(TIMETABLE_START + timedelta(weeks=week - 1))
        if mapped is None:
            raise ValueError('timetable week is outside teaching terms')
        term = mapped[0]
    term = term or 1
    assumed_scheme = 'calendar'
    try:
        monday = week_to_date(term, week, chosen_scheme or assumed_scheme)
    except ValueError:
        if chosen_scheme is not None:
            raise
        # A handbook may omit "UCL timetable" before an academic-year week
        # number. Keep the requested term constraint and disclose the fallback.
        assumed_scheme = 'timetable'
        monday = week_to_date(term, week, assumed_scheme)
    alternatives = []
    if chosen_scheme is None:
        for candidate_scheme in ('teaching', 'timetable'):
            try:
                candidate = week_to_date(term, week, candidate_scheme)
            except ValueError:
                continue
            if candidate != monday and candidate not in alternatives:
                alternatives.append(candidate)
    ambiguous = assumed_term or chosen_scheme is None
    reason = 'Explicit term and numbering scheme'
    if ambiguous:
        reason = f'Assumed {assumed_scheme} numbering' if chosen_scheme is None else 'Explicit numbering'
        if assumed_term:
            reason += '; assumed Term 1 because no term was supplied'
        reason += '; check against the handbook'
    return WeekResolution(monday, 'low' if ambiguous else 'high', raw, reason,
                          tuple(alternatives))
