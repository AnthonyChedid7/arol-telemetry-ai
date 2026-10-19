# Hosting the website with daily CSV uploads

The website runs on **Streamlit Community Cloud**. The Anthropic key, the
storage key and an upload password live in the app's **Secrets**, never in the
repository. Visitors need no API key. Whoever knows the upload password can add
a day of AROL telemetry from the sidebar, and it is kept in a private
**Supabase Storage** bucket, because Streamlit's own disk is wiped whenever the
app restarts or sleeps.

| Service | Role | Cost |
|---|---|---|
| Streamlit Community Cloud | runs the website | free |
| Supabase (Free plan) | keeps uploaded telemetry: 1 GB, about 2.6 MB per day, so roughly 380 days | free |
| Anthropic API | Claude understands the question and words the answer | paid per question, from the key owner's credits (two requests per question) |

**What is sent to Anthropic** with the Claude option: the question, the data's
machine and time range, and, to word the answer, the computed results (counts
and rates per head, day or interval). Raw telemetry rows are never sent. Every
number in Claude's answer is checked against the computed results; if one does
not match, the built-in answer is shown instead. Set
`agent.claude.answer_from_results: false` in `config.yaml` to keep the results
on the server (Claude then only plans; the built-in answer is shown).

Free-plan limits to know: Supabase pauses a project after 1 week without
activity (press **Restore** in the Supabase dashboard). Streamlit puts an app
to sleep after 12 hours without visitors; the next visitor wakes it in about a
minute, and the uploaded days are reloaded from Supabase.

**Data:** uploaded AROL telemetry is stored by Supabase and processed on
Streamlit's servers. Confirm that AROL allows this before uploading real data.

## 1. Supabase: storage for the uploads

1. Create a free account at supabase.com and a **New project** (any name and
   region; keep the database password somewhere safe, the app does not use it).
2. **Storage** → **New bucket**, name `telemetry`. Leave **Public bucket off**.
3. **Project Settings** → **API Keys**: copy a **secret key** (`sb_secret_...`).
   The legacy `service_role` key also works. This key has full access to the
   project: put it only in the Streamlit Secrets (and, for the check below, in
   your local `.env`), never in a file in Git.
4. Copy the **Project URL** (`https://<project>.supabase.co`, under
   Project Settings).

Optional check from your computer, before deploying. Put the values in
`Project-experiment/.env`:

```
SUPABASE_URL=https://<project>.supabase.co
SUPABASE_KEY=sb_secret_...
AROL_STORAGE_BUCKET=telemetry
```

then, from `Project-experiment`:

```
.venv\Scripts\python -m scripts.check_storage
```

It should print `OK: write and read work.` and `No telemetry uploaded yet.`

## 2. Streamlit Community Cloud: the website

1. Sign in at share.streamlit.io with GitHub and click **Create app**.
2. Repository `EddyKhoury/arol-telemetry-ai`, branch `pro-ui`, main file
   `app/streamlit_app.py`.
3. **Advanced settings**: Python **3.14** (3.12 also works). In **Secrets**
   paste, with your own values:

   ```toml
   ANTHROPIC_API_KEY = "sk-ant-..."
   SUPABASE_URL = "https://<project>.supabase.co"
   SUPABASE_KEY = "sb_secret_..."
   AROL_STORAGE_BUCKET = "telemetry"
   AROL_UPLOAD_PASSWORD = "a long password only the uploaders know"
   ```

4. **Deploy**. The first start installs packages and takes a few minutes.
5. **Settings → Sharing**: choose **Only specific people can view this app**
   and invite the team. The repository is public, so the app starts public, and
   every question costs the key owner's credits.
6. In console.anthropic.com, set a monthly **spend limit**.

Secrets can be changed later in the app's **Settings → Secrets**; the app
restarts and keeps the uploaded data.

## 3. Choosing the data and adding days

The sidebar's **Data to analyse** offers:

- **AROL telemetry (shared, stored)**: every day added to Supabase, for everyone
  using the site. Selected by default once a day is stored.
- **My own CSV files (this session only)**: choose up to 7 daily CSVs of one
  machine and click **Analyse these files**. They are converted for your
  browser session only, never added to the shared data, and deleted when the
  session ends (or with **Remove my files**). No password is needed. Use this to
  study a file without changing what others see.
- **Demo data (synthetic)**: the made-up day with planted problems.

The example questions follow the chosen data's dates.

### Adding a day to the shared data

1. Choose **AROL telemetry (shared, stored)** and open **Add a day to the
   shared data**.
2. Enter the upload password and choose the day's CSV, keeping AROL's export
   name: `telemetry_<machine>_<YYYY-MM-DD>.csv` (up to 200 MB).
3. Click **Add to the shared data**. A full day took about 10 seconds on a
   laptop; allow up to a minute on the free host. The sidebar then shows the
   new file count and time range, and every question uses all stored days.

Days added another way (another visitor, or `pool_store.add_csv` from a
computer with the keys in `.env`) appear by themselves within a minute; no
reboot is needed. **Check for new days** shows them at once.

The upload is refused, with nothing changed, when the file:

- is already uploaded (same content or same name);
- starts before the last uploaded day ends: **add days in date order**;
- belongs to a different machine than the uploaded data;
- is not valid AROL telemetry (missing columns, bad counters, unordered timestamps).

Upload attempts and their errors are written to the app's logs
(**Manage app** on the site), lines starting `[stored upload]` or `[session upload]`.

## Testing uploads locally

Without Supabase, a plain folder stands in for the bucket. In `.env`:

```
AROL_STORE_DIR=data/upload_store
AROL_UPLOAD_PASSWORD=<any test password>
```

then start the website as usual (`streamlit run app/streamlit_app.py`). The
uploaded pool is mirrored to `data/stored_pool/`; delete that folder to see a
fresh host reload everything from the store. Both folders are ignored by Git.
Remove `AROL_STORE_DIR` (and leave `SUPABASE_*` unset) to go back to the local
private pool.
