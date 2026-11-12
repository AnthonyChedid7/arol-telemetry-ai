"""A local model (Ollama) that plans any question, the way the Claude planner does.

It gets Claude's instructions and every registered tool, so it can route the
same questions: rates, per-day results, rankings, comparisons, torque, idle,
throughput, head checks. Every proposal goes through the same validation as
Claude's (tool names, argument types, combination, pool scope). When a local
model's proposal does not validate, or it gives none, the deterministic rules
are tried on the same question, and the route is labelled as that fallback, so
an answer is never presented as the model's when it was not.

Small local models do not word answers: the built-in sentence, computed from
the results, is used. Nothing leaves the computer that runs Ollama.
"""
from __future__ import annotations

import json
import re
from types import SimpleNamespace

from ..common import registry
from .claude_planner import SYSTEM_PROMPT, ClaudePlanner, _context_text
from .planner import USER_AGENT

LOCAL_NOTE = (
    "\n\nYou run as a local model. Answer only by calling one or more of the tools; "
    "do not write prose. Use the exact argument names and types from each tool's schema. "
    "Times are ISO timestamps such as 2026-02-02T00:00:00. Write heads as H01..H36.")


def _ollama_tools(specs):
    return [{"type": "function",
             "function": {"name": s["name"], "description": s.get("description", ""),
                          "parameters": s.get("input_schema") or {"type": "object", "properties": {}}}}
            for s in specs]


def _calls_from_text(text):
    """Tool calls some models write as JSON text instead of native calls."""
    body = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    try:
        data = json.loads(body)
    except ValueError:
        return []
    items = data if isinstance(data, list) else [data]
    calls = []
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            args = item.get("arguments", item.get("parameters", {}))
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    continue
            if isinstance(args, dict):
                calls.append((item["name"], args))
    return calls


class OllamaPlanner(ClaudePlanner):
    """Claude's planning prompt and validation, served by a local Ollama model."""

    name = "ollama"

    def __init__(self, cfg, client=None):
        super().__init__(cfg, client=client)
        llm = ((cfg.get("agent") or {}).get("llm") or {})
        self.host = (llm.get("host") or "http://localhost:11434").rstrip("/")
        self.model = llm.get("model", "llama3.2:3b")
        self.temperature = float(llm.get("temperature", 0.0))
        self.seed = llm.get("seed", 42)
        self.timeout = float(llm.get("timeout_seconds", 180))
        self.num_ctx = int(llm.get("num_ctx", 8192))  # prompt + 13 tools are about 5k tokens
        self.fallback = bool(llm.get("fallback_to_rules", True))
        self.answer_from_results = False
        self.fallback_reason: str | None = None

    def _call(self, question, context, tools):
        import urllib.request
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT + LOCAL_NOTE},
                {"role": "user", "content": f"<context>\n{_context_text(context)}\n</context>\n\n"
                                            f"<question>\n{question}\n</question>"},
            ],
            "tools": _ollama_tools(tools),
            "stream": False,
            "options": {"temperature": self.temperature, "seed": self.seed, "num_ctx": self.num_ctx},
        }
        request = urllib.request.Request(
            f"{self.host}/api/chat", data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            reply = json.loads(response.read().decode("utf-8"))
        message = reply.get("message") or {}
        calls = []
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            calls.append((fn.get("name", ""), args if isinstance(args, dict) else {}))
        if not calls:
            calls = _calls_from_text(message.get("content"))
        blocks = [SimpleNamespace(type="tool_use", name=n, input=a) for n, a in calls]
        if not blocks and message.get("content"):
            blocks = [SimpleNamespace(type="text", text=str(message["content"])[:500])]
        usage = SimpleNamespace(input_tokens=reply.get("prompt_eval_count"),
                                output_tokens=reply.get("eval_count"))
        return SimpleNamespace(content=blocks, stop_reason="end_turn", model=self.model, usage=usage)

    def plan(self, query, context):
        self.fallback_reason = None
        try:
            plan = super().plan(query, context)
        except Exception as exc:  # fallback disabled and the server is unreachable
            plan = self._reject(f"the local model could not be reached ({type(exc).__name__}: {exc})")
            self.last_error = f"{type(exc).__name__}: {exc}"
        if plan.calls or not self.fallback:
            return plan
        # The model gave no valid analysis: try the deterministic rules on the same words.
        from .head_diagnostic import HeadDiagnosticPlanner
        rules = HeadDiagnosticPlanner().plan(query, context)
        if not rules.calls:
            return plan
        self.fallback_reason = self.validation_error or "no tool proposed"
        # last_error makes the orchestrator label the route "ollama->rules (fallback)".
        self.last_error = f"{self.model} gave no valid analysis ({self.fallback_reason})"
        rules.rationale = f"{self.last_error}; deterministic rules: {rules.rationale}"
        return rules

    def _reject(self, reason, proposed=""):
        plan = super()._reject(reason, proposed)
        plan.clarification = plan.clarification.replace("Claude proposed", f"{self.model} proposed")
        plan.rationale = plan.rationale.replace("Claude proposal", f"{self.model} proposal")
        return plan

    def explain(self, query, results, report):
        return None

    def compose_answer(self, query, plan, results, meta, draft):
        return None


def available_models(host):
    """Installed model names at host, or [] when Ollama does not answer."""
    import urllib.request
    try:
        request = urllib.request.Request(f"{host.rstrip('/')}/api/tags", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=3) as response:
            return [m["name"] for m in json.loads(response.read()).get("models", []) if m.get("name")]
    except Exception:
        return []


__all__ = ["OllamaPlanner", "available_models", "registry"]
