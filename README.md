# Job Agent

A local job-alert system that monitors **LinkedIn** and company **career sites** (Greenhouse, Lever, Ashby, SmartRecruiters) for new openings at companies you care about. Jobs are stored in SQLite, shown in a React dashboard, and emailed to you when new listings appear.

---

## Table of contents

- [Features](#features)
- [Prerequisites](#prerequisites)
- [Clone and install](#clone-and-install)
- [Configure](#configure)
- [Run the application](#run-the-application)
- [First-time usage](#first-time-usage)
- [Dashboard (UI)](#dashboard-ui)
- [CLI mode](#cli-mode)
- [Configuration reference](#configuration-reference)
- [Email setup](#email-setup)
- [Optional: AI scoring](#optional-ai-scoring)
- [API reference](#api-reference)
- [Project structure](#project-structure)
- [Troubleshooting](#troubleshooting)

---

## Features

- Fetches jobs from **career-site APIs** and **LinkedIn** guest search
- **React dashboard** with card and table views, search, filters, and sort
- **Email alerts** when genuinely new jobs are found (Gmail, Resend, SMTP, or Maileroo)
- **Background polling** — the API server fetches on a schedule while the UI stays open
- **New-job celebration** — confetti animation when new listings appear (great for a desk display)
- **Experience filtering** — filters jobs by years of experience in the title/description
- **Deduplication** — same role from LinkedIn and a career site is stored once
- **Optional AI mode** — Claude scores jobs and tailors resumes (`--with-ai`)

---

## Prerequisites

Install these before you start:

| Tool | Version | Check |
|------|---------|-------|
| **Python** | 3.11 or newer | `python3 --version` |
| **Node.js** | 18 or newer | `node --version` |
| **npm** | 9+ (bundled with Node) | `npm --version` |
| **Git** | any recent version | `git --version` |

Email alerts are optional but recommended. For AI mode you need an [Anthropic API key](https://console.anthropic.com/).

---

## Clone and install

```bash
# 1. Clone the repository
git clone https://github.com/YOUR_USERNAME/job-agent.git
cd job-agent

# 2. Create a Python virtual environment
python3 -m venv .venv
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\activate         # Windows

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Install UI dependencies
cd ui && npm install && cd ..
```

> **Windows note:** Use two terminals to run the API and UI separately (see [Run the application](#run-the-application)). The `start-ui.sh` script is macOS/Linux only.

---

## Configure

Three files hold your personal settings. None of them are committed to git — you create them locally from the examples.

```bash
cp env.template .env
cp config/companies.example.json config/companies.json
cp resume/master_resume.example.txt resume/master_resume.txt
```

### 1. Edit `.env`

Open `.env` and set at minimum:

```env
USER_NAME=Your Name
TARGET_ROLES=Senior Software Engineer,Software Engineer
JOB_LOCATION=India
NOTIFY_EMAIL=your.email@gmail.com
```

See [Configuration reference](#configuration-reference) for every variable.

### 2. Edit `config/companies.json`

Add the companies you want to watch:

```json
{
  "target_companies": [
    "Google",
    "Microsoft",
    "Amazon",
    "Meta",
    "Apple"
  ]
}
```

You can list as many companies as you like. Career-site URLs and ATS board mappings are already curated in `jobs/career_registry.py` for common tech companies.

**Alternative:** set companies directly in `.env` instead of a JSON file:

```env
TARGET_COMPANIES=Google,Microsoft,Amazon
```

### 3. Edit `resume/master_resume.txt` (optional)

Only needed if you use `--with-ai`. Replace the placeholder with your real resume text.

### 4. Set up email (optional but recommended)

Add one of the email options to `.env` so you get alerts for new jobs. The easiest path is Gmail — see [Email setup](#email-setup).

---

## Run the application

You need **two processes**: the FastAPI backend (port 8000) and the React frontend (port 5173). The UI proxies API calls to the backend automatically.

### Option A — one command (macOS / Linux)

```bash
source .venv/bin/activate
chmod +x start-ui.sh    # only needed once
./start-ui.sh
```

### Option B — two terminals (all platforms)

**Terminal 1 — API server:**

```bash
cd job-agent
source .venv/bin/activate
python -m uvicorn api.server:app --reload --host 127.0.0.1 --port 8000
```

**Terminal 2 — React UI:**

```bash
cd job-agent/ui
npm run dev
```

### Open the dashboard

Go to **http://localhost:5173**

The API is also available directly at **http://127.0.0.1:8000** (e.g. `GET /api/jobs`).

### Production UI build (optional)

```bash
cd ui && npm run build
```

Serve the `ui/dist/` folder with any static file server. You still need the API running on port 8000 (or update the proxy in your server config).

---

## First-time usage

1. Start the API and UI (see above).
2. Open **http://localhost:5173**.
3. Click **Fetch jobs** in the top-right corner.
4. Wait for the fetch to complete (can take a few minutes depending on how many companies you watch).
5. Jobs appear in the dashboard. New ones show a **New** badge.
6. Click **Open ↗** on any job to view the listing.
7. Click **Mark all read** to clear new badges after you've reviewed them.

The server will **automatically fetch again every 5 minutes** (configurable via `CHECK_INTERVAL_MINUTES`). The UI checks for changes every 30 seconds and shows a countdown to the next fetch.

---

## Dashboard (UI)

| Feature | Description |
|---------|-------------|
| **Card / Table view** | Toggle between card grid and sortable table |
| **Search** | Filter by job title or company name |
| **Company filter** | Show jobs from one company |
| **Source filter** | Filter by LinkedIn, Greenhouse, Lever, etc. |
| **Sort** | Newest or oldest by posted date |
| **New badge** | Highlights jobs not seen before |
| **Fetch jobs** | Trigger a manual fetch (rate-limited to the poll interval) |
| **Mark all read** | Clear all "new" badges |
| **Next fetch countdown** | Live timer showing when the next automatic fetch runs |
| **New-job animation** | Confetti burst when new jobs appear |
| **Auto-refresh** | Polls for changes every 30 seconds while the tab is visible |

---

## CLI mode

You can run the fetcher without the UI — useful for servers or cron jobs.

```bash
source .venv/bin/activate

# Fetch once and exit
python main.py --once

# Fetch every N minutes (same interval as CHECK_INTERVAL_MINUTES)
python main.py

# Send a test email to verify email config
python main.py --test-email

# AI scoring mode (requires CLAUDE_API_KEY and master_resume.txt)
python main.py --once --with-ai
```

Logs are written to `logs/job_agent.log`.

---

## Configuration reference

All settings are loaded from `.env` and `config/companies.json` by `config.py`. Copy `env.template` to get started.

### Profile

| Variable | Default | Description |
|----------|---------|-------------|
| `USER_NAME` | *(empty)* | Your name (shown in CLI and API meta) |
| `TARGET_ROLES` | `Software Engineer,...` | Comma-separated job titles to match |
| `JOB_LOCATION` | `India` | Location passed to LinkedIn and career-site filters |
| `FILTER_BY_ROLE` | `true` | When `true`, only jobs matching `TARGET_ROLES` are kept |
| `MIN_MATCH_SCORE` | `65` | Minimum AI score to act on a job (`--with-ai` only) |
| `JOB_LOOKBACK` | `r604800` | LinkedIn time filter (604800 s = last 7 days) |
| `COMPANIES_CONFIG_PATH` | `config/companies.json` | Path to your company shortlist JSON |
| `TARGET_COMPANIES` | *(none)* | Alternative: comma-separated companies in `.env` |
| `RESUME_PATH` | `resume/master_resume.txt` | Path to master resume for AI mode |

### Experience filter

| Variable | Default | Description |
|----------|---------|-------------|
| `EXPERIENCE_YEARS` | `4` | Your years of experience |
| `EXPERIENCE_MIN` | `2` | Accept jobs requiring at least this many years |
| `EXPERIENCE_MAX` | `6` | Reject jobs requiring more than this many years |
| `FILTER_BY_EXPERIENCE` | `true` | Enable/disable experience filtering |

### Fetch settings

| Variable | Default | Description |
|----------|---------|-------------|
| `CHECK_INTERVAL_MINUTES` | `5` | How often the background poller fetches new jobs |
| `FETCH_CONCURRENCY` | `8` | Parallel threads for company fetches |

### API keys

| Variable | Description |
|----------|-------------|
| `CLAUDE_API_KEY` | Anthropic API key for `--with-ai` mode |

---

## Email setup

Set `NOTIFY_EMAIL` to the address that should receive alerts, then configure **one** transport below.

### Option 1 — Gmail (easiest)

1. Enable 2FA on your Google account.
2. Create an [App Password](https://myaccount.google.com/apppasswords).
3. Add to `.env`:

```env
NOTIFY_EMAIL=your.email@gmail.com
GMAIL_APP_PASSWORD=your_16_char_app_password
```

### Option 2 — Resend

```env
NOTIFY_EMAIL=your.email@gmail.com
RESEND_API_KEY=re_xxxxxxxx
RESEND_FROM=onboarding@resend.dev
```

### Option 3 — Custom SMTP

```env
NOTIFY_EMAIL=your.email@gmail.com
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=your_smtp_user
SMTP_PASSWORD=your_smtp_password
SMTP_FROM=alerts@example.com
SMTP_FROM_NAME=Job Agent
SMTP_USE_TLS=true
```

### Option 4 — Maileroo API

```env
NOTIFY_EMAIL=your.email@gmail.com
MAILEROO_API_KEY=your_sending_key
```

### Test your email config

```bash
python main.py --test-email
```

Or via the API:

```bash
curl -X POST http://127.0.0.1:8000/api/email/test
```

---

## Optional: AI scoring

With a Claude API key and a filled-in `resume/master_resume.txt`:

```bash
# Add to .env
CLAUDE_API_KEY=sk-ant-...

# Run with AI scoring
python main.py --once --with-ai
```

AI mode scores each new job against your resume (0–100) and can generate tailored resumes for strong matches. This is CLI-only today; the dashboard shows all fetched jobs regardless of AI score.

---

## API reference

Base URL: `http://127.0.0.1:8000`

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/jobs` | All jobs + metadata. Query params: `company`, `search`, `sort` (`newest`/`oldest`), `source` |
| `GET` | `/api/jobs/status` | Lightweight status: `updated_at`, `new_count`, `total` |
| `GET` | `/api/companies` | Distinct company names in the database |
| `GET` | `/api/meta` | Full metadata: profile, fetch status, email config, poll interval |
| `POST` | `/api/fetch` | Start a background fetch |
| `POST` | `/api/fetch/sync` | Fetch jobs now and wait for result |
| `POST` | `/api/jobs/mark-read` | Mark all jobs as read (clear "new" badges) |
| `POST` | `/api/email/test` | Send a test email |

### Example

```bash
# Get all jobs
curl http://127.0.0.1:8000/api/jobs

# Fetch jobs now
curl -X POST http://127.0.0.1:8000/api/fetch/sync

# Check if anything changed (used by the UI for smart polling)
curl http://127.0.0.1:8000/api/jobs/status
```

---

## Project structure

```
job-agent/
├── api/
│   └── server.py              # FastAPI backend + background poller
├── ai/
│   ├── job_scorer.py          # Claude job scoring (--with-ai)
│   └── resume_builder.py      # Tailored resume generation
├── config/
│   ├── companies.example.json # Example company list (committed)
│   └── companies.json         # Your company list (gitignored — create locally)
├── jobs/
│   ├── job_fetcher.py         # LinkedIn + orchestration
│   ├── career_fetcher.py      # Career-site fetching
│   ├── career_registry.py     # ATS URLs and board mappings
│   ├── job_store.py           # SQLite read/write
│   ├── database.py            # Schema and migrations
│   └── ats/                   # Greenhouse, Lever, Ashby, SmartRecruiters
├── notifications/
│   ├── email_notifier.py      # SMTP email sender
│   ├── resend_api.py          # Resend transport
│   └── maileroo_api.py        # Maileroo transport
├── resume/
│   ├── master_resume.example.txt  # Resume template (committed)
│   └── master_resume.txt          # Your resume (gitignored — create locally)
├── ui/                        # React dashboard (Vite)
├── data/                      # SQLite database (gitignored, auto-created)
├── logs/                      # Application logs (gitignored)
├── config.py                  # Loads .env + companies.json
├── env.template               # Environment variable template (committed)
├── main.py                    # CLI entry point
├── start-ui.sh                # Start API + UI together (macOS/Linux)
└── requirements.txt           # Python dependencies
```

### Job sources and deduplication

Each fetch pulls from:

1. **Career-site APIs** — Greenhouse, Lever, Ashby, SmartRecruiters (parallel, with ATS auto-discovery)
2. **LinkedIn** — guest job search API

Jobs are deduplicated by:

- **`job_id`** — unique per source (e.g. `li_123456`, `gh_postman_456`)
- **`dedup_hash`** — cross-source dedup from company + title + location

---

## Troubleshooting

### "No jobs yet" after fetching

- Check that `config/companies.json` has companies listed.
- Verify `JOB_LOCATION` matches where you want to work.
- Try setting `FILTER_BY_ROLE=false` temporarily to see all jobs at each company.
- Check API logs in the terminal running uvicorn.

### Fetch button says "runs every 5 minutes"

Manual fetches are rate-limited to the same interval as background polling (`CHECK_INTERVAL_MINUTES`). Wait for the countdown or increase the interval in `.env`.

### Email not working

- Run `python main.py --test-email` and read the error message.
- For Gmail: make sure you're using an **App Password**, not your regular password.
- The UI shows a warning banner if email is misconfigured — check `/api/meta` → `email_status`.

### UI shows "Failed to load jobs"

- Make sure the API server is running on port 8000.
- The UI dev server proxies `/api` to `http://127.0.0.1:8000` — both must be running.

### `ModuleNotFoundError` or import errors

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

### Port already in use

```bash
# Find and kill the process on port 8000 or 5173
lsof -ti:8000 | xargs kill
lsof -ti:5173 | xargs kill
```

### Slow first fetch

Fetching is network-bound. With many companies, the first cycle can take several minutes. Increase `FETCH_CONCURRENCY` in `.env` (default `8`) to speed it up.
