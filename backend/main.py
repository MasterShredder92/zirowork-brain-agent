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
from typing import Optional, Tuple
import base64
from apify_client import ApifyClient

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
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "10oKB6NWeo8IbxQ6ZJ--7ckKz1F3Y5et0").strip()
GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON", "")
PUBLIC_SUBMISSIONS_ENABLED = os.getenv("PUBLIC_SUBMISSIONS_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
PUBLIC_REVIEW_FOLDER_ID = os.getenv("PUBLIC_REVIEW_FOLDER_ID", "").strip()
PUBLIC_HIGH_IMPORTANCE_FOLDER_ID = os.getenv("PUBLIC_HIGH_IMPORTANCE_FOLDER_ID", "").strip()
PUBLIC_MEDIUM_IMPORTANCE_FOLDER_ID = os.getenv("PUBLIC_MEDIUM_IMPORTANCE_FOLDER_ID", "").strip()
PUBLIC_LOW_IMPORTANCE_FOLDER_ID = os.getenv("PUBLIC_LOW_IMPORTANCE_FOLDER_ID", "").strip()
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
PUBLIC_FROM_EMAIL = os.getenv("PUBLIC_FROM_EMAIL", "ZiroWork <research@zirowork.com>").strip()
PUBLIC_REPLY_TO_EMAIL = os.getenv("PUBLIC_REPLY_TO_EMAIL", "").strip()
# OAuth credentials (preferred over service account — uses your personal Drive quota)
# Set GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, GOOGLE_OAUTH_REFRESH_TOKEN in Railway Variables
GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
GOOGLE_OAUTH_REFRESH_TOKEN = os.getenv("GOOGLE_OAUTH_REFRESH_TOKEN", "").strip()
INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME", "")
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD", "")
APIFY_API_TOKEN = os.getenv("APIFY_API_TOKEN", "").strip()
APIFY_ACTOR_ID = "shu8hvrXbJbY3Eb9W"  # Instagram video downloader actor

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

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001").strip()
CLAUDE_FALLBACK_MODELS = [
    model
    for model in [
        CLAUDE_MODEL,
        "claude-haiku-4-5",
    ]
    if model
]
CLAUDE_FALLBACK_MODELS = list(dict.fromkeys(CLAUDE_FALLBACK_MODELS))
CLAUDE_MAX_TOKENS = int(os.getenv("CLAUDE_MAX_TOKENS", "2500"))
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
    mode: Optional[str] = "private"
    email: Optional[str] = None
    name: Optional[str] = None


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
    mode: Optional[str] = None
    email_sent: Optional[bool] = None
    importance: Optional[str] = None


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


def _extract_title_from_markdown(markdown: str) -> Optional[str]:
    """Extract the first real H1 title from Claude's markdown output."""
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped.startswith("# "):
            continue

        title = stripped.lstrip("#").strip()
        title = re.sub(r"[*_`\[\]]+", "", title).strip()
        if not title:
            continue
        if title.lower() in {"processing failed", "untitled", "unknown"}:
            continue
        return title

    return None


def _is_unknown_creator(creator: Optional[str]) -> bool:
    return _slugify(creator or "") in {"", "unknown", "unknown-creator"}


def _build_filename_from_claude_output(
    claude_output: str,
    creator: Optional[str],
    category: Optional[str],
    date: str,
) -> str:
    """
    Build Drive filename from Claude's video-topic title instead of creator.

    Primary: first H1 from Claude output, which should describe what the video is about.
    Fallbacks avoid producing generic `unknown-creator` filenames when metadata fails.
    """
    topic = _extract_title_from_markdown(claude_output)
    if topic:
        slug = _slugify(topic)
    elif category:
        slug = _slugify(category)
    elif creator and not _is_unknown_creator(creator):
        slug = _slugify(creator)
    else:
        slug = "instagram-video"

    return f"{date}-{slug or 'instagram-video'}.md"


def _normalize_mode(mode: Optional[str]) -> str:
    normalized = (mode or "private").strip().lower()
    if normalized in {"private", "zach", "internal"}:
        return "private"
    if normalized in {"public", "share"}:
        return "public"
    return "invalid"


def _valid_email(email: Optional[str]) -> bool:
    if not email:
        return False
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email.strip()))


def _public_config_error() -> Optional[str]:
    missing = []
    if not PUBLIC_SUBMISSIONS_ENABLED:
        return "Public submissions are disabled."
    if not PUBLIC_REVIEW_FOLDER_ID:
        missing.append("PUBLIC_REVIEW_FOLDER_ID")
    if not RESEND_API_KEY:
        missing.append("RESEND_API_KEY")
    if missing:
        return "Public share mode is missing Railway variables: " + ", ".join(missing)
    return None


def _score_public_importance(transcript: str, category: str, claude_output: str) -> tuple[str, int, str]:
    haystack = f"{category}\n{claude_output}\n{transcript}".lower()
    score = 0
    reasons = []

    category_scores = {
        "agent design": 30,
        "product strategy": 25,
        "llm optimization": 22,
        "technical architecture": 20,
        "business & growth": 18,
        "ai safety & ethics": 8,
    }
    for key, value in category_scores.items():
        if key in (category or "").lower():
            score += value
            reasons.append(f"category:{category}")
            break

    high_terms = [
        "claude code", "agent", "automation", "workflow", "prompt", "system",
        "conversion", "sales", "lead", "mrr", "revenue", "pricing", "landing page",
        "website", "product", "tool", "template", "framework", "content engine",
    ]
    medium_terms = ["ai", "llm", "cursor", "dev", "design", "marketing", "growth", "copy", "seo"]

    high_hits = [term for term in high_terms if term in haystack]
    medium_hits = [term for term in medium_terms if term in haystack]
    score += min(len(high_hits) * 8, 48)
    score += min(len(medium_hits) * 3, 18)
    if high_hits:
        reasons.append("high_signal_terms:" + ",".join(high_hits[:6]))
    if medium_hits:
        reasons.append("medium_signal_terms:" + ",".join(medium_hits[:6]))

    if len(transcript) >= 1200:
        score += 8
        reasons.append("substantive_transcript")
    elif len(transcript) < 300:
        score -= 12
        reasons.append("short_transcript")

    if "processing failed" in claude_output.lower():
        score -= 18
        reasons.append("claude_processing_failed")

    if score >= 55:
        importance = "high"
    elif score >= 28:
        importance = "medium"
    else:
        importance = "low"

    return importance, max(score, 0), "; ".join(reasons) or "low signal"


def _public_review_folder_id(importance: str) -> str:
    if importance == "high" and PUBLIC_HIGH_IMPORTANCE_FOLDER_ID:
        return PUBLIC_HIGH_IMPORTANCE_FOLDER_ID
    if importance == "medium" and PUBLIC_MEDIUM_IMPORTANCE_FOLDER_ID:
        return PUBLIC_MEDIUM_IMPORTANCE_FOLDER_ID
    if importance == "low" and PUBLIC_LOW_IMPORTANCE_FOLDER_ID:
        return PUBLIC_LOW_IMPORTANCE_FOLDER_ID
    return PUBLIC_REVIEW_FOLDER_ID


def _prepend_public_review_metadata(
    markdown: str,
    email: str,
    name: Optional[str],
    importance: str,
    score: int,
    reason: str,
) -> str:
    submitter_name = (name or "").strip() or "Not provided"
    return (
        "---\n"
        "submission_mode: public_share\n"
        f"submitter_email: {email.strip()}\n"
        f"submitter_name: {submitter_name}\n"
        f"importance: {importance}\n"
        f"importance_score: {score}\n"
        f"importance_reason: {reason}\n"
        "---\n\n"
        "# Public Submission Review\n\n"
        f"**Submitter:** {submitter_name} <{email.strip()}>\n"
        f"**Importance:** {importance.title()} ({score})\n"
        f"**Reason:** {reason}\n\n"
        "---\n\n"
        + markdown
    )


def send_markdown_email(to_email: str, filename: str, markdown: str) -> tuple[bool, Optional[str]]:
    log.info(f"[5/6] email public output to {to_email}: {filename}")
    if not RESEND_API_KEY:
        return False, "RESEND_API_KEY not configured."

    import httpx

    attachment_content = base64.b64encode(markdown.encode("utf-8")).decode("ascii")
    payload = {
        "from": PUBLIC_FROM_EMAIL,
        "to": [to_email.strip()],
        "subject": f"Your ZiroWork Instagram breakdown: {filename}",
        "text": (
            "Your Instagram breakdown is attached as a Markdown file.\n\n"
            "You can save it to Notes, Obsidian, Google Drive, or any text editor.\n\n"
            "— ZiroWork"
        ),
        "attachments": [
            {
                "filename": filename,
                "content": attachment_content,
            }
        ],
    }
    if PUBLIC_REPLY_TO_EMAIL:
        payload["reply_to"] = PUBLIC_REPLY_TO_EMAIL

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if resp.status_code not in {200, 202}:
            error = f"Resend HTTP {resp.status_code}: {resp.text[:500]}"
            log.error(f"[5/6] email failed: {error}")
            return False, error
        log.info("[5/6] email sent")
        return True, None
    except Exception as e:
        error = f"Email send failed: {type(e).__name__}: {e}"
        log.error(f"[5/6] {error}")
        return False, error




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
    """Score categories by keyword frequency. No anthropic, no httpx."""
    log.info(f"[3b/6] auto-categorizing transcript")
    transcript_lower = transcript.lower()
    scores = {cat: 0 for cat in CONTENT_CATEGORIES}

    keyword_map = {
        "Agent Design": ["agent", "agentic", "autonomous", "planning"],
        "LLM Optimization": ["model", "llm", "training", "fine-tune", "inference", "prompt"],
        "Product Strategy": ["product", "strategy", "market", "launch", "user"],
        "AI Safety & Ethics": ["safety", "ethics", "alignment", "risk", "bias"],
        "Technical Architecture": ["architecture", "system", "infrastructure", "scaling", "distributed"],
        "Business & Growth": ["business", "growth", "monetization", "acquisition", "metric"],
    }

    for cat, keywords in keyword_map.items():
        if cat in CONTENT_CATEGORIES:
            scores[cat] = sum(transcript_lower.count(kw) for kw in keywords)

    best = max(scores, key=scores.get)
    log.info(f"[3b/6] auto-categorized: {best}")
    return best


# ── Step 1: extract audio ────────────────────────────────────────────────────
def extract_audio(instagram_link: str, work_dir: str) -> str:
    """
    Download audio from Instagram via Apify.

    The Apify payload can contain multiple signed Instagram/Facebook CDN URLs. Those URLs
    can be short-lived or intermittently blocked by the CDN, so we try every plausible
    video URL with browser-like headers before failing the extraction step.
    """
    log.info(f"[1/6] extract audio via Apify: {instagram_link}")

    if not APIFY_API_TOKEN:
        log.error("[1/6] APIFY_API_TOKEN not configured")
        raise RuntimeError(
            "APIFY_API_TOKEN is not set in Railway Variables. "
            "Set it to your Apify API token from https://apify.com/account/integrations"
        )

    def _flatten_urls(value) -> list[str]:
        urls: list[str] = []
        if isinstance(value, str):
            clean = value.strip().replace("&amp;", "&").replace("\\u0026", "&")
            if clean.lower().startswith(("http://", "https://")):
                urls.append(re.sub(r"^https?://", lambda m: m.group(0).lower(), clean, flags=re.I))
        elif isinstance(value, list):
            for item in value:
                urls.extend(_flatten_urls(item))
        elif isinstance(value, dict):
            for item in value.values():
                urls.extend(_flatten_urls(item))
        return urls

    def _candidate_media_urls(post: dict) -> list[str]:
        priority_keys = [
            "videoUrl",
            "videoUrls",
            "video_url",
            "video",
            "videos",
            "mediaUrl",
            "media_url",
            "displayUrl",
            "display_url",
        ]
        candidates: list[str] = []
        for key in priority_keys:
            candidates.extend(_flatten_urls(post.get(key)))

        # Fallback: actors change schemas. Pull any nested URL under video/media keys.
        for key, value in post.items():
            key_lower = str(key).lower()
            if "video" in key_lower or "media" in key_lower:
                candidates.extend(_flatten_urls(value))

        seen = set()
        deduped: list[str] = []
        for url in candidates:
            if url in seen:
                continue
            seen.add(url)
            deduped.append(url)
        return deduped

    # Step 1: Run Apify Instagram actor to get media URLs.
    log.info("[1/6] running Apify Instagram actor...")
    client = ApifyClient(APIFY_API_TOKEN)

    try:
        run_input = {
            "directUrls": [instagram_link],
            "resultsLimit": 1,
            "resultsType": "posts",
        }

        run = client.actor(APIFY_ACTOR_ID).call(run_input=run_input)
        log.info(f"[1/6] Apify actor finished: {run['status']}")

        results = list(client.dataset(run["defaultDatasetId"]).iterate_items())

        if not results:
            log.error("[1/6] Apify returned no results")
            raise RuntimeError("Apify actor returned no video data. Video may be private or deleted.")

        post = results[0]
        log.info(f"[1/6] Apify found post by {post.get('ownerUsername', 'Unknown')}")
        video_urls = _candidate_media_urls(post)

        if not video_urls:
            log.error(f"[1/6] No video URL in Apify response: {post.keys()}")
            raise RuntimeError("Apify found the post but no video URL. May be a carousel or unsupported format.")

        log.info(f"[1/6] Apify returned {len(video_urls)} candidate media URL(s)")

    except Exception as e:
        log.error(f"[1/6] Apify actor failed: {type(e).__name__}: {e}")
        raise RuntimeError(f"Apify Instagram actor failed: {str(e)[:250]}") from e

    # Step 2 + 3: Download each candidate and extract audio until one works.
    log.info("[1/6] downloading video file...")
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    session = requests.Session()
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept": "video/mp4,video/*;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
        "Referer": "https://www.instagram.com/",
    }

    video_path = os.path.join(work_dir, "video.mp4")
    audio_path = os.path.join(work_dir, "audio.mp3")
    last_error = None

    for index, video_url in enumerate(video_urls, start=1):
        for output_path in (video_path, audio_path):
            if os.path.exists(output_path):
                os.remove(output_path)

        try:
            log.info(f"[1/6] downloading candidate {index}/{len(video_urls)}: {video_url[:80]}...")
            with session.get(
                video_url,
                timeout=(15, 180),
                stream=True,
                allow_redirects=True,
                headers=headers,
            ) as video_response:
                video_response.raise_for_status()
                content_type = video_response.headers.get("content-type", "").lower()
                if content_type.startswith("image/"):
                    raise RuntimeError(f"candidate is image content ({content_type}), not video")

                with open(video_path, "wb") as f:
                    for chunk in video_response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)

            video_size = os.path.getsize(video_path)
            if video_size < 10 * 1024:
                raise RuntimeError(f"downloaded media was too small ({video_size} bytes)")
            log.info(f"[1/6] video downloaded: {video_size / 1024 / 1024:.1f} MB")

            log.info("[1/6] extracting audio from video with ffmpeg...")
            cmd = [
                "ffmpeg",
                "-y",
                "-i", video_path,
                "-q:a", "5",
                audio_path,
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )

            if result.returncode != 0 or not os.path.exists(audio_path):
                raise RuntimeError(f"ffmpeg failed: {result.stderr.strip()[:300]}")

            audio_size = os.path.getsize(audio_path)
            if audio_size <= 0:
                raise RuntimeError("ffmpeg produced an empty audio file")

            log.info(f"[1/6] audio extracted: {audio_size / 1024:.1f} KB")
            return audio_path

        except subprocess.TimeoutExpired as e:
            last_error = "ffmpeg timed out after 120s"
            log.warning(f"[1/6] candidate {index} failed: {last_error}")
            if index == len(video_urls):
                raise RuntimeError(last_error) from e
        except FileNotFoundError as e:
            raise RuntimeError("ffmpeg not installed in this environment") from e
        except Exception as e:
            last_error = f"{type(e).__name__}: {str(e)[:220]}"
            log.warning(f"[1/6] candidate {index} failed: {last_error}")
            continue

    raise RuntimeError(
        "Failed to download/extract video from Apify media URLs. "
        f"Tried {len(video_urls)} candidate(s). Last error: {last_error}"
    )


# ── Step 2: transcribe ───────────────────────────────────────────────────────
def transcribe_audio(audio_path: str) -> str:
    """
    Transcribe audio using raw httpx POST to OpenAI Whisper API.
    Bypasses the OpenAI SDK connection handling that fails on Railway.
    Retries 4 times with exponential backoff.
    """
    import httpx
    import time

    log.info(f"[2/6] transcribe: {audio_path}")
    size = os.path.getsize(audio_path)
    if size > WHISPER_MAX_BYTES:
        raise RuntimeError(
            f"audio is {size / 1024 / 1024:.1f} MB — Whisper rejects >25 MB"
        )

    last_error = None
    for attempt in range(1, 5):  # 4 attempts
        try:
            with open(audio_path, "rb") as f:
                audio_bytes = f.read()

            with httpx.Client(timeout=120.0) as client:
                response = client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                    files={"file": (os.path.basename(audio_path), audio_bytes, "audio/mpeg")},
                    data={
                        "model": "whisper-1",
                        "language": "en",
                        "response_format": "verbose_json",
                        "timestamp_granularities[]": "segment",
                    },
                )

            if response.status_code == 200:
                result = response.json()
                text = result.get("text", "")
                log.info(f"[2/6] transcript: {len(text)} chars (attempt {attempt})")
                return text
            elif response.status_code == 429:
                wait = 2 ** attempt
                log.warning(f"[2/6] Whisper rate limited (attempt {attempt}/4). Retrying in {wait}s...")
                time.sleep(wait)
                last_error = RuntimeError(f"Whisper rate limited: {response.text}")
            else:
                raise RuntimeError(f"Whisper HTTP {response.status_code}: {response.text}")

        except httpx.ConnectError as e:
            wait = 2 ** attempt
            log.warning(f"[2/6] Whisper connection error (attempt {attempt}/4): {e}. Retrying in {wait}s...")
            last_error = e
            if attempt < 4:
                time.sleep(wait)
        except RuntimeError:
            raise
        except Exception as e:
            wait = 2 ** attempt
            log.warning(f"[2/6] Whisper error (attempt {attempt}/4): {type(e).__name__}: {e}. Retrying in {wait}s...")
            last_error = e
            if attempt < 4:
                time.sleep(wait)

    raise RuntimeError(f"Whisper API error: {last_error}")


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
    """
    Process transcript with Claude using raw httpx POST.

    Haiku occasionally fails by model/version availability or output budget. Keep the
    primary model configurable, then fall through known Haiku IDs before returning a
    raw-transcript fallback.
    """
    import httpx

    user = (
        f"Creator: {creator}\n"
        f"Category: {category}\n"
        f"Source: {source_url}\n\n"
        f"TRANSCRIPT:\n{transcript}"
    )
    last_error = None

    for model in CLAUDE_FALLBACK_MODELS:
        log.info(f"[3/6] Claude ({model})")
        payload = {
            "model": model,
            "max_tokens": CLAUDE_MAX_TOKENS,
            "system": CLAUDE_SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user}],
        }
        try:
            with httpx.Client(timeout=120.0) as client:
                resp = client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json=payload,
                )
            if resp.status_code != 200:
                last_error = f"Claude HTTP {resp.status_code}: {resp.text[:500]}"
                log.warning(f"[3/6] Claude model failed ({model}): {last_error}")
                continue

            data = resp.json()
            content_blocks = data.get("content", [])
            text_blocks = [
                block.get("text", "")
                for block in content_blocks
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            out = "\n".join(block for block in text_blocks if block).strip()
            if not out:
                last_error = f"Claude returned no text content: {str(data)[:500]}"
                log.warning(f"[3/6] Claude model failed ({model}): {last_error}")
                continue

            log.info(f"[3/6] Claude output from {model}: {len(out)} chars")
            return out
        except Exception as e:
            last_error = f"Claude API error on {model}: {type(e).__name__}: {e}"
            log.warning(f"[3/6] {last_error}")
            continue

    raise RuntimeError(last_error or "Claude API failed for all configured Haiku models")


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
def _get_drive_credentials():
    """Return OAuth2 credentials using refresh token (preferred) or service account."""
    if GOOGLE_OAUTH_REFRESH_TOKEN and GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        creds = Credentials(
            token=None,
            refresh_token=GOOGLE_OAUTH_REFRESH_TOKEN,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=GOOGLE_OAUTH_CLIENT_ID,
            client_secret=GOOGLE_OAUTH_CLIENT_SECRET,
            scopes=["https://www.googleapis.com/auth/drive"],
        )
        creds.refresh(Request())
        log.info("[5/6] using OAuth credentials (personal Drive quota)")
        return creds
    elif GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON:
        from google.oauth2.service_account import Credentials
        sa_info = json.loads(GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON)
        creds = Credentials.from_service_account_info(
            sa_info, scopes=["https://www.googleapis.com/auth/drive"]
        )
        log.info("[5/6] using service account credentials")
        return creds
    return None


def save_to_drive(content: str, filename: str, folder_id: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    target_folder_id = (folder_id or GOOGLE_DRIVE_FOLDER_ID or "").strip()
    log.info(f"[5/6] save to Drive: {filename} -> {target_folder_id[:8]}...")
    if not target_folder_id:
        return None, "Google Drive folder ID not set in Railway Variables."

    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaInMemoryUpload

    try:
        creds = _get_drive_credentials()
        if not creds:
            return None, "No Drive credentials configured. Set GOOGLE_OAUTH_REFRESH_TOKEN or GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON."
        service = build("drive", "v3", credentials=creds, cache_discovery=False)
        file = (
            service.files()
            .create(
                body={
                    "name": filename,
                    "parents": [target_folder_id],
                    "mimeType": "text/markdown",
                },
                media_body=MediaInMemoryUpload(
                    content.encode("utf-8"), mimetype="text/markdown", resumable=False
                ),
                fields="id, webViewLink",
                supportsAllDrives=True,
            )
            .execute()
        )
        url = file.get("webViewLink") or f"https://drive.google.com/file/d/{file['id']}/view"
        log.info(f"[5/6] saved: {url}")
        return url, None
    except HttpError as e:
        status = getattr(e.resp, "status", None)
        reason = str(e).lower()
        if status == 404:
            error = f"Drive folder not found: {target_folder_id}. Check the configured Drive folder ID in Railway."
        elif status == 403:
            if "storagequotaexceeded" in reason or "quota" in reason:
                error = "Google Drive storage quota exceeded on the authenticated account."
            else:
                error = f"Google Drive permission denied (403). Ensure the folder is shared with the authenticated account."
        else:
            error = f"Google Drive API error {status}: {e}"
        log.error(f"[5/6] Drive write failed: {error}")
        return None, error
    except Exception as e:
        error = f"Google Drive write failed: {type(e).__name__}: {e}"
        log.error(f"[5/6] {error}")
        return None, error


# ── API routes ───────────────────────────────────────────────────────────────
@app.get("/api/health")
def health() -> dict:
    import subprocess
    try:
        commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        commit = os.getenv("RAILWAY_GIT_COMMIT_SHA", "unknown")[:7]
    return {
        "status": "ok",
        "service": "ZiroWork Brain Agent",
        "version": "2.0.1",
        "commit": commit,
        "spa_built": SPA_DIR.exists(),
        "drive_configured": bool(GOOGLE_DRIVE_FOLDER_ID and (GOOGLE_OAUTH_REFRESH_TOKEN or GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON)),
        "drive_auth_method": "oauth" if GOOGLE_OAUTH_REFRESH_TOKEN else ("service_account" if GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON else "none"),
        "drive_folder_id": GOOGLE_DRIVE_FOLDER_ID[:8] + "..." if GOOGLE_DRIVE_FOLDER_ID else "not set",
        "claude_model": CLAUDE_MODEL,
        "claude_fallback_models": CLAUDE_FALLBACK_MODELS,
        "claude_max_tokens": CLAUDE_MAX_TOKENS,
        "public_submissions_enabled": PUBLIC_SUBMISSIONS_ENABLED,
        "public_review_folder_configured": bool(PUBLIC_REVIEW_FOLDER_ID),
        "public_email_configured": bool(RESEND_API_KEY),
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
    mode = _normalize_mode(req.mode)
    log.info(f"=== request: mode={mode} link={req.instagram_link[:60]} ===")

    if mode == "invalid":
        return ProcessVideoResponse(
            status="error",
            error="Invalid mode. Use private or public.",
            code="INVALID_MODE",
        )
    if mode == "public":
        public_config_error = _public_config_error()
        if public_config_error:
            return ProcessVideoResponse(
                status="error",
                error=public_config_error,
                code="MISSING_PUBLIC_CONFIG",
                mode=mode,
            )
        if not _valid_email(req.email):
            return ProcessVideoResponse(
                status="error",
                error="A valid email address is required for public share mode.",
                code="INVALID_EMAIL",
                mode=mode,
            )

    if not OPENAI_API_KEY or not ANTHROPIC_API_KEY:
        return ProcessVideoResponse(
            status="error",
            error="Server is missing OPENAI_API_KEY or ANTHROPIC_API_KEY. Set them in Railway Variables.",
            code="MISSING_CONFIG",
            mode=mode,
        )
    if not _valid_instagram_link(req.instagram_link):
        return ProcessVideoResponse(
            status="error",
            error="Invalid Instagram link. Must be an instagram.com /reel/, /p/, or /tv/ URL.",
            code="INVALID_LINK",
            mode=mode,
        )

    today = datetime.now().strftime("%Y-%m-%d")
    work_dir = tempfile.mkdtemp(prefix="brain_agent_")
    creator = None
    category = None

    try:
        creator = extract_creator_from_metadata(req.instagram_link)

        try:
            audio_path = extract_audio(req.instagram_link, work_dir)
        except RuntimeError as e:
            return ProcessVideoResponse(status="error", error=str(e), code="EXTRACTION_FAILED", mode=mode)

        try:
            transcript = transcribe_audio(audio_path)
        except RuntimeError as e:
            return ProcessVideoResponse(
                status="error",
                error=f"Transcription failed: {e}. Check your OpenAI API key and audio file.",
                code="TRANSCRIPTION_FAILED",
                mode=mode,
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
            safe_error = str(e).replace("\n", " ")[:700]
            claude_output = (
                "# Processing Failed\n\n"
                "**Note:** Claude processing failed. Raw transcript below.\n\n"
                f"**Claude error:** `{safe_error}`\n\n---\n\n"
                + transcript
            )

        filename = _build_filename_from_claude_output(
            claude_output=claude_output,
            creator=creator,
            category=category,
            date=today,
        )
        final_md = format_markdown(
            claude_output, creator, category, req.instagram_link, today
        )

        importance = None
        email_sent = None

        if mode == "public":
            assert req.email is not None
            importance, score, reason = _score_public_importance(transcript, category, claude_output)
            review_folder_id = _public_review_folder_id(importance)
            review_filename = f"{today}-{importance}-{_slugify(req.email.split('@')[0])}-{filename.removeprefix(today + '-') }"
            review_md = _prepend_public_review_metadata(
                markdown=final_md,
                email=req.email,
                name=req.name,
                importance=importance,
                score=score,
                reason=reason,
            )
            internal_drive_url, drive_error = save_to_drive(review_md, review_filename, folder_id=review_folder_id)
            if not internal_drive_url:
                return ProcessVideoResponse(
                    status="error",
                    error=f"Processed successfully, but internal review copy failed to save: {drive_error}",
                    code="PUBLIC_REVIEW_SAVE_FAILED",
                    mode=mode,
                    creator=creator,
                )

            email_sent, email_error = send_markdown_email(req.email, filename, final_md)
            if not email_sent:
                return ProcessVideoResponse(
                    status="error",
                    error=f"Internal review copy saved, but email delivery failed: {email_error}",
                    code="EMAIL_DELIVERY_FAILED",
                    mode=mode,
                    filename=filename,
                    preview=final_md[:1500] + ("..." if len(final_md) > 1500 else ""),
                    creator=creator,
                    email_sent=False,
                )

            message = f"Sent to {req.email.strip()}."
            preview = final_md[:1500] + ("..." if len(final_md) > 1500 else "")
            log.info(f"=== done public: {review_filename} ({creator} / {category} / {importance}) ===")
            return ProcessVideoResponse(
                status="success",
                filename=filename,
                drive_url=None,
                preview=preview,
                message=message,
                creator=creator,
                mode=mode,
                email_sent=True,
            )

        drive_url, drive_error = save_to_drive(final_md, filename)
        message = (
            f"Saved to Google Drive: {filename}"
            if drive_url
            else f"Processed successfully, but Google Drive save failed: {drive_error}"
        )
        preview = final_md[:1500] + ("..." if len(final_md) > 1500 else "")

        log.info(f"=== done private: {filename} ({creator} / {category}) ===")
        return ProcessVideoResponse(
            status="success",
            filename=filename,
            drive_url=drive_url,
            preview=preview,
            message=message,
            creator=creator,
            category=category,
            mode=mode,
            email_sent=email_sent,
            importance=importance,
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
