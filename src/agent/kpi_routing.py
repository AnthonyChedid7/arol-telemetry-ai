"""Strict deterministic KPI requests reuse the existing validated scope grammar."""
import re
from .torque_routing import _parse_canonical_torque_request


def parse_kpi_request(query):
    text = ' '.join((query or '').split()).rstrip('.?!')
    # Intercept unsupported KPI phrasings before legacy keyword routing can
    # silently discard their scope. Torque routing runs first in RulePlanner.
    if not re.search(r'\b(?:success|successful|reject|rejection|failure|kpis?|quality|performance|performing|no[ -]load)\b', text, re.I):
        return None
    def clarify():
        return dict(goal='Clarify the KPI and full observation scope.', ambiguous=True,
                    clarification=("Ask for 'success rate', 'success rate per head', 'reject rate', 'failure rate for each head', 'No Load rate', or 'capping KPIs', "
                                   "with optional 'for head 5', 'for machine M1', 'on YYYY-MM-DD', or "
                                   "'from <ISO> until <ISO>'. Rates require all statuses within that scope. "
                                   "Status prefilters, relative dates, time buckets, rankings and root-cause questions are not supported here."),
                    rationale='unsupported or conflicting KPI scope; no tools called')
    prefix = r'(?:(?:show me|show|what is|what are) (?:the )?)?'
    base = re.match(prefix + r'(?:success rate|reject(?:ion)? rate|failure rate|no[ -]load rate|capping kpis|kpis)(?=\s|$)', text, re.I)
    if base is None:
        return clarify()
    tail = text[base.end():].strip()
    per_head = re.match(r'(?:(?:per|by) head|for (?:each|every) head)(?=\s|$)', tail, re.I)
    if per_head:
        tail = tail[per_head.end():].strip()
    # 'for all closures' is deliberately rejected as a status clause too;
    # this avoids silently treating a denominator choice as a row filter.
    if re.search(r'\b(?:status|closures|bins)\b', tail, re.I):
        return clarify()
    plan = _parse_canonical_torque_request('Average torque ' + tail)
    if plan is None or plan.get('ambiguous'):
        return clarify()
    params = plan['calls'][0][1]
    if set(params) - {'head_id', 'machine_id', 'start', 'end'}:
        return clarify()
    tools = ['success_rate_per_head'] if per_head else ['success_rate', 'success_rate_per_head']
    failure_wording = re.search(r'\bfailure rate\b', base.group(), re.I) is not None
    reject_wording = re.search(r'\breject(?:ion)? rate\b', base.group(), re.I) is not None
    return dict(goal=('Report cap-present rejection flags as the requested per-head failure-rate interpretation.'
                      if failure_wording else
                      'Describe observed-event KPIs with explicit denominators within the requested scope.'),
                calls=[(name, dict(params)) for name in tools], filters=dict(params),
                rationale='parsed the complete KPI question and preserved all scope; all statuses retained',
                interpretation=('Failure rate means rejection flags among confirmed cap-present observed events; '
                                'No Load and unknown cap presence are reported separately.'
                                if failure_wording else ''),
                focus='failure' if failure_wording or reject_wording else '')
