# Running the website on your own laptop

This guide sets up the same thing Anthony runs locally: the telemetry website at
`http://localhost:8501`, the real February data, and two ways to ask questions:

- **Claude (Anthropic API)**: everyday questions, answered in about 5 seconds.
  Claude runs on Anthropic's servers; your local website calls it with an API key.
- **Local model (Ollama, on this computer)**: an open model (Llama or Qwen) that
  runs on your own laptop, with no API key and no internet.

The setup takes about 20 minutes, plus the model download. All commands are for
Windows and are run in a terminal (PowerShell) **inside the project folder**,
unless the step says otherwise.

---

## 1. What you need first

| Item | Check | If missing |
| --- | --- | --- |
| Python 3.12 or newer | `python --version` | Install it from python.org. Tick "Add Python to PATH". |
| Git | `git --version` | Install it from git-scm.com. |
| Ollama | Open `http://localhost:11434` in a browser: it should say "Ollama is running". | Install it from ollama.com and start the Ollama app. |
| From Anthony, sent privately | The values listed in step 4 | Ask him. Never post them in a group chat or on GitHub. |

---

## 2. Get the project

Clone Anthony's fork (it has the newest version; Eddy's repository does not have
the local model option yet):

```bash
git clone https://github.com/AnthonyChedid7/arol-telemetry-ai.git
```

```bash
cd arol-telemetry-ai
```

If you already have a copy of the fork, update it instead:

```bash
git checkout main
```

```bash
git pull
```

---

## 3. Install the Python packages (once)

```bash
python -m venv .venv
```

```bash
.venv\Scripts\python -m pip install --upgrade pip
```

```bash
.venv\Scripts\python -m pip install -r requirements-dev.txt -r requirements-web.txt
```

Optional check that everything installed correctly (about 1 minute, should end
with "passed"):

```bash
.venv\Scripts\python -m pytest tests -q
```

---

## 4. Create the `.env` file (keys and the shared data)

1. In the project folder, copy `.env.example` and name the copy `.env`.
2. Open `.env` in a text editor and fill in these lines with the values Anthony
   sends you (remove the `#` in front of each line you fill):

```
ANTHROPIC_API_KEY=sk-ant-...
SUPABASE_URL=https://....supabase.co
SUPABASE_KEY=sb_secret_...
AROL_STORAGE_BUCKET=telemetry
```

- `ANTHROPIC_API_KEY` switches on Claude. Each question costs a few cents on the
  account that owns the key.
- The three `SUPABASE_*` / `AROL_STORAGE_BUCKET` lines give you the **shared real
  data** (machine `MCC777eda3db57348ef8a3113a642ae74db`, 2026-02-01 to
  2026-02-05), the same data as the hosted website.
- Do not add `AROL_UPLOAD_PASSWORD` unless you are meant to add days to the
  shared data.

`.env` is ignored by Git, so it is never uploaded. **Never** add it to a commit,
and never paste its contents anywhere public.

---

## 5. Download a local model

Pick the model by your laptop:

| Your laptop | Command | Accuracy in our tests (20 questions) |
| --- | --- | --- |
| NVIDIA graphics card with 12 GB+, or 16 GB+ of free RAM | `ollama pull qwen2.5-coder:14b` | 18 correct |
| NVIDIA card with 6-8 GB, or 8 GB+ of free RAM | `ollama pull qwen2.5:7b` | 15 correct |
| No NVIDIA card, little free memory | `ollama pull llama3.2:3b` | 14 correct |

Check what is installed:

```bash
ollama list
```

Speed: with an NVIDIA card, an answer takes a few seconds. Without one, a single
question can take 2-3 minutes. Close other programs (browsers, Teams, games)
before using the local model, so it has memory to work with.

---

## 6. Start the website

Make sure the Ollama app is running, then:

```bash
.venv\Scripts\streamlit.exe run app/streamlit_app.py
```

The website opens at **http://localhost:8501**. Leave the terminal open; closing
it stops the website. To stop it on purpose, press `Ctrl+C` in that terminal.

---

## 7. Check the sidebar

- **Data to analyse**: choose **AROL telemetry (shared, stored)**. Under it you
  should see the machine, the number of files and events, and **Dates covered
  (UTC)**.
- **Question understanding**: both options must appear:
  - **Claude (Anthropic API)**
  - **Local model (Ollama, on this computer)**, with a box to pick the model.

If something is missing:

| Problem | Cause | Fix |
| --- | --- | --- |
| No "Local model" option | Ollama is not running, or has no model | Start the Ollama app, run `ollama list`, wait 30 seconds and refresh the page. |
| A box asking for an Anthropic key, or a warning that rules answer instead of Claude | `.env` has no valid `ANTHROPIC_API_KEY` | Check the spelling in `.env` (no spaces, no quotes needed), then restart the website (`Ctrl+C`, start again). |
| No "AROL telemetry (shared, stored)" | The `SUPABASE_*` lines are missing or wrong | Check them in `.env`, restart the website. Meanwhile **Demo data (synthetic)** works without anything. |
| "The website has newer code than the running analysis server" | The code changed while the website was running | Restart the website. |

---

## 8. Rehearse the demo

Ask each question with **Claude** first, then switch to **Local model** and ask
it again. Before the professor watches, ask the local model one warm-up
question: the first question loads the model and is the slowest.

These questions were checked on the real data:

1. `Which head had the highest failure rate on each day?` (a per-day table)
2. `Show me the failure rate for each head` (a table of all 36 heads)
3. `Rank the 5 heads with the lowest success rate`
4. `Plot the failure rate of each head for each day` (a chart)
5. `Which head performs worst?`
6. `Is there any problem with head 4 from 2026-02-02T00:00:00 until 2026-02-02T12:00:00?`
7. `Is torque drifting for head 12 from 2026-02-02T00:00:00 until 2026-02-02T12:00:00?`
8. `Show the torque distribution of head 5 from 2026-02-02T00:00:00 until 2026-02-02T12:00:00`
9. `Was the machine idle from 2026-02-02T00:00:00 until 2026-02-02T12:00:00?`

Avoid `What was the success rate on each day?`: it covers more than 1 million
events and the page asks you to narrow the time window.

After an answer, switch on **Show details for further investigation** to show
the full report, the trace and the downloads.

How to read the result:

- **Interpreted as** shows which analysis ran and on which window. Check it,
  especially with the local model.
- **Analysis route** shows who planned the question: `claude`, `local model ...
  (Ollama)`, or `... ->rules (fallback)` when the built-in rules stepped in.

### Tips for the local model

- Name the head: `head 5` or `H05`.
- For torque, drift and anomaly questions, give a time window:
  `from 2026-02-02T00:00:00 until 2026-02-02T12:00:00`.
- Known weak spots: "No Load rate of each head" and "how many caps did head 10
  close" are sometimes sent to the wrong analysis. Claude handles both.

---

## 9. What to say about the two options

"All numbers are computed by our own validated analysis tools; the language
model only interprets the question. Claude runs through Anthropic's API. The
local option runs open models (Llama/Qwen) through Ollama on hardware we
control, so no question or data leaves the computer; in a factory this would be
a server on the plant network."
