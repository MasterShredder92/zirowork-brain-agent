# ZiroWork Brain Agent

**Video Intelligence Processor** — converts Instagram links into structured markdown knowledge files for private Drive capture or public browser delivery with Kit subscriber capture.

**Private pipeline:** Instagram link → audio extraction → transcript (Whisper) → markdown (Claude) → Zach's Google Drive → cleanup.

**Public share pipeline:** Instagram link + email → audio extraction → transcript (Whisper) → markdown (Claude) → browser-delivered markdown to submitter → optional Kit subscriber capture → hidden review copy routed to Zach's separate Google Drive folder by importance → cleanup.

---

## Architecture

Single Railway service. FastAPI serves both the API and the built React SPA from one process at one URL.

```
Railway service
├─ /                   → React SPA (Vite build, dist/public/)
├─ /assets/*           → static
├─ /api/health         → FastAPI
├─ /api/config         → FastAPI
└─ /api/process-video  → FastAPI (the pipeline)
```

| Layer        | Tech                                                |
|--------------|-----------------------------------------------------|
| Frontend     | React 19 + TypeScript + Tailwind 4 (Vite)           |
| Backend      | Python 3.11 + FastAPI + Uvicorn                     |
| Audio        | Apify Instagram actor + ffmpeg                      |
| Transcribe   | OpenAI Whisper (`whisper-1`)                        |
| Process      | Anthropic Claude (`claude-haiku-4-5-20251001`)      |
| Storage      | Google Drive API v3                                 |
| Capture      | Kit API v4 subscriber/form/tag sync                 |
| Deploy       | Railway via Nixpacks (single service)               |

---

## Repo layout

```
.
├── backend/
│   ├── main.py            ← FastAPI app + pipeline + SPA fallback
│   └── requirements.txt
├── client/
│   ├── src/pages/Home.tsx ← single-page UI
│   └── ...
├── shared/                ← types shared between client & server
├── nixpacks.toml          ← Railway build (Node + Python + ffmpeg)
├── railway.json           ← Railway runtime config + healthcheck
├── Procfile               ← fallback start command
├── package.json           ← Vite build scripts
├── vite.config.ts         ← /api dev proxy → :8000
└── .env.example           ← env var schema
```

---

## Deploy to Railway

1. Push the repo to GitHub.
2. In Railway, **New Project → Deploy from GitHub** and select the repo.
3. Add these **Variables**:
   - `OPENAI_API_KEY`
   - `ANTHROPIC_API_KEY`
   - `APIFY_API_TOKEN`
   - `GOOGLE_DRIVE_FOLDER_ID`
   - `GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON` or Google OAuth variables
   - For public share mode: `PUBLIC_REVIEW_FOLDER_ID`
   - Optional Kit capture: `KIT_API_KEY`, `KIT_FORM_ID`, `KIT_TAG_ID`, `KIT_REFERRER`
   - Optional public routing: `PUBLIC_HIGH_IMPORTANCE_FOLDER_ID`, `PUBLIC_MEDIUM_IMPORTANCE_FOLDER_ID`, `PUBLIC_LOW_IMPORTANCE_FOLDER_ID`
   - Optional tuning: `APPROVED_CREATORS`, `CONTENT_CATEGORIES`, `CLAUDE_MODEL`, `LOG_LEVEL`
4. Railway will detect `nixpacks.toml`, install Node + Python + ffmpeg, build the SPA, install Python deps into `/opt/venv`, and start `python backend/main.py`.
5. The healthcheck at `/api/health` must return 200 within 30s for the deploy to be marked healthy.

**Why this works (and the previous build didn't):**

- All deploy config is at the repo root where Railway looks. Previously `nixpacks.toml`, `Procfile`, `runtime.txt` and `railway.json` were under `backend/`, where Nixpacks ignores them.
- The root `package.json` no longer pretends to be a deployable Node app; build is just `vite build`.
- `backend/main.py` no longer crashes at import on missing API keys — it logs warnings and returns `MISSING_CONFIG` from the API instead, so the container stays up and you can see logs.
- FastAPI serves the SPA, so there's no second service, no CORS, no two URLs.

---

## Local development

Two terminals:

```bash
# 1. Backend (port 8000)
cd backend
python -m venv .venv && source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
cp ../.env.example ../.env       # then fill in real keys
python main.py
```

```bash
# 2. Frontend (port 3000, proxies /api → :8000)
pnpm install
pnpm dev
```

Open <http://localhost:3000>. The Vite dev server proxies `/api/*` to FastAPI, so it behaves the same as production.

To preview the production build locally:

```bash
pnpm build
cd backend && python main.py    # FastAPI now serves dist/public + /api on :8000
```

---

## Environment variables

See [.env.example](.env.example) for the full schema. Required core variables are `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, and `APIFY_API_TOKEN`. Private mode requires `GOOGLE_DRIVE_FOLDER_ID` plus either service-account JSON or OAuth credentials.

Public share mode requires `PUBLIC_REVIEW_FOLDER_ID`. Public users receive the markdown directly in the browser with copy/download controls. `KIT_API_KEY` is optional but recommended; when configured, public submitters are captured in Kit and can optionally be routed through `KIT_FORM_ID` and `KIT_TAG_ID`. The optional high, medium, and low review folder IDs route public copies by importance; if they are blank, every public review copy falls back to `PUBLIC_REVIEW_FOLDER_ID`. Zach's internal review metadata, scoring, and Drive routing stay hidden.

`PORT` is set automatically by Railway. `CORS_ALLOW_ORIGINS` defaults to `*` and isn't strictly needed for the single-service setup.

### Public share setup

Create this Drive structure under Zach's account, then paste each folder ID into Railway Variables:

| Folder | Railway variable | Required |
|---|---|---|
| Public Review Intake | `PUBLIC_REVIEW_FOLDER_ID` | Yes |
| High Importance | `PUBLIC_HIGH_IMPORTANCE_FOLDER_ID` | No |
| Medium Importance | `PUBLIC_MEDIUM_IMPORTANCE_FOLDER_ID` | No |
| Low Importance | `PUBLIC_LOW_IMPORTANCE_FOLDER_ID` | No |

Configure Kit if you want public submitters added to your email list. Set `KIT_API_KEY` from Kit's API key screen. Optionally set `KIT_FORM_ID` to attach the subscriber to a Kit form and `KIT_TAG_ID` to tag these leads as Instagram extractor users. Direct transactional email delivery is intentionally not used here; the output is delivered in-browser so there is no Resend dependency.

---

## API

### `GET /api/health`
```json
{
  "status": "ok",
  "service": "ZiroWork Brain Agent",
  "version": "2.0.0",
  "spa_built": true,
  "drive_configured": true,
  "openai_configured": true,
  "anthropic_configured": true
}
```

### `GET /api/config`
```json
{
  "approved_creators": ["Andrew Huberman", "..."],
  "content_categories": ["Agent Design", "..."]
}
```

### `POST /api/process-video`
**Private request:**
```json
{
  "instagram_link": "https://www.instagram.com/reel/...",
  "mode": "private"
}
```

**Public share request:**
```json
{
  "instagram_link": "https://www.instagram.com/reel/...",
  "mode": "public",
  "email": "reader@example.com",
  "name": "Reader Name"
}
```

**Private success:**
```json
{
  "status": "success",
  "mode": "private",
  "filename": "2026-05-14-claude-code-agent-patterns.md",
  "drive_url": "https://drive.google.com/file/d/.../view",
  "preview": "---\ndate: 2026-05-14\n...",
  "message": "Saved to Google Drive: 2026-05-14-claude-code-agent-patterns.md"
}
```

**Public success:**
```json
{
  "status": "success",
  "mode": "public",
  "filename": "2026-05-14-claude-code-agent-patterns.md",
  "drive_url": null,
  "email_sent": false,
  "kit_synced": true,
  "preview": "---\ndate: 2026-05-14\n...",
  "message": "Processed. Your markdown is ready below. You were also added to the ZiroWork Kit list."
}
```
**Error:**
```json
{ "status": "error", "error": "...", "code": "INVALID_LINK" }
```

| Code | Meaning |
|---|---|
| `MISSING_CONFIG` | `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` not set on server |
| `MISSING_PUBLIC_CONFIG` | Public mode is disabled or missing `PUBLIC_REVIEW_FOLDER_ID` |
| `INVALID_MODE` | Request mode is not `private` or `public` |
| `INVALID_EMAIL` | Public mode request did not include a valid email address |
| `INVALID_LINK` | Not a recognised Instagram URL |
| `EXTRACTION_FAILED` | Apify/media download/ffmpeg failed |
| `TRANSCRIPTION_FAILED` | Whisper API error or audio > 25 MB |
| `PUBLIC_REVIEW_SAVE_FAILED` | Public output was processed, but Zach's hidden review copy was not saved |

(Claude failures are non-fatal: the raw transcript is saved with a fallback header.)

---

## Output markdown format

```markdown
---
date: 2026-05-14
creator: Andrew Huberman
category: Agent Design
source_url: https://instagram.com/reel/...
processed_by: Claude (claude-haiku-4-5-20251001)
---

**Source:** [Andrew Huberman](link) | **Date:** 2026-05-14

# [Clear, Specific Title]

**Core Insight:** [1-2 sentence summary]

## Key Points
### 1. [Topic] [MM:SS-MM:SS]
[Explanation]
**Why it matters:** [Connection]

## Actionable Takeaways
- [ ] Action 1

## Related Concepts
- [[Concept 1]]
```

---

## Cost (rough)

| Service       | Per video    | Per month (50 videos) |
|---------------|--------------|------------------------|
| Whisper       | $0.02–0.05   | $1–2.50                |
| Claude        | $0.08–0.12   | $4–6                   |
| Drive         | free         | free                   |
| **Total**     | **~$0.10–0.17** | **~$5–8.50**         |

---

## Adding creators / categories

Set the env vars in Railway and redeploy (or restart the service):

```
APPROVED_CREATORS=Andrew Huberman,Simon Willison,Andrej Karpathy,Lex Fridman
CONTENT_CATEGORIES=Agent Design,LLM Optimization,Product Strategy,New Category
```

The frontend re-fetches `/api/config` on load and picks up the new values automatically.

---

*ZiroWork Brain Agent v2.1 — built for Zach Adkins / ZiroWork Intelligence System*
