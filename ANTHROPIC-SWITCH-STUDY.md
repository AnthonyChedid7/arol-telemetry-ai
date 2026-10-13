# Study: switching the agent from Ollama to the Anthropic API

Branch: `claude-planner` (cloned from `EddyKhoury/arol-telemetry-ai`, commit `93b4a6d`).

> **Status: Option B implemented.** New planner in `src/agent/claude_planner.py`. Results:
> 16/16 everyday-language questions planned correctly versus 2/16 for the rules
> (`benchmarks/integration/claude_paraphrases_*.json`). Full test suite green.
> See the README section "Claude planner (everyday wording)". The text below is
> the original plan.

---

## 1. How the agent understands a question today

There are three planners, chosen with `agent.planner` (default `rules`):

| Planner | Used by | How it understands the question |
|---|---|---|
| `rules` | CLI default, all verify/evaluate scripts | Regular expressions (`torque_routing.py`, `kpi_routing.py`, `idle_routing.py`, ...) |
| `diagnostic` | **The website** (`scripts/web_demo.py` hard-codes it) | Same regex rules, plus a head-health phrase matcher |
| `llm` | Only with `--planner llm` | Ollama (`qwen2.5-coder:14b` in `config.yaml`) — see below |

### Why it only understands the "exact vocab"

The vocabulary problem is **not caused by the Ollama model**. It comes from the regex
grammar, and the current LLM planner is locked behind that same grammar:

`LLMPlanner.plan()` in `src/agent/planner.py` does this:

1. Parses the question with the **regex** parser `parse_torque_request()` first.
2. If the regex does not understand it → rejects **without ever asking the model**
   (`"This LLM checkpoint supports the five verified torque analyses only."`).
3. If the regex understands it → asks the model, then **throws the model's answer away
   unless it is identical to what the regex already produced** (`calls != expected.calls`).

So the model can never understand anything the regex doesn't. It is used only as a double
check, and only for 5 of the 12 tools (`VERIFIED_TOOLS`). That's why simply pointing it at
Claude instead of Ollama would change nothing for you.

The website doesn't use the LLM at all. It runs the `diagnostic` planner, which is regex only.

### What the code already has in our favour

- **Tool schemas are already in Anthropic format.** `registry.get_tool_specs()` returns
  `{name, description, input_schema}`, which is exactly what the Anthropic API takes. The
  `_to_ollama_tools()` function exists only to convert them to Ollama's format.
- **12 registered tools**: `torque_stats`, `torque_distribution`, `torque_trend`,
  `detect_torque_anomalies`, `head_correlation`, `success_rate`, `success_rate_per_head`,
  `rank_heads_by_success`, `compare_head_success`, `kpi_over_time`, `observed_throughput`,
  `machine_idle`.
- **Safety nets that stay as they are**: `registry.call_tool()` rejects unknown parameters,
  wrong types and missing required arguments. The orchestrator caps the number of tool calls
  (`max_steps`). Tools compute every number, never the model.

---

## 2. Options

### Option A — swap the transport only (not recommended)
Replace the Ollama HTTP call in `LLMPlanner._chat()` with an Anthropic call. About 30 lines.
**This does not fix your problem**, because the regex gate stays in front.

### Option B — Claude understands the question, tools compute the answers (recommended)
A new `claude` planner where Claude reads the free-form question and chooses **which of the
12 tools to call and with which arguments**. The regex gate is removed from this path.
The existing validation and tool execution stay exactly as they are.

- "how's the tightening force on spindle 5 been this morning?" → `torque_stats(head_id="H05", start=..., end=...)`.
- If something essential is missing (for example no time window for a tool that requires
  one), Claude asks a clarification question instead of guessing.
- The report shows an **"Interpreted as"** line, for example `torque_stats · head H05 · machine X · 2026-02-01 00:00 → 12:00`,
  so you can check at a glance that Claude understood you. This replaces the old
  "must equal the regex" check as the trust mechanism.
- If there's no API key or no internet, it falls back to the rules planner and says so in
  the trace. This is the same fallback behaviour the code already has for Ollama.
- Only the **question text plus the tool descriptions** go to Anthropic. No telemetry data is sent.

### Option C — full agent: Claude also reads the results and writes the answer
Like B, but the tool results go back to Claude, which writes the explanation. The answers
read more naturally, but:
- Aggregated telemetry results get sent to Anthropic (see the data question in section 5).
- It goes against the project's stated principle ("the model never produces findings";
  see the docstrings and `docs/METHODS-AND-LIMITS.md`). This is something you'd need to
  defend at the presentation.

Suggestion: do **B** now. C can be added later as an optional, clearly labelled
"Claude summary" section on top of the tool-computed report.

---

## 3. What changes under Option B (file by file)

| File | Change |
|---|---|
| `requirements.txt` | Add `anthropic` (pinned version). |
| `config.yaml` | Add `agent.claude: {model: claude-opus-5-5, effort: low, timeout_seconds: 60, fallback_to_rules: true}`. The API key is **not** stored here. It comes from the `ANTHROPIC_API_KEY` environment variable. |
| `src/agent/claude_planner.py` *(new)* | `ClaudePlanner`: builds the system prompt (domain glossary: head 5 = `H05`, "successful closures" = status 0, end time exclusive, machine IDs case-sensitive, configured thresholds are off-limits, ask instead of guessing), sends all 12 tool specs from the registry, reads the `tool_use` blocks from the response, validates them, and returns a `Plan`. No tool call → clarification. Network or auth error → rules fallback (recorded in `last_error`, which the orchestrator already labels as `claude->rules (fallback)`). |
| `src/agent/llm_validation.py` | Generalise `strict_calls` so it validates proposals for **all** registered tools (not only the 5 torque ones), and add type rules for `bucket` and list-valued `head_id`. It keeps the "never repair or drop arguments" rule. The Ollama path keeps its current behaviour. |
| `src/agent/planner.py` | `get_planner()` accepts `"claude"`. The `rules` / `diagnostic` / `llm` (Ollama) planners are **left untouched**, so Eddy's verified paths and all existing tests keep working, and you can compare both planners side by side. |
| Context passed to Claude | Selected machine and the pool's time range (from the manifest). This way "this morning", "the first hour" or "on the 1st" resolve to real timestamps instead of today's real-world date. |
| `src/agent/report.py` | Add the "Interpreted as" line when the plan came from Claude. |
| `scripts/demo_agent.py` | `--planner` gets the `claude` choice. |
| `scripts/web_demo.py` | Planner selectable (sidebar toggle or `AROL_PLANNER` env var). The default stays `diagnostic`, so the demo works without a key. When the Claude planner proposes the head-check pair (`compare_head_success` + `detect_torque_anomalies`), reuse the existing diagnostic explanation. |
| `tests/test_claude_planner.py` *(new)* | Offline tests using a fake Anthropic client: tool_use → plan, bad arguments rejected, no tool → clarification, missing key / network error → labelled fallback, model prose never becomes a finding. The existing test suite must still pass unchanged. |
| `README.MD` | Short "Using the Claude planner" section: how to set the API key, cost, fallback. |

### API details to get right (current Anthropic API, verified against the SDK docs)
- Model `claude-opus-5-5` (current default Opus; $4 / $20 per million input/output tokens).
- `tool_choice` must be `auto`. Forcing a tool (`any` / `tool`) returns HTTP 400 on this model.
  The system prompt tells Claude to call tools or ask for clarification.
- Thinking can't be turned off on Opus 5.5. Effort controls cost and speed (`low` is enough
  for picking tools; I'll measure and tune).
- Check `stop_reason` (`tool_use`, `end_turn`, `refusal`, `max_tokens`) before reading the
  result. I'll also enable the server-side refusal fallback (`fallbacks: "default"`) so a
  false-positive safety refusal is retried automatically.
- Tool arguments are read as parsed JSON (`block.input`), never by string matching.

---

## 4. Cost estimate

One question = one API call with about 4,000 input tokens (system prompt + 12 tool schemas)
and a few hundred output tokens.
- Claude Opus 5.5: roughly **$0.02–0.04 per question**. A full demo session of 50 questions costs about $1–2.
- Prompt caching on the system prompt + tools can reduce repeated input cost. I'll turn it on.
- A cheaper model (Sonnet 5.5 or Haiku 5.5) is a one-line config change if you want it. That's your call.

---

## 5. What I need from you

1. **Confirm Option B** (or tell me if you want A or C).
2. **An Anthropic API key.** Create one at console.anthropic.com (it needs credits/billing),
   then set it on your machine yourself:
   ```powershell
   setx ANTHROPIC_API_KEY "sk-ant-..."
   ```
   Don't paste the key into chat or commit it. I'll make sure it's never written to the repo.
   (Alternatively `ant auth login` if you use the Anthropic CLI.)
3. **Data question for the team / professor.** Only the question text leaves the machine
   under Option B, but questions contain machine IDs and head numbers. If the AROL contract
   (`contract.pdf` in your old Project folder) restricts sending anything to external
   services, check that first. It matters much more for Option C.

---

## 6. How I'll verify it works

1. Full offline test suite (`pytest`) still green, plus the new Claude-planner tests.
2. **Live paraphrase check, run only with your OK because it costs money (under $0.50):**
   about 15 questions phrased in everyday words that the regex planner currently rejects
   (for example "how's spindle 5 doing torque-wise on Feb 1st?", "which head had the worst
   success rate this morning on M1?"). For each one I record the tool + arguments Claude chose,
   the rules planner's result, and whether the scope is correct. Results go into a JSON file
   under `benchmarks/integration/`, like the project's existing evidence files.
3. Run the website with the Claude planner, ask a few questions, and confirm the
   "Interpreted as" line and the report look right.

Estimated work: about half a day of changes plus testing. Everything stays on the
`experiment` branch and nothing is pushed until you say so.
