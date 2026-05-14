"""
ZiroWork Brain Agent — Backend API
Pipeline: Instagram Link → Audio Extract → Whisper Transcribe → Claude Process → Markdown → Google Drive → Cleanup
"""

import os
import re
import json
import logging
import tempfile
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

# Load .env
load_dotenv()

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
    ],
    force=True,
)
log = logging.getLogger("brain-agent")

# ── Config ───────────────────────────────────────────────────────────────────
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON", "")
TEMP_DIR = os.getenv("TEMP_DIR", "/tmp")

APPROVED_CREATORS = [c.strip() for c in os.getenv(
    "APPROVED_CREATORS",
    "Andrew Huberman,Simon Willison,Andrej Karpathy"
).split(",") if c.strip()]

CONTENT_CATEGORIES = [c.strip() for c in os.getenv(
    "CONTENT_CATEGORIES",
    "Agent Design,LLM Optimization,Product Strategy,AI Safety & Ethics,Technical Architecture,Business & Growth"
).split(",") if c.strip()]

CLAUDE_MODEL = "claude-opus-4-20250514"

# ── Startup Validation ────────────────────────────────────────────────────────
def validate_startup_config():
    """Validate critical env vars at startup. Fail fast if misconfigured."""
    errors = []

    if not OPENAI_API_KEY:
        errors.append("OPENAI_API_KEY is not set. Set it in Railway Variables.")
    if not ANTHROPIC_API_KEY:
        errors.append("ANTHROPIC_API_KEY is not set. Set it in Railway Variables.")

    if errors:
        log.error("STARTUP VALIDATION FAILED:")
        for err in errors:
            log.error(f"  - {err}")
        raise RuntimeError("Missing critical configuration. See logs above.")

    log.info("✓ Startup validation passed. All critical env vars configured.")

validate_startup_config()

# ── FastAPI App ───────────────────────────────────────────────────────────────
app = FastAPI(title="ZiroWork Brain Agent", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    """Log startup confirmation."""
    log.info(f"✓ ZiroWork Brain Agent started on port {os.getenv('PORT', '8000')}")
    log.info(f"  - Approved creators: {len(APPROVED_CREATORS)}")
    log.info(f"  - Content categories: {len(CONTENT_CATEGORIES)}")

# ── Request / Response Models ─────────────────────────────────────────────────
class ProcessVideoRequest(BaseModel):
    instagram_link: str
    creator: str
    category: str

class ProcessVideoResponse(BaseModel):
    status: str
    filename: Optional[str] = None
    drive_url: Optional[str] = None
    preview: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None
    code: Optional[str] = None

class ConfigResponse(BaseModel):
    approved_creators: list[str]
    content_categories: list[str]

# ── Helpers ───────────────────────────────────────────────────────────────────
def validate_instagram_link(link: str) -> bool:
    """Basic validation for Instagram URLs."""
    patterns = [
        r"https?://(www\.)?instagram\.com/(p|reel|tv)/[\w-]+",
        r"https?://(www\.)?instagram\.com/[\w.]+/(p|reel|tv)/[\w-]+",
    ]
    return any(re.match(p, link) for p in patterns)

def slugify_creator(name: str) -> str:
    """Convert 'Andrew Huberman' → 'andrew-huberman'."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

def build_filename(creator: str, date: str) -> str:
    return f"{date}-{slugify_creator(creator)}.md"

# ── Step 1: Extract Audio ─────────────────────────────────────────────────────
def extract_audio(instagram_link: str, temp_dir: str) -> str:
    """
    Use yt-dlp to extract audio from Instagram link.
    Returns path to audio file.
    """
    log.info(f"[STEP 1] Extracting audio from: {instagram_link}")
    output_template = os.path.join(temp_dir, "audio.%(ext)s")

    cmd = [
        "yt-dlp",
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "5",
        "--no-playlist",
        "--no-warnings",
        "--output", output_template,
        instagram_link,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            log.error(f"yt-dlp stderr: {result.stderr}")
            raise RuntimeError(f"yt-dlp failed: {result.stderr[:300]}")

        # Find the output file
        for f in Path(temp_dir).iterdir():
            if f.suffix in (".mp3", ".m4a", ".ogg", ".wav", ".opus"):
                log.info(f"[STEP 1] Audio extracted: {f} ({f.stat().st_size / 1024:.1f} KB)")
                return str(f)

        raise RuntimeError("yt-dlp ran but no audio file found.")
    except subprocess.TimeoutExpired:
        raise RuntimeError("Audio extraction timed out (>2 min).")

# ── Step 2: Transcribe ────────────────────────────────────────────────────────
def transcribe_audio(audio_path: str) -> dict:
    """
    Transcribe audio using OpenAI Whisper API.
    Returns dict with 'text' and 'segments'.
    """
    import openai

    log.info(f"[STEP 2] Transcribing: {audio_path}")
    client = openai.OpenAI(api_key=OPENAI_API_KEY)

    file_size = os.path.getsize(audio_path)
    if file_size > 25 * 1024 * 1024:
        log.warning(f"[STEP 2] Audio file > 25MB ({file_size/1024/1024:.1f} MB). Whisper may reject it.")

    try:
        with open(audio_path, "rb") as f:
            response = client.audio.transcriptions.create(
                file=f,
                model="whisper-1",
                language="en",
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )
        text = response.text
        segments = getattr(response, "segments", [])
        log.info(f"[STEP 2] Transcription complete. {len(text)} chars, {len(segments)} segments.")
        return {"text": text, "segments": segments}
    except Exception as e:
        log.error(f"[STEP 2] Whisper failed: {e}")
        raise RuntimeError(f"Transcription failed: {str(e)}")

# ── Step 3: Process with Claude ───────────────────────────────────────────────
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
    """
    Send transcript to Claude for cleaning and structuring.
    Returns clean markdown string.
    """
    import anthropic

    log.info(f"[STEP 3] Processing with Claude ({CLAUDE_MODEL})")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    user_message = f"""Creator: {creator}
Category: {category}
Source: {source_url}

TRANSCRIPT:
{transcript}"""

    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2000,
            system=CLAUDE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        result = response.content[0].text
        log.info(f"[STEP 3] Claude output: {len(result)} chars")
        return result
    except Exception as e:
        log.error(f"[STEP 3] Claude failed: {e}")
        raise RuntimeError(f"Claude processing failed: {str(e)}")

# ── Step 4: Format Markdown ───────────────────────────────────────────────────
def format_markdown(
    claude_output: str,
    creator: str,
    category: str,
    source_url: str,
    date: str,
    duration: str = "unknown",
) -> str:
    """Wrap Claude output with YAML front matter."""
    log.info("[STEP 4] Formatting markdown with front matter")
    front_matter = f"""---
date: {date}
creator: {creator}
category: {category}
source_url: {source_url}
duration: {duration}
processed_by: Claude ({CLAUDE_MODEL})
---

**Source:** [{creator}]({source_url}) | **Date:** {date} | **Duration:** {duration}

"""
    return front_matter + claude_output

# ── Step 5: Save to Google Drive ──────────────────────────────────────────────
def save_to_google_drive(content: str, filename: str) -> Optional[str]:
    """
    Write markdown file to Google Drive folder.
    Returns the Drive file URL, or None if failed (non-blocking).
    """
    log.info(f"[STEP 5] Saving to Google Drive: {filename}")

    if not GOOGLE_DRIVE_FOLDER_ID:
        log.warning("[STEP 5] GOOGLE_DRIVE_FOLDER_ID not configured — skipping.")
        return None
    if not GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON:
        log.warning("[STEP 5] GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON not configured — skipping.")
        return None

    from googleapiclient.discovery import build
    from googleapiclient.http import MediaInMemoryUpload
    from google.oauth2.service_account import Credentials

    try:
        sa_info = json.loads(GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON)
        creds = Credentials.from_service_account_info(
            sa_info,
            scopes=["https://www.googleapis.com/auth/drive.file"],
        )
        service = build("drive", "v3", credentials=creds)

        file_metadata = {
            "name": filename,
            "parents": [GOOGLE_DRIVE_FOLDER_ID],
            "mimeType": "text/markdown",
        }
        media = MediaInMemoryUpload(
            content.encode("utf-8"),
            mimetype="text/markdown",
            resumable=False,
        )
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, webViewLink",
        ).execute()

        drive_url = file.get("webViewLink", f"https://drive.google.com/file/d/{file['id']}/view")
        log.info(f"[STEP 5] Saved to Drive: {drive_url}")
        return drive_url
    except Exception as e:
        log.error(f"[STEP 5] Drive write failed: {type(e).__name__}: {e}")
        return None

# ── Step 6: Cleanup ───────────────────────────────────────────────────────────
def cleanup_temp_files(temp_dir: str):
    """Delete temporary audio files."""
    log.info(f"[STEP 6] Cleaning up: {temp_dir}")
    try:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
        log.info("[STEP 6] Cleanup complete.")
    except Exception as e:
        log.warning(f"[STEP 6] Cleanup warning (non-blocking): {e}")

# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/api/config")
def get_config() -> ConfigResponse:
    """Return approved creators and content categories."""
    return ConfigResponse(
        approved_creators=APPROVED_CREATORS,
        content_categories=CONTENT_CATEGORIES,
    )

@app.get("/api/health")
def health():
    return {"status": "ok", "service": "ZiroWork Brain Agent"}

@app.post("/api/process-video")
def process_video(req: ProcessVideoRequest) -> ProcessVideoResponse:
    """
    Main pipeline endpoint.
    POST /api/process-video
    { instagram_link, creator, category }
    """
    log.info(f"=== NEW REQUEST: {req.creator} / {req.category} / {req.instagram_link[:60]} ===")

    # Validate link
    if not validate_instagram_link(req.instagram_link):
        log.warning(f"Invalid Instagram link: {req.instagram_link}")
        return ProcessVideoResponse(
            status="error",
            error="Invalid Instagram link. Must be an instagram.com/reel/, /p/, or /tv/ URL.",
            code="INVALID_LINK",
        )

    # Validate creator
    if req.creator not in APPROVED_CREATORS:
        return ProcessVideoResponse(
            status="error",
            error=f"Creator '{req.creator}' is not in the approved list.",
            code="INVALID_CREATOR",
        )

    # Validate category
    if req.category not in CONTENT_CATEGORIES:
        return ProcessVideoResponse(
            status="error",
            error=f"Category '{req.category}' is not valid.",
            code="INVALID_CATEGORY",
        )

    today = datetime.now().strftime("%Y-%m-%d")
    filename = build_filename(req.creator, today)
    temp_dir = tempfile.mkdtemp(dir=TEMP_DIR, prefix="brain_agent_")
    raw_transcript = ""
    audio_path = None

    try:
        # Step 1: Extract audio
        try:
            audio_path = extract_audio(req.instagram_link, temp_dir)
        except RuntimeError as e:
            return ProcessVideoResponse(
                status="error",
                error=str(e),
                code="EXTRACTION_FAILED",
            )

        # Step 2: Transcribe
        try:
            transcription = transcribe_audio(audio_path)
            raw_transcript = transcription["text"]
        except RuntimeError as e:
            return ProcessVideoResponse(
                status="error",
                error=f"Transcription failed: {str(e)}. Check your OpenAI API key and audio file.",
                code="TRANSCRIPTION_FAILED",
            )

        # Step 3: Process with Claude
        try:
            claude_output = process_with_claude(
                transcript=raw_transcript,
                creator=req.creator,
                category=req.category,
                source_url=req.instagram_link,
            )
        except RuntimeError as e:
            claude_output = f"# Processing Failed\n\n**Note:** Claude processing failed. Raw transcript below.\n\n---\n\n{raw_transcript}"
            log.warning(f"Claude failed, saving raw transcript: {e}")

        # Step 4: Format markdown
        final_markdown = format_markdown(
            claude_output=claude_output,
            creator=req.creator,
            category=req.category,
            source_url=req.instagram_link,
            date=today,
        )

        # Step 5: Save to Google Drive (non-blocking)
        drive_url = save_to_google_drive(final_markdown, filename)
        if drive_url:
            message = f"✓ Saved to ZiroWork-Brain/Raw Videos/ → {filename}"
        else:
            message = "⚠ Processed successfully. Google Drive save failed (check logs). Markdown not saved to Drive."
            log.warning("[STEP 5] Drive write failed — markdown processed but not saved.")

        # Preview: first 1500 chars
        preview = final_markdown[:1500] + ("..." if len(final_markdown) > 1500 else "")

        log.info(f"=== SUCCESS: {filename} ===")
        return ProcessVideoResponse(
            status="success",
            filename=filename,
            drive_url=drive_url,
            preview=preview,
            message=message,
        )

    finally:
        # Step 6: Cleanup
        cleanup_temp_files(temp_dir)


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
