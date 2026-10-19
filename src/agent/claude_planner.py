"""Claude planner: free-form questions -> registered tool calls via the Anthropic API.

The rule planners only accept the exact phrasings their regular expressions
know. This planner lets Claude read the question in everyday language and pick
tools and arguments from the same registry the rules use. What does not change:

  * Claude never computes a number. Tools do the arithmetic; the report shows
    tool results only.
  * Every proposal is validated before any data is loaded (registered tool,
    known argument names, canonical types, supported tool combinations). An
    invalid proposal is rejected, never repaired.
  * Claude's prose is used only as a clarification question, never as a finding.
  * The report states how the question was interpreted, so a reader can check
    that Claude understood it.

Only the question text, the tool descriptions and the pool's machine/time range
are sent to Anthropic. No telemetry rows or tool results leave the machine.
If the API is unreachable or no key is configured, the deterministic planner
answers instead and the trace says so.
"""

from __future__ import annotations

from ..common import registry
from .planner import Plan, Planner
from .pool_scope import validate_pool_scope

DEFAULT_MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = """\
You are the planning step of a telemetry analysis tool for AROL capping machines \
(machines that screw caps onto bottles). A user asks a question in everyday \
language. Your only job is to choose which of the provided analysis tools to run \
and with which arguments. The tools compute every number; you never compute, \
estimate or describe results yourself.

Domain vocabulary (users will not use the exact tool names):
- A machine has several capping heads (also called spindles, chucks or capping \
units). Head 5, spindle 5, H5 and H05 all mean head_id "H05". Always write heads \
as "H" plus at least two digits: H01, H05, H12.
- Torque is the tightening / closing force applied to a cap, in Nm. Questions \
about tightening force, how hard caps are screwed, closing torque or torque \
values are torque questions.
- A closure event is one cap being applied. Status 0 is a successful closure. \
"Good closures", "successful closures", "OK caps" mean status_filter \
"successful". Other integer status codes are errors; use that integer only when \
the user names an exact code. Questions about all closures need no status filter: \
torque tools then exclude "No Load" cycles (the head cycled without a cap, torque \
about 0 Nm) by default. Use status_filter "all_statuses" only when the user \
explicitly wants No Load cycles included.
- Success rate, yield, failure/reject/error rate and quality per head are KPI \
questions (success_rate, success_rate_per_head). "Which head is worst/best" \
means rank_heads_by_success. "Is head N worse than the others", "how does head N \
compare to its peers" means compare_head_success. Comparing the torque of two \
specific heads means head_correlation.
- Any head question asked per day, for each day, every day, daily or day by day \
("worst head each day", "lowest success rate per head for each day", "which head \
failed most every day") means head_success_by_day. It analyses each calendar day \
separately, so omit start and end to cover all the data, or give the requested \
period. Do not use rank_heads_by_success for these: it covers only one window.
- Rankings and comparisons (which head is best/worst, highest/lowest rate \
overall, compare a head with the others) with no time reference mean all the \
data: use the data range from the context as start and end (start = first \
timestamp, end = one second after the last), and the per-head tools \
(success_rate_per_head, rank_heads_by_success) for "each head".
- Throughput, output, production rate, caps per hour mean observed_throughput. \
Success rate or KPIs "per hour", "by hour", "over time", "hour by hour" mean \
kpi_over_time. Both need bucket "hour" or "day".
- Idle time, standstill, stops, machine not working, No Load periods mean \
machine_idle.
- Drift, trend, is torque changing over time mean torque_trend. Outliers, \
anomalies, strange or abnormal values mean detect_torque_anomalies. Spread, \
histogram, distribution mean torque_distribution. Average, mean, min, max, \
summary, statistics mean torque_stats.
- "Is anything wrong with head N", "is head N healthy", "check head N" means a \
head check: call BOTH compare_head_success and detect_torque_anomalies with the \
same head_id, machine_id, start and end, and no status filter.

Arguments:
- Use only argument names from the tool schemas. Omit any argument the user did \
not ask for; do not add filters on your own. Never send null placeholders or \
empty strings.
- Time bounds are naive ISO timestamps with seconds, for example \
2026-02-01T06:00:00. start is inclusive, end is exclusive. A single day means \
00:00:00 that day until 00:00:00 the next day. "Morning" means 06:00-12:00, \
"afternoon" 12:00-18:00, "evening" 18:00-24:00, "night" 00:00-06:00, "the first \
hour" means the first hour of the data. Resolve dates against the data range \
given in the request context, not against today's real date. Recorded data has \
no "today": if the user uses a relative day (today, yesterday, last night, this \
week, recently) and the data covers more than one calendar day, do not pick a \
day; ask which date they mean. Only when the data covers a single calendar day \
may "today" or "this morning" refer to that day. Positional phrases tied to \
the data ("the first hour of data", "the last day", "all the data") are fine.
- When the context names the machine in this data pool and the user does not \
name one, use that machine_id exactly (machine ids are case-sensitive).
- Thresholds, windows, sigma values and limits are configured by the \
application and are never arguments. If the user explicitly asks for a setting \
the tools do not accept (a sigma, threshold, window length, limit, shift \
definition, or anything else missing from the schemas), do not run the analysis \
without it: reply that this setting is fixed by the application configuration \
and ask whether to run with the configured value.
- Head identifiers must be heads listed in the context, and time windows must \
overlap the data range given in the context. Otherwise ask.

Combining tools: call one tool, or at most two tools from these pairs, with \
exactly the same scope arguments: torque_stats + torque_distribution; \
torque_trend + detect_torque_anomalies; success_rate + success_rate_per_head; \
compare_head_success + detect_torque_anomalies (head check).

If the question cannot be answered with the tools, needs a combination not \
listed, or lacks something a tool requires (for example a time window for a \
tool that requires start and end, when the question gives no time reference at \
all), do not call any tool. Instead reply with one short clarification question \
saying what is missing. Never guess a time window the user did not imply; the one \
exception is rankings and comparisons, which mean all the data (see above).\
"""

_FRIENDLY_STATUS = {"successful": "successful closures only",
                    "all_statuses": "every status, including No Load cycles"}


def _text(value) -> str:
    return ", ".join(map(str, value)) if isinstance(value, (list, tuple)) else str(value)


def describe_calls(calls) -> str:
    """Deterministic, human-readable summary of what will run.

    Also used for rejected proposals, so it must accept any argument shapes.
    """
    parts = []
    for name, args in calls:
        args = args if isinstance(args, dict) else {}
        bits = []
        if "head_a" in args or "head_b" in args:
            bits.append(f"heads {_text(args.get('head_a', '?'))} vs {_text(args.get('head_b', '?'))}")
        if "head_id" in args:
            bits.append("head " + _text(args["head_id"]))
        if "machine_id" in args:
            bits.append(f"machine {args['machine_id']}")
        if "start" in args or "end" in args:
            bits.append(f"{args.get('start', 'beginning')} -> {args.get('end', 'end of data')} "
                        "(UTC)")
        if "status_filter" in args:
            value = args["status_filter"]
            bits.append(_FRIENDLY_STATUS.get(value, f"status {value}"))
        if "bucket" in args:
            bits.append(f"per {args['bucket']}")
        if "bins" in args:
            bits.append(f"{args['bins']} bins")
        parts.append(f"`{name}`" + (" (" + ", ".join(bits) + ")" if bits else " (all data)"))
    return "; ".join(parts)


def _claude_unreachable(exc) -> bool:
    """True when Claude cannot answer right now, so the rules should.

    Anything else (bad request, unknown model, rejected beta, a bug) is a
    misconfiguration that must stay visible instead of silently routing every
    question through the rules.
    """
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    import anthropic
    if isinstance(exc, (anthropic.APIConnectionError, anthropic.RateLimitError,
                        anthropic.AuthenticationError, anthropic.PermissionDeniedError,
                        anthropic.CredentialsError)):
        return True
    if isinstance(exc, anthropic.APIStatusError) and exc.status_code >= 500:
        return True
    # Without any credentials the SDK raises TypeError before sending.
    return isinstance(exc, TypeError) and "authentication" in str(exc).lower()


def _context_text(context: dict) -> str:
    lines = []
    machine = context.get("selected_machine")
    summary = context.get("pool_summary") or {}
    machines = summary.get("machines") or ([machine] if machine else [])
    if machine:
        lines.append(f"Selected machine: {machine}")
    elif len(machines) == 1:
        lines.append(f"This data pool contains one machine: {machines[0]}")
    elif machines:
        lines.append("Machines in this data pool: " + ", ".join(machines))
    if summary.get("ts_min") and summary.get("ts_max"):
        lines.append(f"Data available from {summary['ts_min']} to {summary['ts_max']} "
                     "(timestamps are UTC as recorded; use this clock for all times)")
    if summary.get("heads"):
        lines.append("Heads in this data pool: " + ", ".join(summary["heads"]))
    return "\n".join(lines) or "No pool details are available."


class ClaudePlanner(Planner):
    """Plan with Claude; fall back to deterministic rules if the API is unavailable."""

    name = "claude"

    def __init__(self, cfg, client=None):
        self.cfg = cfg
        settings = (cfg.get("agent", {}) or {}).get("claude", {}) or {}
        self.model = settings.get("model", DEFAULT_MODEL)
        self.effort = settings.get("effort", "low")
        self.max_tokens = int(settings.get("max_tokens", 16000))
        self.timeout = float(settings.get("timeout_seconds", 30))
        self.max_retries = int(settings.get("max_retries", 1))
        self.max_question_chars = int(settings.get("max_question_chars", 1000))
        self.fallback = bool(settings.get("fallback_to_rules", True))
        # Claude also words the direct answer from the computed summary results
        # (counts and rates, never raw rows); false keeps the built-in answer.
        self.answer_from_results = bool(settings.get("answer_from_results", True))
        # A key a website visitor typed for this session only (never read from
        # files: config.load refuses keys in config). Otherwise the SDK uses
        # ANTHROPIC_API_KEY from the environment or the project's .env file.
        self._session_api_key = settings.get("session_api_key") or None
        self._client = client
        self.last_error: str | None = None
        self.validation_error: str | None = None
        self.dropped_args: list[str] = []
        self.last_usage: dict | None = None

    # -- transport ---------------------------------------------------------

    def _client_or_create(self):
        if self._client is None:
            import anthropic
            # Bounded so an outage reaches the rules fallback in about a minute.
            options = dict(timeout=self.timeout, max_retries=self.max_retries)
            if self._session_api_key:
                options["api_key"] = self._session_api_key
            self._client = anthropic.Anthropic(**options)
        return self._client

    def _call(self, question: str, context: dict, tools: list[dict]):
        """One Messages API request. Overridden in tests."""
        return self._client_or_create().beta.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            # Tools + system prompt are identical for every question, so they
            # are cached; only the short context/question is billed in full.
            system=[{"type": "text", "text": SYSTEM_PROMPT,
                     "cache_control": {"type": "ephemeral"}}],
            tools=tools,
            tool_choice={"type": "auto"},
            output_config={"effort": self.effort},
            # A false-positive safety decline is retried on Anthropic's
            # recommended fallback model instead of failing the question.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{
                "role": "user",
                "content": f"<context>\n{_context_text(context)}\n</context>\n\n"
                           f"<question>\n{question}\n</question>",
            }],
        )

    # -- planning ----------------------------------------------------------

    def plan(self, query: str, context: dict) -> Plan:
        from .llm_validation import validate_tool_call

        self.last_error = None
        self.validation_error = None
        self.dropped_args = []
        self.last_usage = None

        if len(query or "") > self.max_question_chars:
            return self._reject(f"the question is longer than {self.max_question_chars} characters")

        tools = registry.get_tool_specs()
        try:
            response = self._call(query, context, tools)
        except Exception as exc:
            if not _claude_unreachable(exc):
                # Misconfiguration or a bug: say so instead of hiding it behind the rules.
                return self._reject(f"the Claude planner is misconfigured "
                                    f"({type(exc).__name__}: {exc})")
            self.last_error = f"{type(exc).__name__}: {exc}"
            if not self.fallback:
                raise
            from .head_diagnostic import HeadDiagnosticPlanner
            plan = HeadDiagnosticPlanner().plan(query, context)
            plan.rationale = (f"Claude unavailable ({type(exc).__name__}); "
                              f"deterministic rules: {plan.rationale}")
            return plan

        # With server-side fallback a different model may have answered.
        served_by = getattr(response, "model", None) or self.model
        usage = getattr(response, "usage", None)
        self.last_usage = {"model": served_by}
        if usage is not None:
            self.last_usage.update({
                key: getattr(usage, key, None) for key in (
                    "input_tokens", "output_tokens",
                    "cache_read_input_tokens", "cache_creation_input_tokens")
            })

        stop = getattr(response, "stop_reason", None)
        if stop == "refusal":
            return self._reject("Claude declined to plan this question")
        if stop == "max_tokens":
            return self._reject("Claude's reply was cut off before the plan was complete")

        blocks = list(getattr(response, "content", None) or [])
        proposals = [b for b in blocks if getattr(b, "type", None) == "tool_use"]
        if not proposals:
            text = " ".join(getattr(b, "text", "") for b in blocks
                            if getattr(b, "type", None) == "text").strip()
            return Plan(
                goal="Clarify the requested analysis and scope.",
                ambiguous=True,
                clarification=text or "Please rephrase the question with the analysis, head, and time window.",
                rationale=f"{served_by} asked for clarification; no tools called",
            )

        try:
            calls = [validate_tool_call(b.name, b.input, registry.get) for b in proposals]
            self._check_combination(calls, context)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            proposed = describe_calls([(b.name, b.input if isinstance(b.input, dict) else {})
                                       for b in proposals])
            return self._reject(str(exc), proposed)

        names = [name for name, _ in calls]
        filters = dict(calls[0][1])
        for name, args in calls[1:]:
            for key, value in args.items():
                filters.setdefault(key, value)
        return Plan(
            goal=f"Run {' and '.join(names)} within the scope interpreted from the question.",
            calls=calls,
            filters=filters,
            rationale=(f"{served_by} proposed {len(calls)} tool call(s); names, types, "
                       "combination and pool scope validated"),
            interpretation=describe_calls(calls),
        )

    def _check_combination(self, calls, context):
        """Reject what the event source would refuse, before any data is loaded."""
        from ..common.event_pool_source import _read_plan
        _read_plan(calls)
        validate_pool_scope(calls, context)

    def _reject(self, reason, proposed=""):
        self.validation_error = reason
        detail = f" Claude proposed: {proposed}." if proposed else ""
        return Plan(
            goal="Clarify the requested analysis without changing its scope.",
            ambiguous=True,
            clarification=("The question could not be turned into a valid analysis, so nothing "
                           f"was run. Reason: {reason}.{detail} Please rephrase or add the "
                           "missing detail (head, machine, or time window)."),
            rationale="Claude proposal rejected: " + reason,
        )

    def explain(self, query, results, report):
        """Reuse the deterministic head-check explanation for the head-check pair."""
        from .head_diagnostic import HeadDiagnosticPlanner
        return HeadDiagnosticPlanner().explain(query, results, report)

    # -- answering ---------------------------------------------------------

    def _call_answer(self, content: str):
        """One Messages API request for the answer. Overridden in tests."""
        return self._client_or_create().beta.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[{"type": "text", "text": ANSWER_PROMPT,
                     "cache_control": {"type": "ephemeral"}}],
            tools=[ANSWER_TOOL],
            tool_choice={"type": "auto"},
            output_config={"effort": self.effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": content}],
        )

    def compose_answer(self, query, plan, results, meta, draft):
        """Claude words the direct answer from the computed results (numbers checked by the caller).

        Sends the question and the tools' summary results (counts and rates; no
        raw telemetry rows). Returns {sentence, artifact, metric} or None.
        """
        if not self.answer_from_results:
            return None
        from .answer import ARTIFACTS, METRICS, TOOL_ARTIFACTS, compact_results
        names = [name for name, reply in results if reply.get("ok")]
        forms = sorted(set().union(*(TOOL_ARTIFACTS.get(n, ("none",)) for n in names)))
        content = (f"<question>\n{query}\n</question>\n\n"
                   f"<interpretation>\n{getattr(plan, 'interpretation', '')}\n</interpretation>\n\n"
                   f"<available_forms>{', '.join(forms)}</available_forms>\n\n"
                   f"<data_range>{(meta or {}).get('ts_min')} to {(meta or {}).get('ts_max')} (UTC as recorded)</data_range>\n\n"
                   f"<results>\n{compact_results(results)}\n</results>\n\n"
                   f"<built_in_answer>\n{draft.get('sentence', '')}\n(form: {draft.get('artifact')}, metric: {draft.get('metric')})\n</built_in_answer>")
        response = self._call_answer(content)
        usage = getattr(response, "usage", None)
        if usage is not None and self.last_usage is not None:
            for key in ("input_tokens", "output_tokens"):
                if getattr(usage, key, None) is not None:
                    self.last_usage[f"answer_{key}"] = getattr(usage, key)
        block = next((b for b in (getattr(response, "content", None) or [])
                      if getattr(b, "type", None) == "tool_use" and getattr(b, "name", "") == "give_answer"), None)
        if block is None or not isinstance(block.input, dict):
            return None
        data = block.input
        sentence = data.get("answer")
        if not isinstance(sentence, str) or not sentence.strip() or len(sentence) > 1500:
            return None
        return dict(sentence=sentence,
                    artifact=data.get("form") if data.get("form") in ARTIFACTS else None,
                    metric=data.get("metric") if data.get("metric") in METRICS else None)


ANSWER_PROMPT = """\
You write the direct answer an engineer sees first, for a question about AROL \
capping-machine telemetry. The analysis already ran; its results are given. \
Answer exactly what was asked, the way a colleague would in conversation, and \
nothing else: no caveats, no method description, no extra findings (those are \
shown separately under Details). Do not add facts the question did not ask for, \
such as which head was lowest, an overall machine rate or a runner-up. When the \
answer is a table or a chart, one short sentence introducing it is the whole \
answer, unless the question also asks something specific.

Rules:
- Use only numbers that appear in the results (you may round them; rates are \
fractions, so 0.000497 is 0.0497%). Never compute new numbers such as sums, \
differences or averages yourself. If the results cannot answer the question, say \
so in one sentence.
- One to three full sentences. Name heads as H05 and days as 2026-02-03.
- "Success rate" means status 0 among closures with a cap; "failure", "error" or \
"reject" rate means rejection flags among the same closures. "Caps closed" means \
closures with a cap (n_cap_present).
- If several heads share the answer value (for example many heads at 0% failure), \
say how many share it and name a few; do not pick one.
- Choose the form that shows what was asked: "chart" if the user asked for a plot, \
graph, chart or visual; "head_day_table" for every head on every day; \
"head_table" for every head; "day_table" for one row per day; "interval_table" for \
per-hour or per-day intervals; "none" when the sentence itself is the answer. Use \
only forms listed in <available_forms>. Pick the metric the table should show.
- A built-in answer is given for reference; improve on it when the question asks \
for something more specific, otherwise keep its facts.

Reply only by calling the give_answer tool.\
"""

ANSWER_TOOL = {
    "name": "give_answer",
    "description": "The direct answer to the user's question and the form to show it in.",
    "input_schema": {
        "type": "object",
        "properties": {
            "answer": {"type": "string", "description": "1-3 full sentences answering exactly the question."},
            "form": {"type": "string", "enum": ["none", "chart", "head_table", "head_day_table",
                                                "day_table", "interval_table"]},
            "metric": {"type": "string", "enum": ["success_rate", "failure_rate", "rejects", "closures"]},
        },
        "required": ["answer", "form", "metric"],
        "additionalProperties": False,
    },
}
