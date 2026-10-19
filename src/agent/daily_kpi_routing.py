"""Per-day head questions: "which head had the lowest success rate each day"."""
import re
from datetime import datetime

DAY = re.compile(r'\b(?:(?:each|every|per|by) day|day by day|daily|for each of the days)\b', re.I)
HEADS = re.compile(r'\bheads?\b', re.I)
METRIC = re.compile(r'\b(?:success(?:ful)?|fail(?:s|ed|ing|ures?)?|reject(?:s|ed|ions?)?|errors?|perform(?:s|ed|ing|ance)?|worst|best|'
                    r'lowest|highest|rank(?:ed|ing|s)?|weakest)\b', re.I)
FAILURE = re.compile(r'\b(?:fail(?:s|ed|ing|ures?)?|reject(?:s|ed|ions?)?|errors?)\b', re.I)
MACHINE = re.compile(r'\bfor machine (\S+)', re.I)
WINDOW = re.compile(r'\b(?:from|between) (\S+) (?:until|to|and) (\S+)', re.I)


def _iso(value):
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.isoformat() if parsed.tzinfo is None else None


RATE = re.compile(r'\b(?:success(?:ful)?|fail(?:s|ed|ing|ures?)?|reject(?:s|ed|ions?)?|errors?)\b', re.I)
EXTREME = re.compile(r'\b(?:lowest|highest|best|worst|weakest|least|most|fewest|minimum|maximum|top)\b', re.I)


def parse_daily_kpi_request(query):
    text = ' '.join((query or '').split()).rstrip('.?!')
    # Heads named, or a rate asked for at one end ("the lowest error rates on each
    # day"). Plain "success rate by day for machine M1 ..." stays with the
    # explicit hourly/daily KPI grammar.
    if not DAY.search(text) or not ((HEADS.search(text) and METRIC.search(text))
                                    or (RATE.search(text) and EXTREME.search(text))):
        return None
    # One named head or an hourly breakdown is a different question.
    if re.search(r'\b(?:head|spindle|h)\s*\d+\b', text, re.I) or re.search(r'\bhour', text, re.I):
        return None
    params = {}
    if machine := MACHINE.search(text):
        params['machine_id'] = machine.group(1)
    if window := WINDOW.search(text):
        start, end = _iso(window.group(1)), _iso(window.group(2))
        if start is None or end is None or start >= end:
            return dict(goal='Clarify the period for the per-day head results.', ambiguous=True,
                        clarification=("Give the period as 'from YYYY-MM-DDTHH:MM:SS until "
                                       "YYYY-MM-DDTHH:MM:SS' (UTC, start before end), or leave it out to "
                                       "use every day in the data."),
                        rationale='per-day head question with an unreadable period; no tools called')
        params.update(start=start, end=end)
    focus = 'failure' if FAILURE.search(text) and not re.search(r'\bsuccess', text, re.I) else ''
    scope = (f"from {params['start']} until {params['end']}" if 'start' in params
             else 'every day in the data')
    return dict(goal=('Report, for each calendar day, every head\'s cap-present success and failure '
                      'rate and the day\'s best and worst heads.'),
                calls=[('head_success_by_day', dict(params))], filters=dict(params),
                rationale='per-day head question; each day is analysed with its own query',
                interpretation=(f'Each calendar day ({scope}; UTC days, 00:00-24:00) is analysed '
                                'separately: per-head cap-present success and failure (error) rates.'),
                focus=focus)
