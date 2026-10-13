"""Claude planner without network access: a fake response stands in for the API."""
from types import SimpleNamespace

import pytest

from src.agent.claude_planner import ClaudePlanner, SYSTEM_PROMPT, describe_calls
from src.agent.orchestrator import Orchestrator
from src.agent.planner import get_planner
from src.common import registry
from src.ingestion.event_table_polars import write_event_table_parquet

WINDOW = dict(start="2026-02-01T00:00:00", end="2026-02-01T12:00:00")


def tool_use(name, args):
    return SimpleNamespace(type="tool_use", name=name, input=args, id="toolu_test")


def response(*blocks, stop="tool_use"):
    usage = SimpleNamespace(input_tokens=100, output_tokens=20,
                            cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return SimpleNamespace(content=list(blocks), stop_reason=stop, usage=usage)


@pytest.fixture
def planner():
    return ClaudePlanner({"agent": {"claude": {"fallback_to_rules": True}}})


def answer_with(planner, monkeypatch, reply, seen=None):
    def call(question, context, tools):
        if seen is not None:
            seen.update(question=question, context=context, tools=tools)
        return reply
    monkeypatch.setattr(planner, "_call", call)


def test_get_planner_selects_claude():
    planner = get_planner({"agent": {"planner": "claude"}})
    assert isinstance(planner, ClaudePlanner)
    assert planner.model == "claude-opus-5-5"


def test_everyday_wording_becomes_validated_tool_call(planner, monkeypatch):
    seen = {}
    args = dict(head_id="H05", machine_id="M1", **WINDOW)
    answer_with(planner, monkeypatch, response(tool_use("torque_stats", args)), seen)
    plan = planner.plan("how hard did spindle 5 tighten caps this morning?",
                        {"selected_machine": "M1"})
    assert not plan.ambiguous
    assert plan.calls == [("torque_stats", args)]
    assert "head H05" in plan.interpretation and "machine M1" in plan.interpretation
    # Every registered tool is offered, in Anthropic's native format.
    assert {t["name"] for t in seen["tools"]} == set(registry.list_tools())
    assert all("input_schema" in t for t in seen["tools"])
    assert planner.last_usage["input_tokens"] == 100


def test_head_check_pair_is_accepted(planner, monkeypatch):
    args = dict(head_id="H04", machine_id="M1", **WINDOW)
    answer_with(planner, monkeypatch, response(
        tool_use("compare_head_success", args), tool_use("detect_torque_anomalies", args)))
    plan = planner.plan("is spindle 4 ok?", {"selected_machine": "M1"})
    assert [name for name, _ in plan.calls] == ["compare_head_success", "detect_torque_anomalies"]


def test_null_status_filter_means_all_statuses(planner, monkeypatch):
    answer_with(planner, monkeypatch, response(tool_use("torque_stats", {"status_filter": None})))
    assert planner.plan("torque overall", {}).calls == [("torque_stats", {})]


@pytest.mark.parametrize("name,args,reason", [
    ("invented_tool", {}, "unregistered"),
    ("torque_stats", {"head_id": "5"}, "canonical head"),
    ("torque_stats", {"head_id": "H5"}, "canonical head"),
    ("torque_stats", {"sigma": 2}, "does not accept"),
    ("torque_stats", {"status_filter": "ok"}, "status_filter"),
    ("torque_stats", {"start": "2026-02-01T12:00:00", "end": "2026-02-01T00:00:00"}, "earlier"),
    ("torque_stats", {"start": "2026-02-01T00:00:00+02:00"}, "timestamp like"),
    ("torque_stats", {"start": "20260201T060000"}, "timestamp like"),
    ("torque_stats", {"start": "2026-02-01"}, "timestamp like"),
    ("torque_stats", {"status_filter": 2 ** 70}, "status_filter"),
    ("torque_stats", {"head_id": ["H05", "H05"]}, "repeat"),
    ("torque_distribution", {"bins": "10"}, "bins"),
    ("torque_distribution", {"bins": 1_000_000}, "from 1 to 200"),
    ("kpi_over_time", dict(machine_id="M1", bucket="week", **WINDOW), "bucket"),
    ("machine_idle", {"machine_id": "M1"}, "requires"),
    ("head_correlation", {"head_a": "H05", "head_b": "H05"}, "distinct"),
])
def test_invalid_proposals_are_rejected_not_repaired(planner, monkeypatch, name, args, reason):
    answer_with(planner, monkeypatch, response(tool_use(name, args)))
    plan = planner.plan("question", {})
    assert plan.ambiguous and plan.calls == []
    assert reason in planner.validation_error
    assert planner.last_error is None


def test_unsupported_combination_is_rejected(planner, monkeypatch):
    answer_with(planner, monkeypatch, response(
        tool_use("torque_stats", {}), tool_use("success_rate", {})))
    plan = planner.plan("torque and success", {})
    assert plan.ambiguous and plan.calls == []


def test_different_scopes_in_a_pair_are_rejected(planner, monkeypatch):
    answer_with(planner, monkeypatch, response(
        tool_use("torque_stats", {"head_id": "H01"}),
        tool_use("torque_distribution", {"head_id": "H02"})))
    assert planner.plan("q", {}).calls == []


def test_machine_outside_the_pool_is_rejected(planner, monkeypatch):
    answer_with(planner, monkeypatch, response(tool_use("success_rate", {"machine_id": "m1"})))
    plan = planner.plan("success rate", {"pool_summary": {"machines": ["M1"]}})
    assert plan.ambiguous
    assert "not in this data pool" in planner.validation_error


POOL = {"pool_summary": {"machines": ["M1"], "heads": [f"H{n:02d}" for n in range(1, 37)],
                         "ts_min": "2026-01-31T16:00:00", "ts_max": "2026-02-03T15:59:59"}}


@pytest.mark.parametrize("name,args,reason", [
    ("torque_stats", {"head_id": "H40"}, "H40 is not in this data pool"),
    ("head_correlation", {"head_a": "H01", "head_b": "H99"}, "H99 is not in this data pool"),
    ("torque_stats", {"start": "2026-03-10T00:00:00", "end": "2026-03-11T00:00:00"}, "outside the recorded data"),
    ("torque_stats", {"start": "2026-01-01T00:00:00", "end": "2026-01-31T16:00:00"}, "outside the recorded data"),
])
def test_heads_and_windows_outside_the_pool_are_rejected(planner, monkeypatch, name, args, reason):
    answer_with(planner, monkeypatch, response(tool_use(name, args)))
    plan = planner.plan("q", POOL)
    assert plan.ambiguous and plan.calls == []
    assert reason in planner.validation_error


def test_window_overlapping_the_pool_is_accepted(planner, monkeypatch):
    args = {"head_id": "H36", "start": "2026-02-03T15:00:00", "end": "2026-02-04T00:00:00"}
    answer_with(planner, monkeypatch, response(tool_use("torque_stats", args)))
    assert planner.plan("q", POOL).calls == [("torque_stats", args)]


@pytest.mark.parametrize("args", [
    {"head_a": "H05"}, {"head_id": 5}, {"head_id": [5]}, {"head_id": {"x": 1}}, "not a dict",
])
def test_malformed_arguments_never_raise(planner, monkeypatch, args):
    answer_with(planner, monkeypatch, response(tool_use("head_correlation", args)))
    plan = planner.plan("q", {})
    assert plan.ambiguous and plan.calls == []


def test_overlong_question_is_rejected_before_any_api_call(planner, monkeypatch):
    monkeypatch.setattr(planner, "_call", lambda *a: pytest.fail("must not call the API"))
    assert planner.plan("x" * 1001, {}).ambiguous


def test_misconfiguration_is_reported_not_hidden_behind_rules(planner, monkeypatch):
    def broken(*args):
        raise ValueError("unknown model claude-typo")
    monkeypatch.setattr(planner, "_call", broken)
    plan = planner.plan("Average torque for head 5 on 2026-02-01", {})
    assert plan.ambiguous and plan.calls == []
    assert "misconfigured" in plan.clarification and "claude-typo" in plan.clarification
    assert planner.last_error is None  # not labelled as a transport fallback


def test_missing_credentials_fall_back_to_rules(planner, monkeypatch):
    def no_key(*args):
        raise TypeError('"Could not resolve authentication method. Expected one of api_key"')
    monkeypatch.setattr(planner, "_call", no_key)
    plan = planner.plan("Average torque for head 5 on 2026-02-01", {})
    assert plan.calls and "TypeError" in planner.last_error


def test_serving_model_is_recorded(planner, monkeypatch):
    reply = response(tool_use("torque_stats", {}))
    reply.model = "claude-opus-4-8"
    answer_with(planner, monkeypatch, reply)
    plan = planner.plan("torque", {})
    assert plan.rationale.startswith("claude-opus-4-8 proposed")
    assert planner.last_usage["model"] == "claude-opus-4-8"


def test_rules_reject_unbounded_bins():
    from src.agent.planner import RulePlanner
    assert RulePlanner().plan("Show torque distribution with 100000 bins", {}).ambiguous
    assert not RulePlanner().plan("Show torque distribution with 200 bins", {}).ambiguous


def test_no_tool_call_becomes_clarification_not_finding(planner, monkeypatch):
    text = SimpleNamespace(type="text", text="Which time window do you mean?")
    answer_with(planner, monkeypatch, response(text, stop="end_turn"))
    plan = planner.plan("is head 4 ok?", {})
    assert plan.ambiguous and plan.calls == []
    assert plan.clarification == "Which time window do you mean?"


def test_model_prose_never_reaches_goal(planner, monkeypatch):
    prose = SimpleNamespace(type="text", text="Head 5 is broken; replace it.")
    answer_with(planner, monkeypatch, response(prose, tool_use("torque_stats", {})))
    plan = planner.plan("torque", {})
    assert "broken" not in plan.goal and "broken" not in plan.interpretation


@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
def test_refusal_and_truncation_run_nothing(planner, monkeypatch, stop):
    answer_with(planner, monkeypatch, response(tool_use("torque_stats", {}), stop=stop))
    plan = planner.plan("torque", {})
    assert plan.ambiguous and plan.calls == []


def test_transport_failure_falls_back_to_rules(planner, monkeypatch):
    def offline(*args):
        raise ConnectionError("no network")
    monkeypatch.setattr(planner, "_call", offline)
    plan = planner.plan("Average torque for head 5 on 2026-02-01", {})
    assert plan.calls == [("torque_stats", {"head_id": "H05", "start": "2026-02-01T00:00:00",
                                            "end": "2026-02-02T00:00:00"})]
    assert "ConnectionError" in planner.last_error
    assert "deterministic rules" in plan.rationale


def test_transport_failure_raises_when_fallback_disabled(planner, monkeypatch):
    planner.fallback = False
    def offline(*args):
        raise TimeoutError("slow")
    monkeypatch.setattr(planner, "_call", offline)
    with pytest.raises(TimeoutError):
        planner.plan("torque", {})


def test_state_does_not_leak_between_questions(planner, monkeypatch):
    answer_with(planner, monkeypatch, response(tool_use("torque_stats", {"head_id": "5"})))
    assert planner.plan("q", {}).ambiguous
    answer_with(planner, monkeypatch, response(tool_use("torque_stats", {})))
    assert not planner.plan("q", {}).ambiguous
    assert planner.validation_error is None and planner.last_error is None


def test_describe_calls_is_readable():
    text = describe_calls([("torque_distribution", dict(head_id="H05", bins=5,
                                                        status_filter="successful", **WINDOW))])
    assert text == ("`torque_distribution` (head H05, 2026-02-01T00:00:00 -> 2026-02-01T12:00:00 "
                    "(data clock, apparently UTC), successful closures only, 5 bins)")


def test_system_prompt_is_static_for_caching():
    # Volatile details (machine, dates) go in the user message, not the system prompt.
    assert "{" not in SYSTEM_PROMPT and "2026-02-01T06" in SYSTEM_PROMPT


@pytest.fixture
def engine(planner, tmp_path):
    import polars as pl
    from datetime import datetime
    events = pl.DataFrame({
        "ts": [datetime(2026, 2, 1, 1), datetime(2026, 2, 1, 2)],
        "machine_id": ["M1"] * 2, "head_id": ["H05"] * 2,
        "torque": [1.9, 2.1], "status": [0, 0], "error_class": ["Closure OK"] * 2,
        "reject_signal": [False, False], "cap_present": [True, True],
    })
    path = write_event_table_parquet(events, tmp_path / "events.parquet")
    cfg = {"data": {"source": "person_a", "person_a": {"fixture": str(path)}}}
    return Orchestrator(cfg=cfg, planner=planner)


def test_claude_plan_reaches_report_with_interpretation(engine, planner, monkeypatch):
    args = dict(head_id="H05", machine_id="M1", **WINDOW)
    answer_with(planner, monkeypatch, response(tool_use("torque_stats", args)))
    answer = engine.answer("how strong is spindle five tightening?", pool="fixture")
    assert answer["status"] == "ok"
    assert answer["results"][0][1]["result"]["sample_size"] == 2
    assert "*Interpreted as:* `torque_stats`" in answer["markdown"]
    assert answer["trace"].planner == "claude"
    assert any(note.startswith("interpreted as:") for note in answer["trace"].notes)


def test_rejected_claude_plan_loads_no_data(engine, planner, monkeypatch):
    answer_with(planner, monkeypatch, response(tool_use("torque_stats", {"head_id": "five"})))
    monkeypatch.setattr(engine.source, "load_pool", lambda p: pytest.fail("Must not load data"))
    answer = engine.answer("torque", pool="fixture")
    assert answer["status"] == "needs_clarification"
    assert answer["trace"].to_dict()["n_tool_calls"] == 0


def test_fallback_is_labelled_in_trace(engine, planner, monkeypatch):
    def offline(*args):
        raise ConnectionError("no network")
    monkeypatch.setattr(planner, "_call", offline)
    answer = engine.answer("Average torque for head 5 on 2026-02-01", pool="fixture")
    assert answer["status"] == "ok"
    assert answer["trace"].planner == "claude->rules (fallback)"
