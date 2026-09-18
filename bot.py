# -*- coding: utf-8 -*-

import os
import re
import json
import html
import asyncio
import logging
import random
import shutil
import tempfile
import subprocess
import sys
from pathlib import Path
from urllib.parse import urljoin

import aiohttp
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
)
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# =========================================================
# SOKO TK
# TikTok Downloader - Multi Engine
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WELCOME_IMAGE = os.getenv("JPG", "").strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN غير موجود في GitHub Secrets")

# ---------------------------------------------------------
# Logging
# ---------------------------------------------------------

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("SOKO-TK")

# ---------------------------------------------------------
# Settings
# ---------------------------------------------------------

DOWNLOAD_TIMEOUT = 180
MAX_VIDEO_SIZE = 49 * 1024 * 1024
MAX_AUDIO_SIZE = 49 * 1024 * 1024
MAX_IMAGE_SIZE = 15 * 1024 * 1024

TEMP_ROOT = Path(tempfile.gettempdir()) / "soko_tk"
TEMP_ROOT.mkdir(parents=True, exist_ok=True)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",

    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",

    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 "
    "Mobile/15E148 Safari/604.1",

    "Mozilla/5.0 (Linux; Android 15; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/138.0.0.0 Mobile Safari/537.36",
]

TIKTOK_HOSTS = {
    "tiktok.com",
    "www.tiktok.com",
    "m.tiktok.com",
    "vm.tiktok.com",
    "vt.tiktok.com",
    "tiktokv.com",
    "www.tiktokv.com",
}

# =========================================================
# General helpers
# =========================================================

def random_ua():
    return random.choice(USER_AGENTS)


def clean_text(value):
    if value is None:
        return ""

    if isinstance(value, (dict, list)):
        return ""

    value = html.unescape(str(value))
    value = re.sub(r"<[^>]+>", "", value)
    return value.strip()


def safe_filename(name, fallback="soko_tk"):
    name = clean_text(name)

    if not name:
        name = fallback

    name = re.sub(r'[\\/:*?"<>|]+', "_", name)
    name = re.sub(r"\s+", " ", name).strip()

    return name[:100]


def extract_tiktok_url(text):
    if not text:
        return None

    pattern = re.compile(
        r"https?://[^\s<>\"']+",
        re.IGNORECASE,
    )

    candidates = pattern.findall(text)

    for url in candidates:
        url = url.rstrip(".,!?)]}>")

        try:
            from urllib.parse import urlparse

            host = urlparse(url).hostname

            if not host:
                continue

            host = host.lower()

            if host in TIKTOK_HOSTS or host.endswith(".tiktok.com"):
                return url

        except Exception:
            continue

    return None


def is_probably_media_url(url):
    if not isinstance(url, str):
        return False

    u = url.lower()

    return (
        u.startswith("http://")
        or u.startswith("https://")
    )


def classify_media_url(url):
    if not is_probably_media_url(url):
        return None

    u = url.lower()

    if any(x in u for x in [
        ".mp4",
        ".mov",
        ".webm",
        ".m3u8",
        "video",
        "videoplay",
        "playwm",
        "play/",
    ]):
        return "video"

    if any(x in u for x in [
        ".mp3",
        ".m4a",
        ".aac",
        ".wav",
        ".ogg",
        "audio",
        "music",
    ]):
        return "audio"

    if any(x in u for x in [
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".avif",
        "image",
        "photo",
        "cover",
    ]):
        return "image"

    return None


# =========================================================
# HTTP
# =========================================================

def build_headers(referer=None):
    headers = {
        "User-Agent": random_ua(),
        "Accept": "*/*",
        "Accept-Language": "ar-IQ,ar;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
    }

    if referer:
        headers["Referer"] = referer

    return headers


async def http_get_bytes(
    session,
    url,
    headers=None,
    timeout=DOWNLOAD_TIMEOUT,
):
    try:
        request_headers = build_headers()

        if headers:
            request_headers.update(headers)

        timeout_obj = aiohttp.ClientTimeout(
            total=timeout,
            connect=30,
            sock_read=timeout,
        )

        async with session.get(
            url,
            headers=request_headers,
            timeout=timeout_obj,
            allow_redirects=True,
        ) as response:

            data = await response.read()

            return {
                "ok": response.status < 400,
                "status": response.status,
                "url": str(response.url),
                "content_type": response.headers.get(
                    "Content-Type",
                    "",
                ),
                "content_length": response.headers.get(
                    "Content-Length",
                    "",
                ),
                "data": data,
            }

    except Exception as e:
        logger.warning("HTTP GET failed: %s", e)
        return None


async def http_get_text(
    session,
    url,
    headers=None,
    timeout=45,
):
    result = await http_get_bytes(
        session,
        url,
        headers=headers,
        timeout=timeout,
    )

    if not result:
        return None

    try:
        result["text"] = result["data"].decode(
            "utf-8",
            errors="ignore",
        )
    except Exception:
        result["text"] = ""

    return result


# =========================================================
# TikTok URL resolving
# =========================================================

async def resolve_tiktok_url(session, url):
    try:
        timeout_obj = aiohttp.ClientTimeout(
            total=40,
            connect=15,
        )

        async with session.get(
            url,
            headers=build_headers(),
            allow_redirects=True,
            timeout=timeout_obj,
        ) as response:

            final_url = str(response.url)

            if final_url.startswith("http"):
                return final_url

    except Exception as e:
        logger.warning("TikTok redirect failed: %s", e)

    return url


# =========================================================
# oEmbed
# =========================================================

async def get_oembed(session, url):
    endpoint = "https://www.tiktok.com/oembed"

    try:
        result = await http_get_text(
            session,
            endpoint,
            headers={
                "Accept": "application/json",
                "Referer": "https://www.tiktok.com/",
            },
            timeout=30,
        )

        # oEmbed endpoint needs ?url=
        if result:
            pass

        timeout_obj = aiohttp.ClientTimeout(
            total=30,
            connect=15,
        )

        async with session.get(
            endpoint,
            params={"url": url},
            headers=build_headers(
                "https://www.tiktok.com/"
            ),
            timeout=timeout_obj,
        ) as response:

            if response.status >= 400:
                return {}

            return await response.json(
                content_type=None
            )

    except Exception as e:
        logger.warning("oEmbed failed: %s", e)
        return {}


# =========================================================
# Recursive JSON URL extraction
# =========================================================

def recursive_collect(obj, found=None):
    if found is None:
        found = []

    if isinstance(obj, dict):
        for key, value in obj.items():
            recursive_collect(value, found)

    elif isinstance(obj, list):
        for value in obj:
            recursive_collect(value, found)

    elif isinstance(obj, str):
        value = obj.strip()

        if (
            value.startswith("http://")
            or value.startswith("https://")
        ):
            found.append(value)

        # URLs embedded in strings
        for match in re.findall(
            r'https?://[^\s"\'<>]+',
            value,
            flags=re.I,
        ):
            found.append(
                html.unescape(match).rstrip(
                    ".,;)]}>"
                )
            )

    return found


def unique_urls(urls):
    result = []
    seen = set()

    for url in urls:
        if not isinstance(url, str):
            continue

        url = html.unescape(url).strip()

        if not url:
            continue

        if url in seen:
            continue

        seen.add(url)
        result.append(url)

    return result


# =========================================================
# Metadata
# =========================================================

def recursive_find_values(obj, keys):
    results = []

    if isinstance(obj, dict):
        for key, value in obj.items():

            key_l = str(key).lower()

            if key_l in keys:
                if isinstance(value, (str, int, float)):
                    results.append(value)

            results.extend(
                recursive_find_values(value, keys)
            )

    elif isinstance(obj, list):
        for item in obj:
            results.extend(
                recursive_find_values(item, keys)
            )

    return results


def first_value(obj, keys, default=""):
    values = recursive_find_values(
        obj,
        {x.lower() for x in keys},
    )

    for value in values:
        value = clean_text(value)

        if value:
            return value

    return default


def extract_metadata(oembed, payloads):
    data = {
        "author": "",
        "username": "",
        "description": "",
        "duration": "",
        "views": "",
        "likes": "",
        "comments": "",
        "shares": "",
        "thumbnail": "",
    }

    if isinstance(oembed, dict):

        data["author"] = (
            oembed.get("author_name")
            or ""
        )

        data["username"] = (
            oembed.get("author_unique_id")
            or ""
        )

        data["description"] = (
            oembed.get("title")
            or ""
        )

        data["thumbnail"] = (
            oembed.get("thumbnail_url")
            or ""
        )

    for payload in payloads:

        if not isinstance(payload, (dict, list)):
            continue

        data["author"] = (
            data["author"]
            or first_value(
                payload,
                {
                    "author",
                    "authorname",
                    "nickname",
                    "unique_id",
                    "uniqueid",
                },
            )
        )

        data["username"] = (
            data["username"]
            or first_value(
                payload,
                {
                    "unique_id",
                    "uniqueid",
                    "username",
                    "author_unique_id",
                },
            )
        )

        data["description"] = (
            data["description"]
            or first_value(
                payload,
                {
                    "description",
                    "desc",
                    "title",
                    "caption",
                },
            )
        )

        data["duration"] = (
            data["duration"]
            or first_value(
                payload,
                {
                    "duration",
                    "duration_ms",
                },
            )
        )

        data["views"] = (
            data["views"]
            or first_value(
                payload,
                {
                    "playcount",
                    "play_count",
                    "views",
                    "view_count",
                },
            )
        )

        data["likes"] = (
            data["likes"]
            or first_value(
                payload,
                {
                    "diggcount",
                    "digg_count",
                    "likes",
                    "like_count",
                },
            )
        )

        data["comments"] = (
            data["comments"]
            or first_value(
                payload,
                {
                    "commentcount",
                    "comment_count",
                    "comments",
                },
            )
        )

        data["shares"] = (
            data["shares"]
            or first_value(
                payload,
                {
                    "sharecount",
                    "share_count",
                    "shares",
                },
            )
        )

        data["thumbnail"] = (
            data["thumbnail"]
            or first_value(
                payload,
                {
                    "thumbnail",
                    "thumbnail_url",
                    "cover",
                    "origin_cover",
                    "preview",
                },
            )
        )

    # duration normalization
    try:
        if data["duration"]:
            d = float(
                str(data["duration"])
            )

            if d > 1000:
                d /= 1000

            seconds = int(d)

            data["duration"] = (
                f"{seconds // 60:02d}:"
                f"{seconds % 60:02d}"
            )
    except Exception:
        pass

    return data


# =========================================================
# SnapTik
# =========================================================

async def snaptik_extract(
    session,
    tiktok_url,
):
    payloads = []
    media_urls = []

    # -----------------------------------------------------
    # Token endpoint
    # -----------------------------------------------------

    token = None

    token_headers = {
        "Origin": "https://snaptik.app",
        "Referer": "https://snaptik.app/ar3",
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/plain, */*",
    }

    token_bodies = [
        {"url": tiktok_url},
        {"url": tiktok_url, "lang": "ar"},
        {"url": tiktok_url, "lang": "en"},
    ]

    for body in token_bodies:

        try:
            timeout_obj = aiohttp.ClientTimeout(
                total=40,
                connect=15,
            )

            async with session.post(
                "https://snaptik.app/api/token",
                json=body,
                headers=token_headers,
                timeout=timeout_obj,
            ) as response:

                text_body = await response.text(
                    errors="ignore"
                )

                try:
                    payload = json.loads(text_body)
                except Exception:
                    payload = {
                        "raw": text_body
                    }

                payloads.append(payload)

                candidates = recursive_find_values(
                    payload,
                    {
                        "token",
                        "x-token",
                        "xtoken",
                    },
                )

                if candidates:
                    token = str(
                        candidates[0]
                    ).strip()

                if token:
                    break

        except Exception as e:
            logger.warning(
                "SnapTik token error: %s",
                e,
            )

    # -----------------------------------------------------
    # Extract attempts
    # -----------------------------------------------------

    attempts = []

    if token:
        attempts.extend([
            {
                "url": tiktok_url,
                "token": token,
            },
            {
                "url": tiktok_url,
                "x-token": token,
            },
            {
                "url": tiktok_url,
                "X-Token": token,
            },
        ])

    attempts.extend([
        {"url": tiktok_url},
        {"query": tiktok_url},
    ])

    for body in attempts:

        try:
            headers = {
                "Origin": "https://snaptik.app",
                "Referer": "https://snaptik.app/ar3",
                "Content-Type": "application/json",
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/plain, */*",
            }

            if token:
                headers["X-Token"] = token

            timeout_obj = aiohttp.ClientTimeout(
                total=60,
                connect=15,
            )

            async with session.post(
                "https://snaptik.app/api/extract",
                json=body,
                headers=headers,
                timeout=timeout_obj,
            ) as response:

                raw = await response.text(
                    errors="ignore"
                )

                try:
                    payload = json.loads(raw)
                except Exception:
                    payload = {
                        "raw": raw
                    }

                payloads.append(payload)

                urls = recursive_collect(payload)

                media_urls.extend(urls)

                # Also scan raw response
                media_urls.extend(
                    re.findall(
                        r'https?://[^\s"\'<>]+',
                        raw,
                        flags=re.I,
                    )
                )

        except Exception as e:
            logger.warning(
                "SnapTik extract failed: %s",
                e,
            )

    return {
        "payloads": payloads,
        "media_urls": unique_urls(
            media_urls
        ),
    }


# =========================================================
# Direct media download
# =========================================================

def looks_like_video(data, content_type, url):
    ct = (content_type or "").lower()
    u = (url or "").lower()

    if "video/" in ct:
        return True

    if any(x in ct for x in [
        "application/octet-stream",
        "binary/octet-stream",
    ]):
        if any(x in u for x in [
            ".mp4",
            "video",
            "rapidcdn",
            "snapxcdn",
        ]):
            return True

    if data[:4] == b"ftyp":
        return True

    if len(data) > 12 and b"ftyp" in data[:64]:
        return True

    return False


def looks_like_audio(data, content_type, url):
    ct = (content_type or "").lower()
    u = (url or "").lower()

    if "audio/" in ct:
        return True

    if any(ext in u for ext in [
        ".mp3",
        ".m4a",
        ".aac",
        ".ogg",
        ".wav",
    ]):
        return True

    if data[:3] == b"ID3":
        return True

    return False


def looks_like_image(data, content_type, url):
    ct = (content_type or "").lower()
    u = (url or "").lower()

    if "image/" in ct:
        return True

    if data.startswith(b"\xff\xd8\xff"):
        return True

    if data.startswith(b"\x89PNG"):
        return True

    if data.startswith(b"RIFF") and b"WEBP" in data[:16]:
        return True

    if any(ext in u for ext in [
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
    ]):
        return True

    return False


async def download_media_to_file(
    session,
    url,
    output_dir,
    referer=None,
):
    if not is_probably_media_url(url):
        return None

    try:
        headers = build_headers(
            referer or "https://www.tiktok.com/"
        )

        # Important headers for TikTok CDN
        headers.update({
            "Accept": "*/*",
            "Range": "bytes=0-",
        })

        timeout_obj = aiohttp.ClientTimeout(
            total=DOWNLOAD_TIMEOUT,
            connect=30,
            sock_read=DOWNLOAD_TIMEOUT,
        )

        async with session.get(
            url,
            headers=headers,
            allow_redirects=True,
            timeout=timeout_obj,
        ) as response:

            status = response.status
            content_type = response.headers.get(
                "Content-Type",
                "",
            )

            if status >= 400:
                logger.warning(
                    "Media HTTP %s: %s",
                    status,
                    url[:150],
                )
                return None

            final_url = str(response.url)

            # Read incrementally to disk
            temp_path = output_dir / (
                "media_" +
                str(random.randint(100000, 999999)) +
                ".bin"
            )

            total = 0

            with open(
                temp_path,
                "wb",
            ) as file:

                async for chunk in response.content.iter_chunked(
                    1024 * 256
                ):
                    if not chunk:
                        continue

                    total += len(chunk)

                    # Prevent enormous files
                    if total > 200 * 1024 * 1024:
                        logger.warning(
                            "Media too large: %s",
                            final_url[:150],
                        )

                        file.close()

                        try:
                            temp_path.unlink()
                        except Exception:
                            pass

                        return None

                    file.write(chunk)

            if total < 1024:
                try:
                    temp_path.unlink()
                except Exception:
                    pass

                return None

            with open(
                temp_path,
                "rb",
            ) as file:
                head = file.read(512)

            media_type = None

            if looks_like_video(
                head,
                content_type,
                final_url,
            ):
                media_type = "video"

            elif looks_like_audio(
                head,
                content_type,
                final_url,
            ):
                media_type = "audio"

            elif looks_like_image(
                head,
                content_type,
                final_url,
            ):
                media_type = "image"

            else:
                # Some CDN endpoints return octet-stream.
                # Let ffprobe identify it later.
                media_type = await detect_with_ffprobe(
                    temp_path
                )

            if not media_type:
                try:
                    text_sample = head.decode(
                        "utf-8",
                        errors="ignore",
                    ).lower()

                    if (
                        "<html" in text_sample
                        or "<!doctype" in text_sample
                    ):
                        temp_path.unlink(
                            missing_ok=True
                        )
                        return None

                except Exception:
                    pass

                temp_path.unlink(
                    missing_ok=True
                )

                return None

            extension = {
                "video": ".mp4",
                "audio": ".mp3",
                "image": ".jpg",
            }.get(
                media_type,
                ".bin",
            )

            final_path = output_dir / (
                "soko_media" + extension
            )

            try:
                temp_path.rename(
                    final_path
                )
            except Exception:
                shutil.move(
                    str(temp_path),
                    str(final_path),
                )

            return {
                "path": final_path,
                "type": media_type,
                "size": total,
                "url": final_url,
                "content_type": content_type,
            }

    except Exception as e:
        logger.warning(
            "Direct media download failed: %s",
            e,
        )

        return None


# =========================================================
# FFprobe / FFmpeg
# =========================================================

async def detect_with_ffprobe(path):
    ffprobe = shutil.which("ffprobe")

    if not ffprobe:
        return None

    try:
        process = await asyncio.create_subprocess_exec(
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=format_name",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, _ = await process.communicate()

        fmt = stdout.decode(
            "utf-8",
            errors="ignore",
        ).strip().lower()

        if not fmt:
            return None

        if any(x in fmt for x in [
            "mp4",
            "mov",
            "webm",
            "matroska",
            "mpegts",
        ]):
            return "video"

        if any(x in fmt for x in [
            "mp3",
            "aac",
            "ogg",
            "wav",
            "flac",
            "m4a",
        ]):
            return "audio"

    except Exception:
        pass

    return None


# =========================================================
# yt-dlp fallback
# =========================================================

async def ensure_ytdlp():
    try:
        import yt_dlp

        return True
    except ImportError:
        pass

    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "pip",
            "install",
            "-U",
            "--pre",
            "yt-dlp",
            "curl-cffi",
            "yt-dlp-ejs",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        await process.communicate()

        import importlib

        importlib.invalidate_caches()

        return True

    except Exception as e:
        logger.warning(
            "Could not install yt-dlp: %s",
            e,
        )

        return False


async def ytdlp_download(
    url,
    output_dir,
):
    if not await ensure_ytdlp():
        return None

    output_template = str(
        output_dir /
        "ytdlp_%(id)s.%(ext)s"
    )

    command = [
        sys.executable,
        "-m",
        "yt_dlp",

        "--no-playlist",
        "--no-warnings",
        "--ignore-errors",

        "--retries",
        "3",

        "--fragment-retries",
        "3",

        "--concurrent-fragments",
        "4",

        "--http-chunk-size",
        "10M",

        "-f",
        "bv*+ba/b",

        "--merge-output-format",
        "mp4",

        "--restrict-filenames",

        "-o",
        output_template,

        url,
    ]

    try:
        logger.info(
            "Starting yt-dlp fallback..."
        )

        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()

        out = (
            stdout.decode(
                "utf-8",
                errors="ignore",
            )
            + "\n"
            + stderr.decode(
                "utf-8",
                errors="ignore",
            )
        )

        logger.info(
            "yt-dlp exit=%s",
            process.returncode,
        )

        if process.returncode != 0:
            logger.warning(
                "yt-dlp failed: %s",
                out[-2000:],
            )

            return None

        files = list(
            output_dir.glob(
                "ytdlp_*"
            )
        )

        files = [
            x for x in files
            if x.is_file()
            and x.stat().st_size > 1024
        ]

        if not files:
            return None

        files.sort(
            key=lambda x: x.stat().st_mtime,
            reverse=True,
        )

        selected = files[0]

        detected = await detect_with_ffprobe(
            selected
        )

        if not detected:
            detected = "video"

        return {
            "path": selected,
            "type": detected,
            "size": selected.stat().st_size,
            "url": url,
            "content_type": "",
        }

    except Exception as e:
        logger.warning(
            "yt-dlp exception: %s",
            e,
        )

        return None


# =========================================================
# Audio extraction from video
# =========================================================

async def extract_audio_from_video(
    video_path,
    output_dir,
):
    ffmpeg = shutil.which("ffmpeg")

    if not ffmpeg:
        return None

    output = output_dir / "soko_audio.mp3"

    command = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),

        "-vn",

        "-acodec",
        "libmp3lame",

        "-b:a",
        "128k",

        str(output),
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        _, stderr = await process.communicate()

        if (
            process.returncode == 0
            and output.exists()
            and output.stat().st_size > 1024
        ):
            return output

        logger.warning(
            "Audio extraction failed: %s",
            stderr.decode(
                "utf-8",
                errors="ignore",
            )[-1000:],
        )

    except Exception as e:
        logger.warning(
            "ffmpeg audio error: %s",
            e,
        )

    return None


# =========================================================
# Find best media
# =========================================================

async def download_best_media(
    session,
    tiktok_url,
    snap_result,
    workdir,
):
    media_urls = snap_result.get(
        "media_urls",
        [],
    )

    # Prioritize video URLs
    videos = []
    audios = []
    images = []

    for url in media_urls:

        kind = classify_media_url(url)

        if kind == "video":
            videos.append(url)

        elif kind == "audio":
            audios.append(url)

        elif kind == "image":
            images.append(url)

    # Unknown CDN links are also worth trying
    unknown = [
        u for u in media_urls
        if not classify_media_url(u)
    ]

    videos.extend(unknown)

    # -----------------------------------------------------
    # Direct download - multiple URLs
    # -----------------------------------------------------

    video_file = None

    tried = set()

    for url in videos:

        if url in tried:
            continue

        tried.add(url)

        logger.info(
            "Trying direct video: %s",
            url[:180],
        )

        result = await download_media_to_file(
            session,
            url,
            workdir,
            referer="https://www.tiktok.com/",
        )

        if result and result["type"] == "video":
            video_file = result
            break

    # -----------------------------------------------------
    # Audio direct
    # -----------------------------------------------------

    audio_file = None

    for url in audios:

        if url in tried:
            continue

        tried.add(url)

        result = await download_media_to_file(
            session,
            url,
            workdir,
            referer="https://www.tiktok.com/",
        )

        if result and result["type"] == "audio":
            audio_file = result
            break

    # -----------------------------------------------------
    # yt-dlp fallback
    # -----------------------------------------------------

    if not video_file:

        video_file = await ytdlp_download(
            tiktok_url,
            workdir,
        )

    # -----------------------------------------------------
    # Extract audio from video
    # -----------------------------------------------------

    if (
        video_file
        and not audio_file
        and video_file["type"] == "video"
    ):
        extracted = (
            await extract_audio_from_video(
                video_file["path"],
                workdir,
            )
        )

        if extracted:
            audio_file = {
                "path": extracted,
                "type": "audio",
                "size": extracted.stat().st_size,
                "url": "",
                "content_type": "audio/mpeg",
            }

    # -----------------------------------------------------
    # Images / Photo mode
    # -----------------------------------------------------

    image_files = []

    for url in images[:20]:

        result = await download_media_to_file(
            session,
            url,
            workdir,
            referer="https://www.tiktok.com/",
        )

        if (
            result
            and result["type"] == "image"
        ):
            image_files.append(
                result
            )

    return {
        "video": video_file,
        "audio": audio_file,
        "images": image_files,
    }


# =========================================================
# Formatting
# =========================================================

def format_number(value):
    if value is None:
        return "—"

    value = clean_text(value)

    if not value:
        return "—"

    try:
        n = float(
            str(value).replace(",", "")
        )

        if n >= 1_000_000_000:
            return f"{n / 1_000_000_000:.1f}B"

        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"

        if n >= 1_000:
            return f"{n / 1_000:.1f}K"

        if n.is_integer():
            return str(int(n))

    except Exception:
        pass

    return value


def build_result_text(metadata):
    author = (
        metadata.get("author")
        or "غير معروف"
    )

    username = (
        metadata.get("username")
        or "غير معروف"
    )

    if username and not username.startswith("@"):
        username = "@" + username

    description = (
        metadata.get("description")
        or "لا يوجد وصف"
    )

    duration = (
        metadata.get("duration")
        or "—"
    )

    return (
        "╔════════════════════╗\n"
        "      🌌 SOKO TK RESULT\n"
        "╚════════════════════╝\n\n"

        f"👤 صاحب المنشور: {author}\n"
        f"🔖 المستخدم: {username}\n"
        f"⏱️ المدة: {duration}\n"
        f"👁️ المشاهدات: "
        f"{format_number(metadata.get('views'))}\n"
        f"❤️ الإعجابات: "
        f"{format_number(metadata.get('likes'))}\n"
        f"💬 التعليقات: "
        f"{format_number(metadata.get('comments'))}\n"
        f"🔁 المشاركات: "
        f"{format_number(metadata.get('shares'))}\n\n"

        f"📝 الوصف:\n"
        f"{description[:1000]}\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        "🚀 SOKO TK"
    )


# =========================================================
# Telegram sending
# =========================================================

async def send_video(
    bot,
    chat_id,
    file_path,
):
    size = file_path.stat().st_size

    if size > MAX_VIDEO_SIZE:
        return False, "large"

    try:
        await bot.send_chat_action(
            chat_id=chat_id,
            action=ChatAction.UPLOAD_VIDEO,
        )

        with open(
            file_path,
            "rb",
        ) as file:

            await bot.send_video(
                chat_id=chat_id,
                video=InputFile(file),
                supports_streaming=True,
                read_timeout=120,
                write_timeout=120,
                connect_timeout=30,
                pool_timeout=30,
            )

        return True, "ok"

    except Exception as e:
        logger.warning(
            "Telegram video send failed: %s",
            e,
        )

        return False, str(e)


async def send_audio(
    bot,
    chat_id,
    file_path,
):
    size = file_path.stat().st_size

    if size > MAX_AUDIO_SIZE:
        return False, "large"

    try:
        await bot.send_chat_action(
            chat_id=chat_id,
            action=ChatAction.UPLOAD_DOCUMENT,
        )

        with open(
            file_path,
            "rb",
        ) as file:

            await bot.send_audio(
                chat_id=chat_id,
                audio=InputFile(file),
                read_timeout=120,
                write_timeout=120,
                connect_timeout=30,
                pool_timeout=30,
            )

        return True, "ok"

    except Exception as e:
        logger.warning(
            "Telegram audio send failed: %s",
            e,
        )

        return False, str(e)


async def send_images(
    bot,
    chat_id,
    images,
):
    sent = 0

    for image in images[:20]:

        path = image["path"]

        if (
            not path.exists()
            or path.stat().st_size > MAX_IMAGE_SIZE
        ):
            continue

        try:
            with open(
                path,
                "rb",
            ) as file:

                await bot.send_photo(
                    chat_id=chat_id,
                    photo=InputFile(file),
                    read_timeout=60,
                    write_timeout=60,
                )

            sent += 1

        except Exception as e:
            logger.warning(
                "Image send failed: %s",
                e,
            )

    return sent


# =========================================================
# Start
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    name = (
        user.full_name
        if user
        else "غير معروف"
    )

    user_id = (
        user.id
        if user
        else "—"
    )

    text = (
        "𐚁‌⇄❮𝗦𝗢𝗞𝗢❯\n"
        "╔════════════════════╗\n"
        "      🌌 DOWNLOAD VIDEO TIKTOK\n"
        "╚════════════════════╝\n\n"

        "𝗦𝗢𝗞𝗢・TK 🎧 ❮❯\n"
        "🎵 تنزيل صوت - فيديو - ستوري - تيكتوك\n"
        "🖼️ صور ومعلومات كاملة\n\n"

        f"👤 الاسم: {name}\n"
        f"🆔 ID: {user_id}\n\n"

        "📥 أرسل رابط TikTok الآن."
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "👨‍💻 Developer",
                url="https://t.me/FDF01",
            )
        ]
    ])

    if WELCOME_IMAGE:

        try:
            await update.message.reply_photo(
                photo=WELCOME_IMAGE,
                caption=text,
                reply_markup=keyboard,
            )
            return

        except Exception as e:
            logger.warning(
                "Welcome image failed: %s",
                e,
            )

    await update.message.reply_text(
        text,
        reply_markup=keyboard,
    )


# =========================================================
# Help
# =========================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "🎧 SOKO TK\n\n"
        "أرسل رابط منشور TikTok فقط.\n\n"
        "يدعم:\n"
        "• tiktok.com\n"
        "• www.tiktok.com\n"
        "• vt.tiktok.com\n"
        "• vm.tiktok.com\n"
        "• m.tiktok.com\n"
        "• روابط المشاركة\n\n"
        "📥 يحاول تنزيل الفيديو مباشرة، "
        "ثم يستخدم محركًا احتياطيًا إذا لزم الأمر."
    )


# =========================================================
# TikTok handler
# =========================================================

async def handle_tiktok(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    message = update.effective_message

    if not message or not message.text:
        return

    tiktok_url = extract_tiktok_url(
        message.text
    )

    if not tiktok_url:

        await message.reply_text(
            "❌ لم أجد رابط TikTok صالحًا.\n\n"
            "أرسل رابط المنشور مثل:\n"
            "https://www.tiktok.com/...\n"
            "https://vt.tiktok.com/..."
        )

        return

    processing = await message.reply_text(
        "⏳ جاري تحليل رابط TikTok...\n"
        "🔎 استخراج بيانات المنشور والوسائط..."
    )

    workdir = Path(
        tempfile.mkdtemp(
            prefix="soko_",
            dir=str(TEMP_ROOT),
        )
    )

    try:

        timeout_obj = aiohttp.ClientTimeout(
            total=DOWNLOAD_TIMEOUT,
            connect=30,
            sock_read=DOWNLOAD_TIMEOUT,
        )

        connector = aiohttp.TCPConnector(
            limit=20,
            ttl_dns_cache=300,
            ssl=False,
        )

        async with aiohttp.ClientSession(
            timeout=timeout_obj,
            connector=connector,
        ) as session:

            # -------------------------------------------------
            # Resolve short URL
            # -------------------------------------------------

            await processing.edit_text(
                "⏳ جاري الوصول إلى TikTok...\n"
                "🔗 معالجة الرابط..."
            )

            resolved_url = await resolve_tiktok_url(
                session,
                tiktok_url,
            )

            logger.info(
                "Original URL: %s",
                tiktok_url,
            )

            logger.info(
                "Resolved URL: %s",
                resolved_url,
            )

            # -------------------------------------------------
            # oEmbed
            # -------------------------------------------------

            oembed = await get_oembed(
                session,
                resolved_url,
            )

            # -------------------------------------------------
            # SnapTik
            # -------------------------------------------------

            await processing.edit_text(
                "🔎 تم الوصول إلى المنشور.\n"
                "⚡ جاري البحث عن رابط الوسائط المباشر..."
            )

            snap = await snaptik_extract(
                session,
                resolved_url,
            )

            payloads = snap.get(
                "payloads",
                [],
            )

            metadata = extract_metadata(
                oembed,
                payloads,
            )

            # -------------------------------------------------
            # Download media
            # -------------------------------------------------

            await processing.edit_text(
                "📦 تم استخراج معلومات المنشور.\n"
                "⬇️ جاري تنزيل الوسائط إلى السيرفر...\n"
                "🔄 سيتم تجربة أكثر من مسار عند الحاجة."
            )

            media = await download_best_media(
                session,
                resolved_url,
                snap,
                workdir,
            )

            video = media.get("video")
            audio = media.get("audio")
            images = media.get("images") or []

            # -------------------------------------------------
            # Result metadata
            # -------------------------------------------------

            result_text = build_result_text(
                metadata
            )

            # -------------------------------------------------
            # Video
            # -------------------------------------------------

            if video:

                ok, reason = await send_video(
                    context.bot,
                    message.chat_id,
                    video["path"],
                )

                if not ok:

                    if reason == "large":
                        await message.reply_text(
                            result_text
                            + "\n\n"
                            "⚠️ الفيديو تم تنزيله بنجاح، "
                            "لكن حجمه أكبر من الحد الذي "
                            "يمكن للبوت إرساله مباشرة."
                        )

                    else:
                        await message.reply_text(
                            result_text
                            + "\n\n"
                            "⚠️ تم تنزيل الفيديو على السيرفر، "
                            "لكن Telegram رفض إرسال الملف."
                        )

                else:
                    await message.reply_text(
                        result_text
                    )

            # -------------------------------------------------
            # Images / Photo mode
            # -------------------------------------------------

            elif images:

                await message.reply_text(
                    result_text
                    + "\n\n"
                    f"🖼️ وضع الصور: {len(images)} صورة"
                )

                await send_images(
                    context.bot,
                    message.chat_id,
                    images,
                )

            # -------------------------------------------------
            # No video but audio
            # -------------------------------------------------

            elif audio:

                await message.reply_text(
                    result_text
                    + "\n\n"
                    "🎵 تم العثور على الصوت فقط."
                )

            # -------------------------------------------------
            # Completely failed
            # -------------------------------------------------

            else:

                await message.reply_text(
                    result_text
                    + "\n\n"
                    "⚠️ حصلت على معلومات المنشور، "
                    "لكن لم أستطع تنزيل ملف الوسائط.\n\n"
                    "🔄 تمت تجربة رابط الوسائط المباشر "
                    "ومحرك التنزيل الاحتياطي.\n\n"
                    "قد يكون المنشور محميًا أو أن TikTok "
                    "غيّر طريقة تسليم الملف لهذا المنشور."
                )

            # -------------------------------------------------
            # Audio
            # -------------------------------------------------

            if audio:

                # Don't send audio if too large
                if (
                    audio["path"].exists()
                    and audio["path"].stat().st_size
                    <= MAX_AUDIO_SIZE
                ):
                    await send_audio(
                        context.bot,
                        message.chat_id,
                        audio["path"],
                    )

        try:
            await processing.delete()
        except Exception:
            pass

    except Exception as e:

        logger.exception(
            "Main TikTok handler failed"
        )

        try:
            await processing.edit_text(
                "❌ حدث خطأ أثناء معالجة الرابط.\n\n"
                "🔄 جرّب إرسال الرابط مرة أخرى."
            )
        except Exception:
            pass

    finally:

        # -----------------------------------------------------
        # Cleanup
        # -----------------------------------------------------

        try:
            shutil.rmtree(
                workdir,
                ignore_errors=True,
            )
        except Exception:
            pass


# =========================================================
# Buttons
# =========================================================

async def button_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    if query:
        await query.answer()


# =========================================================
# Main
# =========================================================

def main():

    logger.info(
        "Starting SOKO TK..."
    )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        CommandHandler(
            "help",
            help_command,
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            button_callback
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            handle_tiktok,
        )
    )

    logger.info(
        "SOKO TK is running."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
