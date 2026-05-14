"""
ZiroWork Brain Agent — single-service backend.

Serves the built Vite SPA from dist/public/ at /, and the pipeline API at /api/*.
Pipeline: Instagram link → yt-dlp audio → Whisper transcribe → Claude process →
markdown → Google Drive → cleanup.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    force=True,
)
log = logging.getLogger("brain-agent")

# ── Config ───────────────────────────────────────────────────────────────────
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON", "")
INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME", "")
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD", "")

APPROVED_CREATORS = [
    c.strip()
    for c in os.getenv(
        "APPROVED_CREATORS", "Andrew Huberman,Simon Willison,Andrej Karpathy"
    ).split(",")
    if c.strip()
]
CONTENT_CATEGORIES = [
    c.strip()
    for c in os.getenv(
        "CONTENT_CATEGORIES",
        "Agent Design,LLM Optimization,Product Strategy,AI Safety & Ethics,"
        "Technical Architecture,Business & Growth",
    ).split(",")
    if c.strip()
]

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-4-20250514")
WHISPER_MAX_BYTES = 25 * 1024 * 1024  # OpenAI hard limit
YTDLP_TIMEOUT_SEC = 180

# Repo-root-relative path to the built SPA. backend/main.py → ../dist/public.
SPA_DIR = (Path(__file__).resolve().parent.parent / "dist" / "public").resolve()

@asynccontextmanager
async def _lifespan(app: FastAPI):
    port = os.getenv("PORT", "8000")
    log.info(f"ZiroWork Brain Agent v2.0 starting on port {port}")
    log.info(f"  approved creators:  {len(APPROVED_CREATORS)}")
    log.info(f"  content categories: {len(CONTENT_CATEGORIES)}")
    log.info(f"  SPA dir:            {SPA_DIR}  (exists={SPA_DIR.exists()})")
    if not OPENAI_API_KEY:
        log.warning("  OPENAI_API_KEY missing — /api/process-video will fail")
    if not ANTHROPIC_API_KEY:
        log.warning("  ANTHROPIC_API_KEY missing — /api/process-video will fail")
    if not (GOOGLE_DRIVE_FOLDER_ID and GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON):
        log.warning("  Google Drive not configured — markdown will be returned but not saved")
    yield


# ── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(title="ZiroWork Brain Agent", version="2.0.0", lifespan=_lifespan)

# Same-origin in production (FastAPI serves the SPA), but allow any in dev.
_cors_origins = os.getenv("CORS_ALLOW_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins if o.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ── Models ───────────────────────────────────────────────────────────────────
class ProcessVideoRequest(BaseModel):
    instagram_link: str


class ProcessVideoResponse(BaseModel):
    status: str
    filename: Optional[str] = None
    drive_url: Optional[str] = None
    preview: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None
    code: Optional[str] = None
    creator: Optional[str] = None
    category: Optional[str] = None


class ConfigResponse(BaseModel):
    approved_creators: list[str]
    content_categories: list[str]


# ── Helpers ──────────────────────────────────────────────────────────────────
_INSTAGRAM_PATTERNS = [
    re.compile(r"^https?://(www\.)?instagram\.com/(p|reel|tv)/[\w-]+"),
    re.compile(r"^https?://(www\.)?instagram\.com/[\w.]+/(p|reel|tv)/[\w-]+"),
]


def _valid_instagram_link(link: str) -> bool:
    return any(p.match(link) for p in _INSTAGRAM_PATTERNS)


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _build_filename(creator: str, date: str) -> str:
    return f"{date}-{_slugify(creator)}.md"


def extract_creator_from_url(instagram_link: str) -> str:
    """Extract creator handle from Instagram URL. Falls back to 'Unknown Creator' if not found."""
    log.info(f"[0/6] extract creator from URL: {instagram_link}")
    try:
        from urllib.parse import urlparse
        parsed = urlparse(instagram_link)
        path_parts = parsed.path.strip("/").split("/")
        if path_parts and path_parts[0] and not path_parts[0] in ("reel", "p", "tv", "stories"):
            creator = path_parts[0].replace(".", " ").title()
            log.info(f"[0/6] extracted creator from URL: {creator}")
            return creator
    except Exception as e:
        log.warning(f"[0/6] failed to parse URL: {e}")
    log.warning("[0/6] couldn't extract creator from URL, using 'Unknown Creator'")
    return "Unknown Creator"


def extract_creator_from_metadata(instagram_link: str) -> str:
    """
    Extract creator/uploader name from Instagram video via yt-dlp metadata.
    Falls back to URL parsing if metadata extraction fails (e.g., rate-limited).
    """
    log.info(f"[0/6] extract creator metadata: {instagram_link}")
    cmd = [
        "yt-dlp",
        "--dump-json",
        "--no-playlist",
    ]
    if INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD:
        cmd.extend(["-u", INSTAGRAM_USERNAME, "-p", INSTAGRAM_PASSWORD])
    cmd.append(instagram_link)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            uploader = data.get("uploader") or data.get("channel") or data.get("artist")
            if uploader:
                log.info(f"[0/6] extracted creator from metadata: {uploader}")
                return uploader.strip()
    except Exception as e:
        log.debug(f"[0/6] metadata extraction failed ({type(e).__name__}), trying URL parsing")

    return extract_creator_from_url(instagram_link)


def auto_categorize_transcript(transcript: str) -> str:
    """Use Claude to pick a category from CONTENT_CATEGORIES based on transcript content."""
    import anthropic

    log.info(f"[3b/6] auto-categorizing transcript")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    categories_str = ", ".join(CONTENT_CATEGORIES)
    user_msg = (
        f"Given this transcript, pick ONE category from the list that best fits the content. "
        f"Categories: {categories_str}\n\n"
        f"TRANSCRIPT (first 1500 chars):\n{transcript[:1500]}\n\n"
        f"Respond with ONLY the category name, nothing else."
    )
    try:
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=100,
            messages=[{"role": "user", "content": user_msg}],
        )
        category = resp.content[0].text.strip()
        if category in CONTENT_CATEGORIES:
            log.info(f"[3b/6] auto-categorized: {category}")
            return category
        else:
            log.warning(f"[3b/6] Claude returned unknown category '{category}', defaulting to first")
            return CONTENT_CATEGORIES[0]
    except Exception as e:
        log.error(f"[3b/6] auto-categorize failed: {e}, defaulting to first category")
        return CONTENT_CATEGORIES[0]


# ── Step 1: extract audio ────────────────────────────────────────────────────
def extract_audio(instagram_link: str, work_dir: str) -> str:
    log.info(f"[1/6] extract audio: {instagram_link}")
    output_template = os.path.join(work_dir, "audio.%(ext)s")
    cmd = [
        "yt-dlp",
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "5",
        "--no-playlist",
        "--no-warnings",
        "--output", output_template,
    ]
    if INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD:
        cmd.extend(["-u", INSTAGRAM_USERNAME, "-p", INSTAGRAM_PASSWORD])
    cmd.append(instagram_link)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=YTDLP_TIMEOUT_SEC)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"audio extraction timed out after {YTDLP_TIMEOUT_SEC}s") from e
    except FileNotFoundError as e:
        raise RuntimeError("yt-dlp not installed in this environment") from e

    if result.returncode != 0:
        log.error(f"[1/6] yt-dlp stderr: {result.stderr[:500]}")
        raise RuntimeError(f"yt-dlp failed: {result.stderr.strip()[:300]}")

    for f in Path(work_dir).iterdir():
        if f.suffix in (".mp3", ".m4a", ".ogg", ".wav", ".opus"):
            log.info(f"[1/6] audio: {f.name} ({f.stat().st_size / 1024:.1f} KB)")
            return str(f)
    raise RuntimeError("yt-dlp ran but produced no audio file")


# ── Step 2: transcribe ───────────────────────────────────────────────────────
def transcribe_audio(audio_path: str) -> str:
    import openai

    log.info(f"[2/6] transcribe: {audio_path}")
    size = os.path.getsize(audio_path)
    if size > WHISPER_MAX_BYTES:
        raise RuntimeError(
            f"audio is {size / 1024 / 1024:.1f} MB — Whisper rejects >25 MB"
        )

    client = openai.OpenAI(api_key=OPENAI_API_KEY)
    try:
        with open(audio_path, "rb") as f:
            resp = client.audio.transcriptions.create(
                file=f,
                model="whisper-1",
                language="en",
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )
    except Exception as e:
        raise RuntimeError(f"Whisper API error: {e}") from e

    text = resp.text or ""
    log.info(f"[2/6] transcript: {len(text)} chars")
    return text


# ── Step 3: process with Claude ──────────────────────────────────────────────
CLAUDE_SYSTEM_PROMPT = """You are the intelligence processor for ZiroWork's Research Brain.

Transform raw video transcripts into clean, actionable markdown for an Obsidian knowledge base.

TASK:
1. Remove all noise: sales pitches, sponsorships, "subscribe" calls, filler
2. Extract core intelligence: the actual insight being shared
3. Expand and clarify: fill in implied context, connect ideas
4. Structure as markdown: clear headers, timestamps, actionable takeaways

RULES:
- Sales pitches, ads, sponsorships: DELETE
- Tangents unrelated to core topic: DELETE
- Excessive pleasantries or filler: DELETE
- Every key point MUST include timestamp [MM:SS]
- Assume reader is technically capable (no over-explaining)
- No corporate jargon
- Direct, clear, professional tone

OUTPUT FORMAT:

# [Clear, Specific Title]

**Core Insight:** [1-2 sentence summary]

## Key Points

### 1. [Topic] [MM:SS-MM:SS]
[Explanation, expanded with context]
- Bullet point
- Bullet point

**Why it matters:** [Connection to AI/product building]

### 2. [Topic] [MM:SS-MM:SS]
[etc...]

## Actionable Takeaways
- [ ] [Specific, testable action]
- [ ] [Specific, testable action]
- [ ] [Specific, testable action]

## Related Concepts
- [[Concept 1]]
- [[Concept 2]]

OUTPUT MARKDOWN ONLY. NO PREAMBLE. JUST THE MARKDOWN."""


def process_with_claude(transcript: str, creator: str, category: str, source_url: str) -> str:
    import anthropic

    log.info(f"[3/6] Claude ({CLAUDE_MODEL})")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    user = (
        f"Creator: {creator}\n"
        f"Category: {category}\n"
        f"Source: {source_url}\n\n"
        f"TRANSCRIPT:\n{transcript}"
    )
    try:
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2000,
            system=CLAUDE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user}],
        )
    except Exception as e:
        raise RuntimeError(f"Claude API error: {e}") from e

    out = resp.content[0].text
    log.info(f"[3/6] Claude output: {len(out)} chars")
    return out


# ── Step 4: format markdown ──────────────────────────────────────────────────
def format_markdown(
    body: str, creator: str, category: str, source_url: str, date: str
) -> str:
    front_matter = (
        "---\n"
        f"date: {date}\n"
        f"creator: {creator}\n"
        f"category: {category}\n"
        f"source_url: {source_url}\n"
        f"processed_by: Claude ({CLAUDE_MODEL})\n"
        "---\n\n"
        f"**Source:** [{creator}]({source_url}) | **Date:** {date}\n\n"
    )
    return front_matter + body


# ── Step 5: save to Google Drive ─────────────────────────────────────────────
def save_to_drive(content: str, filename: str) -> Optional[str]:
    log.info(f"[5/6] save to Drive: {filename}")
    if not (GOOGLE_DRIVE_FOLDER_ID and GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON):
        log.warning("[5/6] Drive not configured — skipping")
        return None

    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaInMemoryUpload

    try:
        sa_info = json.loads(GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON)
        creds = Credentials.from_service_account_info(
            sa_info, scopes=["https://www.googleapis.com/auth/drive.file"]
        )
        service = build("drive", "v3", credentials=creds, cache_discovery=False)
        file = (
            service.files()
            .create(
                body={
                    "name": filename,
                    "parents": [GOOGLE_DRIVE_FOLDER_ID],
                    "mimeType": "text/markdown",
                },
                media_body=MediaInMemoryUpload(
                    content.encode("utf-8"), mimetype="text/markdown", resumable=False
                ),
                fields="id, webViewLink",
            )
            .execute()
        )
        url = file.get("webViewLink") or f"https://drive.google.com/file/d/{file['id']}/view"
        log.info(f"[5/6] saved: {url}")
        return url
    except Exception as e:
        log.error(f"[5/6] Drive write failed: {type(e).__name__}: {e}")
        return None


# ── API routes ───────────────────────────────────────────────────────────────
@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "ZiroWork Brain Agent",
        "version": "2.0.0",
        "spa_built": SPA_DIR.exists(),
        "drive_configured": bool(GOOGLE_DRIVE_FOLDER_ID and GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON),
        "openai_configured": bool(OPENAI_API_KEY),
        "anthropic_configured": bool(ANTHROPIC_API_KEY),
    }


@app.get("/api/config", response_model=ConfigResponse)
def get_config() -> ConfigResponse:
    return ConfigResponse(
        approved_creators=APPROVED_CREATORS,
        content_categories=CONTENT_CATEGORIES,
    )


@app.post("/api/process-video", response_model=ProcessVideoResponse)
def process_video(req: ProcessVideoRequest) -> ProcessVideoResponse:
    log.info(f"=== request: {req.instagram_link[:60]} ===")

    if not OPENAI_API_KEY or not ANTHROPIC_API_KEY:
        return ProcessVideoResponse(
            status="error",
            error="Server is missing OPENAI_API_KEY or ANTHROPIC_API_KEY. Set them in Railway Variables.",
            code="MISSING_CONFIG",
        )
    if not _valid_instagram_link(req.instagram_link):
        return ProcessVideoResponse(
            status="error",
            error="Invalid Instagram link. Must be an instagram.com /reel/, /p/, or /tv/ URL.",
            code="INVALID_LINK",
        )

    today = datetime.now().strftime("%Y-%m-%d")
    work_dir = tempfile.mkdtemp(prefix="brain_agent_")
    creator = None
    category = None

    try:
        creator = extract_creator_from_metadata(req.instagram_link, work_dir)
        filename = _build_filename(creator, today)

        try:
            audio_path = extract_audio(req.instagram_link, work_dir)
        except RuntimeError as e:
            return ProcessVideoResponse(status="error", error=str(e), code="EXTRACTION_FAILED")

        try:
            transcript = transcribe_audio(audio_path)
        except RuntimeError as e:
            return ProcessVideoResponse(
                status="error",
                error=f"Transcription failed: {e}. Check your OpenAI API key and audio file.",
                code="TRANSCRIPTION_FAILED",
            )

        category = auto_categorize_transcript(transcript)

        try:
            claude_output = process_with_claude(
                transcript=transcript,
                creator=creator,
                category=category,
                source_url=req.instagram_link,
            )
        except RuntimeError as e:
            log.warning(f"Claude failed, falling back to raw transcript: {e}")
            claude_output = (
                "# Processing Failed\n\n"
                "**Note:** Claude processing failed. Raw transcript below.\n\n---\n\n"
                + transcript
            )

        final_md = format_markdown(
            claude_output, creator, category, req.instagram_link, today
        )
        drive_url = save_to_drive(final_md, filename)
        message = (
            f"Saved to Google Drive: {filename}"
            if drive_url
            else "Processed successfully, but Google Drive save failed (check logs)."
        )
        preview = final_md[:1500] + ("..." if len(final_md) > 1500 else "")

        log.info(f"=== done: {filename} ({creator} / {category}) ===")
        return ProcessVideoResponse(
            status="success",
            filename=filename,
            drive_url=drive_url,
            preview=preview,
            message=message,
            creator=creator,
            category=category,
        )
    finally:
        log.info(f"[6/6] cleanup: {work_dir}")
        shutil.rmtree(work_dir, ignore_errors=True)


# ── SPA serving (must be registered after /api routes) ───────────────────────
if SPA_DIR.exists():
    # Serve hashed assets directly. Skip the mount if the assets dir is missing
    # (StaticFiles raises on a non-existent directory).
    _assets_dir = SPA_DIR / "assets"
    if _assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        # Anything under /api/* that wasn't matched above → 404 JSON, not index.html.
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        # Try to serve a real file (favicon, robots.txt, etc.).
        candidate = (SPA_DIR / full_path).resolve()
        if (
            candidate.is_file()
            and SPA_DIR in candidate.parents
        ):
            return FileResponse(candidate)
        # Otherwise hand the SPA index.html and let wouter handle the route.
        index = SPA_DIR / "index.html"
        if index.is_file():
            return FileResponse(index)
        raise HTTPException(status_code=404, detail="SPA not built")
else:
    log.warning(
        "SPA build not found at %s — only /api/* will respond. "
        "Run `pnpm build` before starting in production.",
        SPA_DIR,
    )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        log_level="info",
        access_log=True,
    )
