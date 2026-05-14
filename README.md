# ZiroWork Brain Agent

**Video Intelligence Processor** — converts Instagram links into structured markdown knowledge files saved to Google Drive for Obsidian sync.

**Pipeline:** Instagram Link → Audio Extract (yt-dlp) → Transcribe (Whisper) → Process (Claude) → Markdown → Google Drive → Cleanup

---

## Stack

| Layer | Tech |
|---|---|
| Frontend | React 19 + TypeScript + Tailwind 4 |
| Backend | Python 3.11 + FastAPI + Uvicorn |
| Audio Extract | yt-dlp |
| Transcription | OpenAI Whisper API (`whisper-1`) |
| AI Processing | Anthropic Claude (`claude-opus-4-20250514`) |
| Storage | Google Drive API v3 (service account) |
| Design | ZiroWork design system (Bebas Neue + DM Sans, lime/black) |

---

## Project Structure

```
zirowork-brain-agent/
├── backend/
│   ├── main.py          ← FastAPI app, full 6-step pipeline
│   └── env-example.txt  ← Environment variable reference
├── client/
│   ├── src/
│   │   ├── pages/Home.tsx     ← Main UI
│   │   ├── index.css          ← ZiroWork design system
│   │   └── App.tsx
│   └── index.html
└── README.md
```

---

## Setup

### 1. Backend

```bash
cd backend

# Install dependencies (already done if using this repo)
pip install fastapi uvicorn yt-dlp openai anthropic google-api-python-client google-auth python-dotenv

# Create .env from example
cp env-example.txt .env
# Fill in your API keys (see Environment Variables below)

# Start backend
python main.py
# → Running at http://localhost:8000
```

### 2. Frontend

```bash
# From project root
pnpm install
pnpm dev
# → Running at http://localhost:3000
```

The frontend auto-connects to `http://localhost:8000`. Override with `VITE_BACKEND_URL` env var.

---

## Environment Variables

Create `backend/.env` with these values:

```bash
# OpenAI (Whisper)
OPENAI_API_KEY=sk-...

# Anthropic (Claude)
ANTHROPIC_API_KEY=sk-ant-...

# Google Drive — folder ID from URL: drive.google.com/drive/folders/<ID>
GOOGLE_DRIVE_FOLDER_ID=your_folder_id

# Service account JSON (single line, or use a file path)
GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON={"type":"service_account","project_id":"..."}

# Optional
APPROVED_CREATORS=Andrew Huberman,Simon Willison,Andrej Karpathy
CONTENT_CATEGORIES=Agent Design,LLM Optimization,Product Strategy,AI Safety & Ethics,Technical Architecture,Business & Growth
LOG_LEVEL=info
TEMP_DIR=/tmp
BACKEND_PORT=8000
```

---

## Google Drive Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a project → Enable **Google Drive API**
3. Create a **Service Account** → Download JSON key
4. Share your target Drive folder with the service account email (Editor access)
5. Copy the folder ID from the URL and set `GOOGLE_DRIVE_FOLDER_ID`
6. Paste the entire service account JSON (minified) into `GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON`

---

## API Reference

### `GET /api/health`
```json
{ "status": "ok", "service": "ZiroWork Brain Agent" }
```

### `GET /api/config`
```json
{
  "approved_creators": ["Andrew Huberman", ...],
  "content_categories": ["Agent Design", ...]
}
```

### `POST /api/process-video`

**Request:**
```json
{
  "instagram_link": "https://www.instagram.com/reel/...",
  "creator": "Andrew Huberman",
  "category": "Agent Design"
}
```

**Success Response:**
```json
{
  "status": "success",
  "filename": "2025-05-14-andrew-huberman.md",
  "drive_url": "https://drive.google.com/file/d/.../view",
  "preview": "# Title\n\n**Core Insight:**...",
  "message": "Saved to ZiroWork-Brain/Raw Videos/"
}
```

**Error Response:**
```json
{
  "status": "error",
  "error": "Invalid Instagram link. Check URL and try again.",
  "code": "INVALID_LINK"
}
```

**Error Codes:**

| Code | Meaning |
|---|---|
| `INVALID_LINK` | yt-dlp can't extract — bad URL |
| `INVALID_CREATOR` | Creator not in approved list |
| `INVALID_CATEGORY` | Category not valid |
| `MISSING_CONFIG` | API key not set in .env |
| `TRANSCRIPTION_FAILED` | Whisper API error |
| `PROCESSING_FAILED` | Claude API error (raw transcript saved) |
| `DRIVE_WRITE_FAILED` | Google Drive API error |
| `TIMEOUT` | Network timeout |

---

## Output Markdown Format

```markdown
---
date: 2025-05-14
creator: Andrew Huberman
category: Agent Design
source_url: https://instagram.com/reel/...
duration: unknown
processed_by: Claude (claude-opus-4-20250514)
---

**Source:** [Andrew Huberman](link) | **Date:** 2025-05-14

# [Clear, Specific Title]

**Core Insight:** [1-2 sentence summary]

## Key Points

### 1. [Topic] [MM:SS-MM:SS]
[Explanation]
- Bullet
**Why it matters:** [Connection to AI/product building]

## Actionable Takeaways
- [ ] Action 1
- [ ] Action 2

## Related Concepts
- [[Concept 1]]
```

---

## Cost Estimate

| Service | Per Video | Per Month (50 videos) |
|---|---|---|
| OpenAI Whisper | $0.02–0.05 | $1–2.50 |
| Anthropic Claude | $0.08–0.12 | $4–6 |
| Google Drive | Free | Free |
| **Total** | **~$0.10–0.17** | **~$5–8.50** |

---

## Testing Checklist

- [ ] Test with real Instagram Reel (5–30 min)
- [ ] Verify Whisper transcription includes timestamps
- [ ] Verify Claude removes sales pitch from raw transcript
- [ ] Verify markdown saves to correct Google Drive folder
- [ ] Verify filename: `YYYY-MM-DD-creator-name.md`
- [ ] Test error: invalid link (graceful error, no crash)
- [ ] Test error: network timeout (retry, then fail cleanly)
- [ ] Verify temp audio files deleted after processing
- [ ] Performance: link input → Drive save < 5 min for 30-min video

---

## Adding Creators / Categories

Edit `backend/.env`:

```bash
APPROVED_CREATORS=Andrew Huberman,Simon Willison,Andrej Karpathy,Lex Fridman
CONTENT_CATEGORIES=Agent Design,LLM Optimization,Product Strategy,New Category
```

Restart backend. Frontend picks up changes automatically via `/api/config`.

---

## Post-MVP Roadmap

- Creator approval workflow (add/remove via UI)
- Agent interface to query ZiroWork-Brain folder
- Weekly digest feature
- Search + filtering
- Auto-categorization (Claude assigns categories)

---

*ZiroWork Brain Agent — Built for Zach Adkins / ZiroWork Intelligence System*
