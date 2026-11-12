"""The general local-model planner: Claude's prompt and validation, served by Ollama."""
import json
import urllib.request

import pytest

from src.agent.ollama_planner import OllamaPlanner, _calls_from_text

CFG = {"agent": {"llm": {"model": "qwen2.5:7b", "host": "http://ollama.test:11434"}}}
CONTEXT = {"selected_machine": "M1"}


class Reply:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


@pytest.fixture
def ollama(monkeypatch):
    sent = []

    def answer_with(message):
        def urlopen(request, timeout=0):
            sent.append(request)
            return Reply({"message": message, "prompt_eval_count": 10, "eval_count": 5})
        monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    answer_with.sent = sent
    return answer_with


def test_native_tool_call_becomes_a_validated_plan(ollama):
    ollama({"tool_calls": [{"function": {"name": "success_rate_per_head", "arguments": {}}}]})
    planner = OllamaPlanner(CFG)
    plan = planner.plan("Show me the failure rate for each head", CONTEXT)
    assert [name for name, _ in plan.calls] == ["success_rate_per_head"]
    assert planner.last_error is None
    request = ollama.sent[0]
    body = json.loads(request.data)
    assert request.full_url == "http://ollama.test:11434/api/chat"
    assert request.get_header("User-agent") == "arol-telemetry-planner"
    assert body["model"] == "qwen2.5:7b" and body["options"]["num_ctx"] == 8192
    assert {t["function"]["name"] for t in body["tools"]} >= {"torque_stats", "head_success_by_day"}


def test_json_text_reply_is_read_as_a_call():
    calls = _calls_from_text('```json\n{"name": "torque_stats", "arguments": {"head_id": "H05"}}\n```')
    assert calls == [("torque_stats", {"head_id": "H05"})]
    assert _calls_from_text("I think head 5 is fine.") == []


def test_invalid_proposal_falls_back_to_the_rules_and_says_so(ollama):
    ollama({"tool_calls": [{"function": {"name": "no_such_tool", "arguments": {}}}]})
    planner = OllamaPlanner(CFG)
    plan = planner.plan("show me the failure rate for each head", CONTEXT)
    assert plan.calls and plan.calls[0][0] == "success_rate_per_head"
    assert planner.last_error and "qwen2.5:7b gave no valid analysis" in planner.last_error


def test_without_fallback_an_invalid_proposal_is_refused(ollama):
    ollama({"content": "no idea"})
    cfg = {"agent": {"llm": dict(CFG["agent"]["llm"], fallback_to_rules=False)}}
    plan = OllamaPlanner(cfg).plan("show me the failure rate for each head", CONTEXT)
    assert plan.ambiguous and not plan.calls
