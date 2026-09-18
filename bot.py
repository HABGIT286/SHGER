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
from urllib.parse import (
    urlparse,
    urljoin,
    quote,
)

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
# SOKO TK X10
# MULTI ENGINE TIKTOK DOWNLOADER
# =========================================================


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WELCOME_IMAGE = os.getenv("JPG", "").strip()


if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN غير موجود داخل GitHub Secrets"
    )


# =========================================================
# CONFIG
# =========================================================


MAX_DOWNLOAD_MB = 500
MAX_DOWNLOAD_BYTES = MAX_DOWNLOAD_MB * 1024 * 1024

TELEGRAM_SAFE_MB = 49
TELEGRAM_SAFE_BYTES = TELEGRAM_SAFE_MB * 1024 * 1024

MAX_IMAGES = 35

REQUEST_TIMEOUT = 90
DOWNLOAD_TIMEOUT = 300

TEMP_ROOT = Path(
    tempfile.gettempdir()
) / "soko_tk_x10"

TEMP_ROOT.mkdir(
    parents=True,
    exist_ok=True,
)


# =========================================================
# LOGGING
# =========================================================


logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s | "
        "%(levelname)s | "
        "%(name)s | "
        "%(message)s"
    ),
)

logger = logging.getLogger(
    "SOKO-TK-X10"
)


# =========================================================
# USER AGENTS
# =========================================================


USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/139.0.0.0 Safari/537.36"
    ),

    (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/139.0.0.0 Safari/537.36"
    ),

    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/18.5 Safari/605.1.15"
    ),

    (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/18.5 Mobile/15E148 Safari/604.1"
    ),

    (
        "Mozilla/5.0 (Linux; Android 15; Pixel 9) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/139.0.0.0 Mobile Safari/537.36"
    ),
]


def random_ua():
    return random.choice(
        USER_AGENTS
    )


# =========================================================
# TIKTOK HOSTS
# =========================================================


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
# BASIC HELPERS
# =========================================================


def clean_text(value):
    if value is None:
        return ""

    if isinstance(
        value,
        (dict, list),
    ):
        return ""

    value = html.unescape(
        str(value)
    )

    value = re.sub(
        r"<[^>]+>",
        "",
        value,
    )

    return value.strip()


def safe_filename(
    value,
    default="soko_tk",
):
    value = clean_text(
        value
    )

    if not value:
        value = default

    value = re.sub(
        r'[\\/:*?"<>|]+',
        "_",
        value,
    )

    value = re.sub(
        r"\s+",
        " ",
        value,
    ).strip()

    return value[:100]


def unique(items):
    output = []
    seen = set()

    for item in items:
        if not item:
            continue

        item = str(item).strip()

        if item in seen:
            continue

        seen.add(item)
        output.append(item)

    return output


# =========================================================
# URL EXTRACTION
# =========================================================


def extract_urls(text):
    if not text:
        return []

    urls = re.findall(
        r"https?://[^\s<>\"']+",
        text,
        flags=re.I,
    )

    return [
        x.rstrip(
            ".,!?)]}>"
        )
        for x in urls
    ]


def is_tiktok_url(url):
    try:
        host = (
            urlparse(url)
            .hostname
            or ""
        ).lower()

        return (
            host in TIKTOK_HOSTS
            or host.endswith(
                ".tiktok.com"
            )
        )

    except Exception:
        return False


def extract_tiktok_url(text):
    for url in extract_urls(text):

        if is_tiktok_url(url):
            return url

    return None


def normalize_url(url):
    """
    Remove tracking parameters while preserving
    TikTok identity parameters when useful.
    """

    try:
        parsed = urlparse(
            url
        )

        return (
            parsed.scheme
            + "://"
            + parsed.netloc
            + parsed.path
        )

    except Exception:
        return url


# =========================================================
# HTTP HEADERS
# =========================================================


def browser_headers(
    referer=None,
):
    headers = {
        "User-Agent": random_ua(),
        "Accept": (
            "text/html,"
            "application/xhtml+xml,"
            "application/xml;q=0.9,"
            "image/avif,"
            "image/webp,"
            "*/*;q=0.8"
        ),
        "Accept-Language": (
            "ar-IQ,ar;q=0.9,"
            "en-US;q=0.8,en;q=0.7"
        ),
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }

    if referer:
        headers[
            "Referer"
        ] = referer

    return headers


def api_headers(
    referer=None,
):
    headers = browser_headers(
        referer
    )

    headers.update({
        "Accept": "*/*",
        "X-Requested-With":
            "XMLHttpRequest",
    })

    return headers


# =========================================================
# HTTP GET
# =========================================================


async def get_text(
    session,
    url,
    *,
    headers=None,
    params=None,
    timeout=REQUEST_TIMEOUT,
):
    try:

        h = browser_headers()

        if headers:
            h.update(
                headers
            )

        timeout_obj = (
            aiohttp.ClientTimeout(
                total=timeout,
                connect=30,
                sock_read=timeout,
            )
        )

        async with session.get(
            url,
            headers=h,
            params=params,
            allow_redirects=True,
            timeout=timeout_obj,
        ) as response:

            data = await response.read()

            return {
                "status":
                    response.status,

                "url":
                    str(response.url),

                "content_type":
                    response.headers.get(
                        "Content-Type",
                        "",
                    ),

                "headers":
                    dict(response.headers),

                "data":
                    data,

                "text":
                    data.decode(
                        "utf-8",
                        errors="ignore",
                    ),
            }

    except Exception as e:

        logger.warning(
            "GET failed %s | %s",
            url[:120],
            e,
        )

        return None


async def post_text(
    session,
    url,
    *,
    data=None,
    json_data=None,
    headers=None,
    timeout=REQUEST_TIMEOUT,
):
    try:

        h = browser_headers()

        if headers:
            h.update(
                headers
            )

        timeout_obj = (
            aiohttp.ClientTimeout(
                total=timeout,
                connect=30,
                sock_read=timeout,
            )
        )

        async with session.post(
            url,
            data=data,
            json=json_data,
            headers=h,
            allow_redirects=True,
            timeout=timeout_obj,
        ) as response:

            raw = await response.read()

            return {
                "status":
                    response.status,

                "url":
                    str(response.url),

                "content_type":
                    response.headers.get(
                        "Content-Type",
                        "",
                    ),

                "headers":
                    dict(response.headers),

                "data":
                    raw,

                "text":
                    raw.decode(
                        "utf-8",
                        errors="ignore",
                    ),
            }

    except Exception as e:

        logger.warning(
            "POST failed %s | %s",
            url[:120],
            e,
        )

        return None


# =========================================================
# REDIRECT RESOLUTION
# =========================================================


async def resolve_tiktok(
    session,
    url,
):
    try:

        result = await get_text(
            session,
            url,
            timeout=40,
        )

        if result:

            final_url = result[
                "url"
            ]

            if is_tiktok_url(
                final_url
            ):
                return final_url

    except Exception:
        pass

    return url


# =========================================================
# GENERIC URL SCANNER
# =========================================================


def extract_http_urls(
    text,
):
    if not text:
        return []

    found = re.findall(
        r'https?://[^\s"\'<>\\]+',
        text,
        flags=re.I,
    )

    cleaned = []

    for url in found:

        url = html.unescape(
            url
        )

        url = url.replace(
            "\\/",
            "/",
        )

        url = url.rstrip(
            ".,;)]}>"
        )

        cleaned.append(
            url
        )

    return unique(
        cleaned
    )


def classify_url(url):
    u = url.lower()

    if any(
        x in u
        for x in [
            ".mp4",
            ".mov",
            ".mkv",
            ".webm",
            "video",
            "playwm",
            "play/",
            "videoplay",
        ]
    ):
        return "video"

    if any(
        x in u
        for x in [
            ".mp3",
            ".m4a",
            ".aac",
            ".wav",
            ".ogg",
            "audio",
            "music",
        ]
    ):
        return "audio"

    if any(
        x in u
        for x in [
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".avif",
            "image",
            "photo",
        ]
    ):
        return "image"

    return "unknown"


# =========================================================
# RECURSIVE JSON
# =========================================================


def recursive_urls(
    obj,
):
    found = []

    if isinstance(
        obj,
        dict,
    ):

        for value in obj.values():

            found.extend(
                recursive_urls(
                    value
                )
            )

    elif isinstance(
        obj,
        list,
    ):

        for value in obj:

            found.extend(
                recursive_urls(
                    value
                )
            )

    elif isinstance(
        obj,
        str,
    ):

        found.extend(
            extract_http_urls(
                obj
            )
        )

    return unique(
        found
    )


def recursive_values(
    obj,
    keys,
):
    found = []

    keys = {
        x.lower()
        for x in keys
    }

    if isinstance(
        obj,
        dict,
    ):

        for key, value in obj.items():

            if str(key).lower() in keys:

                if isinstance(
                    value,
                    (str, int, float),
                ):
                    found.append(
                        value
                    )

            found.extend(
                recursive_values(
                    value,
                    keys,
                )
            )

    elif isinstance(
        obj,
        list,
    ):

        for value in obj:

            found.extend(
                recursive_values(
                    value,
                    keys,
                )
            )

    return found


def first_value(
    obj,
    keys,
):
    values = recursive_values(
        obj,
        keys,
    )

    for value in values:

        value = clean_text(
            value
        )

        if value:
            return value

    return ""


# =========================================================
# ENGINE 1
# YT-DLP
# =========================================================


async def install_ytdlp():
    """
    Always update yt-dlp.
    TikTok changes frequently.
    """

    try:

        process = (
            await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "pip",
                "install",
                "-U",
                "--pre",
                "yt-dlp",
                "curl-cffi",
                "yt-dlp-ejs",
                stdout=
                    asyncio.subprocess.PIPE,
                stderr=
                    asyncio.subprocess.PIPE,
            )
        )

        stdout, stderr = (
            await process.communicate()
        )

        logger.info(
            "yt-dlp install exit=%s",
            process.returncode,
        )

        if process.returncode != 0:

            logger.warning(
                stderr.decode(
                    "utf-8",
                    errors="ignore",
                )[-1500:]
            )

            return False

        return True

    except Exception as e:

        logger.warning(
            "yt-dlp install failed: %s",
            e,
        )

        return False


async def engine_ytdlp(
    url,
    workdir,
):
    logger.info(
        "[ENGINE 1] yt-dlp"
    )

    await install_ytdlp()

    output = (
        workdir
        / "ytdlp_%(id)s.%(ext)s"
    )

    command = [
        sys.executable,
        "-m",
        "yt_dlp",

        "--no-playlist",

        "--retries",
        "5",

        "--fragment-retries",
        "5",

        "--retry-sleep",
        "1",

        "--concurrent-fragments",
        "4",

        "--socket-timeout",
        "60",

        "--extractor-retries",
        "3",

        "--no-warnings",

        "--impersonate",
        "chrome",

        "-f",
        (
            "bv*[ext=mp4]+ba/"
            "b[ext=mp4]/"
            "bv*+ba/b"
        ),

        "--merge-output-format",
        "mp4",

        "-o",
        str(output),

        url,
    ]

    try:

        process = (
            await asyncio.create_subprocess_exec(
                *command,
                stdout=
                    asyncio.subprocess.PIPE,
                stderr=
                    asyncio.subprocess.PIPE,
            )
        )

        stdout, stderr = (
            await process.communicate()
        )

        logs = (
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
            "[ENGINE 1] exit=%s",
            process.returncode,
        )

        files = list(
            workdir.glob(
                "ytdlp_*"
            )
        )

        files = [
            x for x in files
            if x.is_file()
            and x.stat().st_size > 1024
        ]

        if not files:
            logger.warning(
                "[ENGINE 1] no file\n%s",
                logs[-2500:],
            )

            return []

        files.sort(
            key=lambda x:
                x.stat().st_mtime,
            reverse=True,
        )

        return [
            {
                "path": files[0],
                "type": "video",
                "engine": "yt-dlp",
            }
        ]

    except Exception as e:

        logger.warning(
            "[ENGINE 1] failed: %s",
            e,
        )

        return []


# =========================================================
# ENGINE 2
# TIKTOK WEBPAGE
# =========================================================


async def engine_tiktok_web(
    session,
    url,
):
    logger.info(
        "[ENGINE 2] TikTok webpage"
    )

    result = await get_text(
        session,
        url,
        headers={
            "Accept":
                "text/html,application/xhtml+xml",
        },
        timeout=60,
    )

    if not result:
        return {
            "urls": [],
            "payloads": [],
        }

    text = result[
        "text"
    ]

    urls = extract_http_urls(
        text
    )

    payloads = []

    # -----------------------------------------------------
    # JSON script blocks
    # -----------------------------------------------------

    patterns = [
        r'<script[^>]+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
        r'<script[^>]+id="SIGI_STATE"[^>]*>(.*?)</script>',
        r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    ]

    for pattern in patterns:

        for match in re.findall(
            pattern,
            text,
            flags=re.I | re.S,
        ):

            raw = html.unescape(
                match
            ).strip()

            try:

                payload = json.loads(
                    raw
                )

                payloads.append(
                    payload
                )

                urls.extend(
                    recursive_urls(
                        payload
                    )
                )

            except Exception:
                pass

    # -----------------------------------------------------
    # Direct JSON-ish video fields
    # -----------------------------------------------------

    for pattern in [
        r'"playAddr"\s*:\s*"([^"]+)"',
        r'"downloadAddr"\s*:\s*"([^"]+)"',
        r'"play_addr"\s*:\s*"([^"]+)"',
        r'"download_addr"\s*:\s*"([^"]+)"',
        r'"url_list"\s*:\s*\[(.*?)\]',
    ]:

        for match in re.findall(
            pattern,
            text,
            flags=re.I | re.S,
        ):

            urls.extend(
                extract_http_urls(
                    match
                )
            )

    return {
        "urls": unique(
            urls
        ),
        "payloads": payloads,
    }


# =========================================================
# ENGINE 3
# TIKTOK OEMBED
# =========================================================


async def engine_oembed(
    session,
    url,
):
    logger.info(
        "[ENGINE 3] TikTok oEmbed"
    )

    try:

        result = await get_text(
            session,
            "https://www.tiktok.com/oembed",
            params={
                "url": url
            },
            timeout=40,
        )

        if not result:
            return {}

        try:

            return json.loads(
                result["text"]
            )

        except Exception:

            return {}

    except Exception as e:

        logger.warning(
            "oEmbed error: %s",
            e,
        )

        return {}


# =========================================================
# ENGINE 4
# SNAP TIK
# =========================================================


async def engine_snaptik(
    session,
    url,
):
    logger.info(
        "[ENGINE 4] SnapTik"
    )

    all_urls = []
    payloads = []

    # -----------------------------------------------------
    # Get token
    # -----------------------------------------------------

    token = ""

    token_headers = {
        "Origin":
            "https://snaptik.app",

        "Referer":
            "https://snaptik.app/ar3",

        "Content-Type":
            "application/json",

        "X-Requested-With":
            "XMLHttpRequest",

        "Accept":
            "application/json,text/plain,*/*",
    }

    token_bodies = [
        {
            "url": url
        },
        {
            "url": url,
            "lang": "en",
        },
        {
            "url": url,
            "lang": "ar",
        },
    ]

    for body in token_bodies:

        result = await post_text(
            session,
            "https://snaptik.app/api/token",
            json_data=body,
            headers=token_headers,
            timeout=50,
        )

        if not result:
            continue

        raw = result[
            "text"
        ]

        try:
            payload = json.loads(
                raw
            )

        except Exception:
            payload = {
                "raw": raw
            }

        payloads.append(
            payload
        )

        all_urls.extend(
            recursive_urls(
                payload
            )
        )

        token_candidates = (
            recursive_values(
                payload,
                {
                    "token",
                    "x-token",
                    "xtoken",
                },
            )
        )

        for candidate in token_candidates:

            candidate = clean_text(
                candidate
            )

            if (
                len(candidate) > 20
                and len(candidate) < 5000
            ):
                token = candidate
                break

        if token:
            break

    # -----------------------------------------------------
    # Extract
    # -----------------------------------------------------

    bodies = [
        {
            "url": url
        },
        {
            "query": url
        },
    ]

    if token:

        bodies.extend([
            {
                "url": url,
                "token": token,
            },
            {
                "url": url,
                "x-token": token,
            },
        ])

    for body in bodies:

        headers = {
            "Origin":
                "https://snaptik.app",

            "Referer":
                "https://snaptik.app/ar3",

            "Content-Type":
                "application/json",

            "X-Requested-With":
                "XMLHttpRequest",

            "Accept":
                "application/json,text/plain,*/*",
        }

        if token:
            headers[
                "X-Token"
            ] = token

        result = await post_text(
            session,
            "https://snaptik.app/api/extract",
            json_data=body,
            headers=headers,
            timeout=70,
        )

        if not result:
            continue

        raw = result[
            "text"
        ]

        try:
            payload = json.loads(
                raw
            )

        except Exception:
            payload = {
                "raw": raw
            }

        payloads.append(
            payload
        )

        all_urls.extend(
            recursive_urls(
                payload
            )
        )

        all_urls.extend(
            extract_http_urls(
                raw
            )
        )

    return {
        "urls":
            unique(all_urls),

        "payloads":
            payloads,
    }


# =========================================================
# ENGINE 5
# SSSTIK
# =========================================================


async def engine_ssstik(
    session,
    url,
):
    logger.info(
        "[ENGINE 5] SSSTik"
    )

    all_urls = []

    home = await get_text(
        session,
        "https://ssstik.io",
        timeout=45,
    )

    if not home:
        return []

    token_match = re.search(
        r"tt:\s*['\"]([\w\d]+)['\"]",
        home["text"],
    )

    token = (
        token_match.group(1)
        if token_match
        else ""
    )

    headers = {
        "Origin":
            "https://ssstik.io",

        "Referer":
            "https://ssstik.io/en",

        "HX-Current-URL":
            "https://ssstik.io/en",

        "HX-Request":
            "true",

        "HX-Target":
            "target",

        "HX-Trigger":
            "_gcaptcha_pt",

        "Accept":
            "*/*",
    }

    data = {
        "id": url,
        "locale": "en",
        "tt": token,
    }

    result = await post_text(
        session,
        "https://ssstik.io/abc?url=dl",
        data=data,
        headers=headers,
        timeout=70,
    )

    if not result:
        return []

    raw = result[
        "text"
    ]

    all_urls.extend(
        extract_http_urls(
            raw
        )
    )

    # href extraction
    hrefs = re.findall(
        r'href=["\']([^"\']+)["\']',
        raw,
        flags=re.I,
    )

    all_urls.extend(
        hrefs
    )

    # Decode SSSCdn encoded paths
    for x in list(all_urls):

        if "ssscdn.io" in x:

            try:

                import base64

                part = "/".join(
                    x.split("/")[5:]
                )

                decoded = base64.b64decode(
                    part
                ).decode(
                    "utf-8",
                    errors="ignore",
                )

                all_urls.append(
                    decoded
                )

            except Exception:
                pass

    return unique(
        all_urls
    )


# =========================================================
# ENGINE 6
# TIKWM
# =========================================================


async def engine_tikwm(
    session,
    url,
):
    logger.info(
        "[ENGINE 6] TikWM"
    )

    endpoints = [
        "https://www.tikwm.com/api/",
        "https://tikwm.com/api/",
    ]

    found = []

    for endpoint in endpoints:

        result = await post_text(
            session,
            endpoint,
            data={
                "url": url,
                "hd": "1",
            },
            headers={
                "Content-Type":
                    "application/x-www-form-urlencoded",
                "Referer":
                    "https://www.tikwm.com/",
            },
            timeout=60,
        )

        if not result:
            continue

        raw = result[
            "text"
        ]

        try:

            payload = json.loads(
                raw
            )

            found.extend(
                recursive_urls(
                    payload
                )
            )

        except Exception:
            pass

        found.extend(
            extract_http_urls(
                raw
            )
        )

        if found:
            break

    return unique(
        found
    )


# =========================================================
# ENGINE 7
# TIKMATE
# =========================================================


async def engine_tikmate(
    session,
    url,
):
    logger.info(
        "[ENGINE 7] TikMate"
    )

    endpoints = [
        "https://api.tikmate.app/api/lookup",
        "https://tikmate.app/api/lookup",
    ]

    found = []

    for endpoint in endpoints:

        result = await get_text(
            session,
            endpoint,
            params={
                "url": url
            },
            timeout=60,
        )

        if not result:
            continue

        raw = result[
            "text"
        ]

        try:

            payload = json.loads(
                raw
            )

            found.extend(
                recursive_urls(
                    payload
                )
            )

        except Exception:
            pass

        found.extend(
            extract_http_urls(
                raw
            )
        )

        if found:
            break

    return unique(
        found
    )


# =========================================================
# ENGINE 8
# MUSICAL DOWN
# =========================================================


async def engine_musicaldown(
    session,
    url,
):
    logger.info(
        "[ENGINE 8] MusicalDown"
    )

    page = await get_text(
        session,
        "https://musicaldown.com/",
        timeout=50,
    )

    if not page:
        return []

    text = page[
        "text"
    ]

    # Try to locate csrf/token fields
    token = ""

    for pattern in [
        r'name=["\']_token["\'][^>]+value=["\']([^"\']+)',
        r'name=["\']token["\'][^>]+value=["\']([^"\']+)',
    ]:

        m = re.search(
            pattern,
            text,
            flags=re.I,
        )

        if m:
            token = m.group(1)
            break

    data = {
        "url": url
    }

    if token:
        data[
            "_token"
        ] = token

    endpoints = [
        "https://musicaldown.com/download",
        "https://musicaldown.com/",
    ]

    found = []

    for endpoint in endpoints:

        result = await post_text(
            session,
            endpoint,
            data=data,
            headers={
                "Referer":
                    "https://musicaldown.com/",
            },
            timeout=70,
        )

        if not result:
            continue

        raw = result[
            "text"
        ]

        found.extend(
            extract_http_urls(
                raw
            )
        )

        if found:
            break

    return unique(
        found
    )


# =========================================================
# ENGINE 9
# TTDOWNLOADER
# =========================================================


async def engine_ttdownloader(
    session,
    url,
):
    logger.info(
        "[ENGINE 9] TTDownloader"
    )

    pages = [
        "https://ttdownloader.com/",
        "https://ttdownloader.com/download",
    ]

    found = []

    for endpoint in pages:

        result = await post_text(
            session,
            endpoint,
            data={
                "url": url
            },
            headers={
                "Referer":
                    "https://ttdownloader.com/",
            },
            timeout=70,
        )

        if not result:
            continue

        raw = result[
            "text"
        ]

        found.extend(
            extract_http_urls(
                raw
            )
        )

        hrefs = re.findall(
            r'href=["\']([^"\']+)["\']',
            raw,
            flags=re.I,
        )

        found.extend(
            hrefs
        )

        if found:
            break

    return unique(
        found
    )


# =========================================================
# ENGINE 10
# DIRECT / RAPID CDN / GENERIC
# =========================================================


async def engine_generic(
    session,
    url,
):
    logger.info(
        "[ENGINE 10] Generic extraction"
    )

    result = await get_text(
        session,
        url,
        timeout=70,
    )

    if not result:
        return []

    raw = result[
        "text"
    ]

    found = []

    found.extend(
        extract_http_urls(
            raw
        )
    )

    # Common video fields
    patterns = [
        r'"playAddr"\s*:\s*"([^"]+)"',
        r'"downloadAddr"\s*:\s*"([^"]+)"',
        r'"play_addr"\s*:\s*"([^"]+)"',
        r'"download_addr"\s*:\s*"([^"]+)"',
        r'"video_url"\s*:\s*"([^"]+)"',
        r'"videoUrl"\s*:\s*"([^"]+)"',
        r'"url_list"\s*:\s*\[(.*?)\]',
    ]

    for pattern in patterns:

        found.extend(
            re.findall(
                pattern,
                raw,
                flags=re.I | re.S,
            )
        )

    return unique(
        found
    )


# =========================================================
# DIRECT FILE DOWNLOADER
# =========================================================


async def download_url(
    session,
    url,
    workdir,
):
    """
    Important:
    Never give CDN URL directly to Telegram.
    Download it to disk first.
    """

    if not url.startswith(
        ("http://", "https://")
    ):
        return None

    logger.info(
        "Downloading media: %s",
        url[:180],
    )

    headers = browser_headers(
        "https://www.tiktok.com/"
    )

    headers.update({
        "Accept": "*/*",
        "Connection": "keep-alive",
    })

    timeout_obj = (
        aiohttp.ClientTimeout(
            total=DOWNLOAD_TIMEOUT,
            connect=40,
            sock_read=DOWNLOAD_TIMEOUT,
        )
    )

    try:

        async with session.get(
            url,
            headers=headers,
            allow_redirects=True,
            timeout=timeout_obj,
        ) as response:

            if response.status >= 400:

                logger.warning(
                    "CDN HTTP %s",
                    response.status,
                )

                return None

            final_url = str(
                response.url
            )

            content_type = (
                response.headers.get(
                    "Content-Type",
                    "",
                ).lower()
            )

            filename = (
                "media_"
                + str(
                    random.randint(
                        100000,
                        999999,
                    )
                )
                + ".bin"
            )

            temp_path = (
                workdir / filename
            )

            total = 0

            with open(
                temp_path,
                "wb",
            ) as file:

                async for chunk in (
                    response.content.iter_chunked(
                        1024 * 1024
                    )
                ):

                    if not chunk:
                        continue

                    total += len(
                        chunk
                    )

                    if (
                        total
                        > MAX_DOWNLOAD_BYTES
                    ):

                        logger.warning(
                            "File exceeds limit"
                        )

                        try:
                            temp_path.unlink()
                        except Exception:
                            pass

                        return None

                    file.write(
                        chunk
                    )

            if total < 1024:

                temp_path.unlink(
                    missing_ok=True
                )

                return None

            # ---------------------------------------------
            # Validate actual content
            # ---------------------------------------------

            with open(
                temp_path,
                "rb",
            ) as file:

                head = file.read(
                    4096
                )

            # HTML response means downloader error page
            head_text = head.decode(
                "utf-8",
                errors="ignore",
            ).lower()

            if (
                "<html"
                in head_text
                or "<!doctype"
                in head_text
            ):

                temp_path.unlink(
                    missing_ok=True
                )

                return None

            # ---------------------------------------------
            # Detect type
            # ---------------------------------------------

            media_type = None

            if (
                "video/" in content_type
                or b"ftyp" in head[:100]
                or "mp4" in final_url.lower()
            ):
                media_type = "video"

            elif (
                "audio/" in content_type
                or head.startswith(b"ID3")
                or any(
                    x in final_url.lower()
                    for x in [
                        ".mp3",
                        ".m4a",
                        ".aac",
                    ]
                )
            ):
                media_type = "audio"

            elif (
                "image/" in content_type
                or head.startswith(
                    b"\xff\xd8\xff"
                )
                or head.startswith(
                    b"\x89PNG"
                )
                or b"WEBP" in head[:32]
            ):
                media_type = "image"

            else:

                media_type = (
                    await ffprobe_type(
                        temp_path
                    )
                )

            if not media_type:

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

            final_path = (
                workdir
                / (
                    "soko_"
                    + media_type
                    + extension
                )
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
                "path":
                    final_path,

                "type":
                    media_type,

                "size":
                    total,

                "url":
                    final_url,

                "content_type":
                    content_type,
            }

    except Exception as e:

        logger.warning(
            "Download error: %s",
            e,
        )

        return None


# =========================================================
# FFPROBE
# =========================================================


async def ffprobe_type(
    path,
):
    ffprobe = shutil.which(
        "ffprobe"
    )

    if not ffprobe:
        return None

    try:

        process = (
            await asyncio.create_subprocess_exec(
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=format_name",
                "-of",
                "default=nw=1:nk=1",
                str(path),
                stdout=
                    asyncio.subprocess.PIPE,
                stderr=
                    asyncio.subprocess.PIPE,
            )
        )

        stdout, _ = (
            await process.communicate()
        )

        fmt = stdout.decode(
            "utf-8",
            errors="ignore",
        ).lower()

        if any(
            x in fmt
            for x in [
                "mp4",
                "mov",
                "matroska",
                "webm",
                "mpegts",
            ]
        ):
            return "video"

        if any(
            x in fmt
            for x in [
                "mp3",
                "aac",
                "m4a",
                "ogg",
                "wav",
            ]
        ):
            return "audio"

    except Exception:
        pass

    return None


# =========================================================
# AUDIO EXTRACTION
# =========================================================


async def extract_audio(
    video_path,
    workdir,
):
    ffmpeg = shutil.which(
        "ffmpeg"
    )

    if not ffmpeg:
        return None

    output = (
        workdir
        / "soko_audio.mp3"
    )

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

        process = (
            await asyncio.create_subprocess_exec(
                *command,
                stdout=
                    asyncio.subprocess.PIPE,
                stderr=
                    asyncio.subprocess.PIPE,
            )
        )

        _, stderr = (
            await process.communicate()
        )

        if (
            process.returncode == 0
            and output.exists()
            and output.stat().st_size > 1024
        ):
            return output

        logger.warning(
            "ffmpeg failed: %s",
            stderr.decode(
                "utf-8",
                errors="ignore",
            )[-1500:],
        )

    except Exception as e:

        logger.warning(
            "Audio extraction error: %s",
            e,
        )

    return None


# =========================================================
# METADATA
# =========================================================


def build_metadata(
    oembed,
    payloads,
    webpage_payloads,
):
    all_payloads = []

    if isinstance(
        oembed,
        dict,
    ):
        all_payloads.append(
            oembed
        )

    all_payloads.extend(
        payloads
    )

    all_payloads.extend(
        webpage_payloads
    )

    metadata = {
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

    if isinstance(
        oembed,
        dict,
    ):

        metadata[
            "author"
        ] = (
            oembed.get(
                "author_name"
            )
            or ""
        )

        metadata[
            "username"
        ] = (
            oembed.get(
                "author_unique_id"
            )
            or ""
        )

        metadata[
            "description"
        ] = (
            oembed.get(
                "title"
            )
            or ""
        )

        metadata[
            "thumbnail"
        ] = (
            oembed.get(
                "thumbnail_url"
            )
            or ""
        )

    for payload in all_payloads:

        if not isinstance(
            payload,
            (dict, list),
        ):
            continue

        metadata[
            "author"
        ] = (
            metadata["author"]
            or first_value(
                payload,
                {
                    "author",
                    "author_name",
                    "nickname",
                    "nick_name",
                },
            )
        )

        metadata[
            "username"
        ] = (
            metadata["username"]
            or first_value(
                payload,
                {
                    "unique_id",
                    "uniqueId",
                    "username",
                    "author_unique_id",
                },
            )
        )

        metadata[
            "description"
        ] = (
            metadata["description"]
            or first_value(
                payload,
                {
                    "desc",
                    "description",
                    "caption",
                    "title",
                },
            )
        )

        metadata[
            "duration"
        ] = (
            metadata["duration"]
            or first_value(
                payload,
                {
                    "duration",
                    "duration_ms",
                },
            )
        )

        metadata[
            "views"
        ] = (
            metadata["views"]
            or first_value(
                payload,
                {
                    "playCount",
                    "play_count",
                    "playcount",
                    "views",
                    "view_count",
                },
            )
        )

        metadata[
            "likes"
        ] = (
            metadata["likes"]
            or first_value(
                payload,
                {
                    "diggCount",
                    "digg_count",
                    "diggcount",
                    "likes",
                    "like_count",
                },
            )
        )

        metadata[
            "comments"
        ] = (
            metadata["comments"]
            or first_value(
                payload,
                {
                    "commentCount",
                    "comment_count",
                    "commentcount",
                    "comments",
                },
            )
        )

        metadata[
            "shares"
        ] = (
            metadata["shares"]
            or first_value(
                payload,
                {
                    "shareCount",
                    "share_count",
                    "sharecount",
                    "shares",
                },
            )
        )

        metadata[
            "thumbnail"
        ] = (
            metadata["thumbnail"]
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

    # duration
    try:

        value = float(
            str(
                metadata[
                    "duration"
                ]
            )
        )

        if value > 1000:
            value /= 1000

        seconds = int(
            value
        )

        metadata[
            "duration"
        ] = (
            f"{seconds // 60:02d}:"
            f"{seconds % 60:02d}"
        )

    except Exception:
        pass

    return metadata


# =========================================================
# NUMBER FORMAT
# =========================================================


def format_number(
    value,
):
    value = clean_text(
        value
    )

    if not value:
        return "—"

    try:

        number = float(
            value.replace(
                ",",
                "",
            )
        )

        if number >= 1_000_000_000:
            return (
                f"{number / 1_000_000_000:.1f}B"
            )

        if number >= 1_000_000:
            return (
                f"{number / 1_000_000:.1f}M"
            )

        if number >= 1_000:
            return (
                f"{number / 1_000:.1f}K"
            )

        return str(
            int(number)
        )

    except Exception:

        return value


def result_text(
    metadata,
):
    author = (
        metadata.get(
            "author"
        )
        or "غير معروف"
    )

    username = (
        metadata.get(
            "username"
        )
        or "غير معروف"
    )

    if (
        username != "غير معروف"
        and not username.startswith("@")
    ):
        username = (
            "@"
            + username
        )

    description = (
        metadata.get(
            "description"
        )
        or "لا يوجد وصف"
    )

    return (
        "╔════════════════════╗\n"
        "      🌌 SOKO TK RESULT\n"
        "╚════════════════════╝\n\n"

        f"👤 صاحب المنشور: {author}\n"
        f"🔖 المستخدم: {username}\n"
        f"⏱️ المدة: "
        f"{metadata.get('duration') or '—'}\n"

        f"👁️ المشاهدات: "
        f"{format_number(metadata.get('views'))}\n"

        f"❤️ الإعجابات: "
        f"{format_number(metadata.get('likes'))}\n"

        f"💬 التعليقات: "
        f"{format_number(metadata.get('comments'))}\n"

        f"🔁 المشاركات: "
        f"{format_number(metadata.get('shares'))}\n\n"

        "📝 الوصف:\n"
        f"{description[:1500]}\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        "🚀 SOKO TK"
    )


# =========================================================
# MEDIA ENGINE MANAGER
# =========================================================


async def multi_engine(
    session,
    url,
    workdir,
    update_status=None,
):
    """
    Run 10 independent extraction paths.

    The first successful downloadable file wins.
    Other engines still contribute metadata and URLs.
    """

    collected_urls = []

    payloads = []

    webpage_payloads = []

    oembed = {}

    # -----------------------------------------------------
    # Webpage
    # -----------------------------------------------------

    try:

        webpage = (
            await engine_tiktok_web(
                session,
                url,
            )
        )

        collected_urls.extend(
            webpage.get(
                "urls",
                [],
            )
        )

        webpage_payloads.extend(
            webpage.get(
                "payloads",
                [],
            )
        )

    except Exception as e:

        logger.warning(
            "webpage engine failed: %s",
            e,
        )

    # -----------------------------------------------------
    # oEmbed
    # -----------------------------------------------------

    try:

        oembed = (
            await engine_oembed(
                session,
                url,
            )
        )

    except Exception:
        pass

    # -----------------------------------------------------
    # Engines 4-10
    # -----------------------------------------------------

    engine_results = []

    engine_results.append(
        (
            "SnapTik",
            engine_snaptik(
                session,
                url,
            ),
        )
    )

    engine_results.append(
        (
            "SSSTik",
            engine_ssstik(
                session,
                url,
            ),
        )
    )

    engine_results.append(
        (
            "TikWM",
            engine_tikwm(
                session,
                url,
            ),
        )
    )

    engine_results.append(
        (
            "TikMate",
            engine_tikmate(
                session,
                url,
            ),
        )
    )

    engine_results.append(
        (
            "MusicalDown",
            engine_musicaldown(
                session,
                url,
            ),
        )
    )

    engine_results.append(
        (
            "TTDownloader",
            engine_ttdownloader(
                session,
                url,
            ),
        )
    )

    engine_results.append(
        (
            "Generic",
            engine_generic(
                session,
                url,
            ),
        )
    )

    # Execute API engines concurrently
    results = await asyncio.gather(
        *[
            promise
            for _, promise
            in engine_results
        ],
        return_exceptions=True,
    )

    for (
        (name, _),
        result,
    ) in zip(
        engine_results,
        results,
    ):

        if isinstance(
            result,
            Exception,
        ):

            logger.warning(
                "[%s] exception: %s",
                name,
                result,
            )

            continue

        if isinstance(
            result,
            dict,
        ):

            collected_urls.extend(
                result.get(
                    "urls",
                    [],
                )
            )

            payloads.extend(
                result.get(
                    "payloads",
                    [],
                )
            )

        elif isinstance(
            result,
            list,
        ):

            collected_urls.extend(
                result
            )

    # -----------------------------------------------------
    # Prioritize media
    # -----------------------------------------------------

    collected_urls = unique(
        collected_urls
    )

    videos = []
    audios = []
    images = []
    unknown = []

    for media_url in collected_urls:

        kind = classify_url(
            media_url
        )

        if kind == "video":
            videos.append(
                media_url
            )

        elif kind == "audio":
            audios.append(
                media_url
            )

        elif kind == "image":
            images.append(
                media_url
            )

        else:
            unknown.append(
                media_url
            )

    # CDN links often don't expose .mp4
    # so unknown URLs are also tested.
    videos.extend(
        unknown
    )

    # -----------------------------------------------------
    # Direct media attempts
    # -----------------------------------------------------

    video = None

    for media_url in unique(
        videos
    ):

        video = await download_url(
            session,
            media_url,
            workdir,
        )

        if (
            video
            and video["type"] == "video"
        ):
            break

        video = None

    # -----------------------------------------------------
    # Audio attempts
    # -----------------------------------------------------

    audio = None

    for media_url in unique(
        audios
    ):

        audio = await download_url(
            session,
            media_url,
            workdir,
        )

        if (
            audio
            and audio["type"] == "audio"
        ):
            break

        audio = None

    # -----------------------------------------------------
    # Images
    # -----------------------------------------------------

    image_files = []

    for media_url in unique(
        images
    )[:MAX_IMAGES]:

        image = await download_url(
            session,
            media_url,
            workdir,
        )

        if (
            image
            and image["type"] == "image"
        ):

            image_files.append(
                image
            )

    # -----------------------------------------------------
    # ENGINE 1: yt-dlp fallback
    # -----------------------------------------------------

    if not video:

        ytdlp_result = (
            await engine_ytdlp(
                url,
                workdir,
            )
        )

        if ytdlp_result:

            video = (
                ytdlp_result[0]
            )

    # -----------------------------------------------------
    # Audio from downloaded video
    # -----------------------------------------------------

    if (
        video
        and not audio
    ):

        extracted = (
            await extract_audio(
                video["path"],
                workdir,
            )
        )

        if extracted:

            audio = {
                "path":
                    extracted,

                "type":
                    "audio",

                "size":
                    extracted.stat().st_size,

                "engine":
                    "ffmpeg",
            }

    return {
        "video":
            video,

        "audio":
            audio,

        "images":
            image_files,

        "metadata":
            build_metadata(
                oembed,
                payloads,
                webpage_payloads,
            ),

        "urls_found":
            len(
                collected_urls
            ),

        "engine_urls":
            collected_urls,
    }


# =========================================================
# TELEGRAM SEND VIDEO
# =========================================================


async def send_video(
    bot,
    chat_id,
    path,
):
    if not path.exists():
        return False

    size = path.stat().st_size

    if size > TELEGRAM_SAFE_BYTES:
        return False

    try:

        await bot.send_chat_action(
            chat_id,
            ChatAction.UPLOAD_VIDEO,
        )

        with open(
            path,
            "rb",
        ) as file:

            await bot.send_video(
                chat_id=chat_id,
                video=InputFile(
                    file,
                    filename="SOKO_TK.mp4",
                ),
                supports_streaming=True,
                read_timeout=180,
                write_timeout=180,
                connect_timeout=60,
                pool_timeout=60,
            )

        return True

    except Exception as e:

        logger.warning(
            "Telegram video error: %s",
            e,
        )

        return False


# =========================================================
# TELEGRAM SEND AUDIO
# =========================================================


async def send_audio(
    bot,
    chat_id,
    path,
):
    if not path.exists():
        return False

    if (
        path.stat().st_size
        > TELEGRAM_SAFE_BYTES
    ):
        return False

    try:

        await bot.send_chat_action(
            chat_id,
            ChatAction.UPLOAD_DOCUMENT,
        )

        with open(
            path,
            "rb",
        ) as file:

            await bot.send_audio(
                chat_id=chat_id,
                audio=InputFile(
                    file,
                    filename="SOKO_TK.mp3",
                ),
                read_timeout=180,
                write_timeout=180,
                connect_timeout=60,
                pool_timeout=60,
            )

        return True

    except Exception as e:

        logger.warning(
            "Telegram audio error: %s",
            e,
        )

        return False


# =========================================================
# SEND IMAGES
# =========================================================


async def send_images(
    bot,
    chat_id,
    images,
):
    sent = 0

    for image in images:

        path = image[
            "path"
        ]

        if not path.exists():
            continue

        if (
            path.stat().st_size
            > 15 * 1024 * 1024
        ):
            continue

        try:

            with open(
                path,
                "rb",
            ) as file:

                await bot.send_photo(
                    chat_id=chat_id,
                    photo=InputFile(
                        file
                    ),
                    read_timeout=90,
                    write_timeout=90,
                    connect_timeout=40,
                )

            sent += 1

        except Exception as e:

            logger.warning(
                "Image send failed: %s",
                e,
            )

    return sent


# =========================================================
# START
# =========================================================


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = (
        update.effective_user
    )

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

        "📥 أرسل أي رابط TikTok."
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
# HELP
# =========================================================


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        "🚀 SOKO TK X10\n\n"

        "أرسل رابط TikTok بأي شكل.\n\n"

        "✓ www.tiktok.com\n"
        "✓ tiktok.com\n"
        "✓ vt.tiktok.com\n"
        "✓ vm.tiktok.com\n"
        "✓ m.tiktok.com\n"
        "✓ tiktokv.com\n"
        "✓ روابط المشاركة\n"
        "✓ روابط تحتوي Tracking Parameters\n\n"

        "⚡ البوت يستخدم عدة محركات استخراج "
        "ويجرب البدائل تلقائيًا."
    )


# =========================================================
# CALLBACK
# =========================================================


async def callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = (
        update.callback_query
    )

    if query:
        await query.answer()


# =========================================================
# MAIN TIKTOK HANDLER
# =========================================================


async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = (
        update.effective_message
    )

    if (
        not message
        or not message.text
    ):
        return

    url = extract_tiktok_url(
        message.text
    )

    if not url:

        await message.reply_text(
            "❌ أرسل رابط TikTok صالح.\n\n"
            "مثال:\n"
            "https://vt.tiktok.com/..."
        )

        return

    processing = (
        await message.reply_text(
            "🚀 SOKO TK X10\n\n"
            "🔗 تم استلام الرابط.\n"
            "⚡ جاري تشغيل محركات الاستخراج..."
        )
    )

    workdir = Path(
        tempfile.mkdtemp(
            prefix="soko_x10_",
            dir=str(
                TEMP_ROOT
            ),
        )
    )

    try:

        timeout = (
            aiohttp.ClientTimeout(
                total=REQUEST_TIMEOUT,
                connect=40,
                sock_read=REQUEST_TIMEOUT,
            )
        )

        connector = (
            aiohttp.TCPConnector(
                limit=40,
                ttl_dns_cache=300,
                ssl=False,
            )
        )

        async with aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
        ) as session:

            # ---------------------------------------------
            # Resolve
            # ---------------------------------------------

            await processing.edit_text(
                "🔗 SOKO TK X10\n\n"
                "1️⃣ معالجة الرابط\n"
                "2️⃣ معالجة روابط المشاركة\n"
                "3️⃣ تجهيز محركات التحميل..."
            )

            resolved = (
                await resolve_tiktok(
                    session,
                    url,
                )
            )

            logger.info(
                "Original: %s",
                url,
            )

            logger.info(
                "Resolved: %s",
                resolved,
            )

            # ---------------------------------------------
            # Start engines
            # ---------------------------------------------

            await processing.edit_text(
                "⚡ SOKO TK X10\n\n"
                "🔎 يتم الآن تجربة عدة مصادر...\n"
                "⏳ لن يتوقف عند فشل مصدر واحد."
            )

            result = (
                await multi_engine(
                    session,
                    resolved,
                    workdir,
                )
            )

            video = result[
                "video"
            ]

            audio = result[
                "audio"
            ]

            images = result[
                "images"
            ]

            metadata = result[
                "metadata"
            ]

            found_urls = result[
                "urls_found"
            ]

            logger.info(
                "Media URLs found: %s",
                found_urls,
            )

            # ---------------------------------------------
            # Result message
            # ---------------------------------------------

            info = result_text(
                metadata
            )

            # ---------------------------------------------
            # Video
            # ---------------------------------------------

            if video:

                await processing.edit_text(
                    "✅ تم استخراج الفيديو.\n"
                    "📦 تم تنزيله إلى السيرفر.\n"
                    "📤 جاري رفعه إلى Telegram..."
                )

                sent = (
                    await send_video(
                        context.bot,
                        message.chat_id,
                        video["path"],
                    )
                )

                if sent:

                    await message.reply_text(
                        info
                    )

                else:

                    size_mb = (
                        video["path"].stat().st_size
                        / 1024
                        / 1024
                    )

                    await message.reply_text(
                        info
                        + "\n\n"
                        "⚠️ تم تنزيل الفيديو بنجاح "
                        "لكن Telegram لم يقبل إرساله.\n"
                        f"📦 الحجم: {size_mb:.1f} MB"
                    )

            # ---------------------------------------------
            # Photo mode
            # ---------------------------------------------

            elif images:

                await processing.edit_text(
                    "🖼️ تم اكتشاف منشور صور.\n"
                    f"📦 عدد الصور: {len(images)}\n"
                    "📤 جاري الإرسال..."
                )

                await message.reply_text(
                    info
                    + "\n\n"
                    f"🖼️ الصور: {len(images)}"
                )

                await send_images(
                    context.bot,
                    message.chat_id,
                    images,
                )

            # ---------------------------------------------
            # Audio only
            # ---------------------------------------------

            elif audio:

                await processing.edit_text(
                    "🎵 تم العثور على الصوت.\n"
                    "📤 جاري الإرسال..."
                )

                await send_audio(
                    context.bot,
                    message.chat_id,
                    audio["path"],
                )

                await message.reply_text(
                    info
                )

            # ---------------------------------------------
            # No media
            # ---------------------------------------------

            else:

                await processing.edit_text(
                    "❌ لم يتم استخراج الوسائط.\n\n"
                    "🔍 تم تشغيل محركات SOKO TK X10.\n"
                    "قد يكون الرابط محميًا أو المنشور "
                    "غير متاح للتحميل من الشبكة الحالية."
                )

            # ---------------------------------------------
            # Audio alongside video
            # ---------------------------------------------

            if (
                video
                and audio
                and audio["path"].exists()
            ):

                await send_audio(
                    context.bot,
                    message.chat_id,
                    audio["path"],
                )

        # Delete processing
        try:
            await processing.delete()
        except Exception:
            pass

    except Exception as e:

        logger.exception(
            "SOKO handler error"
        )

        try:

            await processing.edit_text(
                "❌ حدث خطأ غير متوقع.\n\n"
                "🔄 أرسل الرابط مرة ثانية."
            )

        except Exception:
            pass

    finally:

        try:

            shutil.rmtree(
                workdir,
                ignore_errors=True,
            )

        except Exception:
            pass


# =========================================================
# MAIN
# =========================================================


def main():

    logger.info(
        "===================================="
    )

    logger.info(
        "SOKO TK X10 STARTING"
    )

    logger.info(
        "Multi Engine Downloader"
    )

    logger.info(
        "===================================="
    )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(
            True
        )
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
            callback
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            handle_message,
        )
    )

    application.run_polling(
        allowed_updates=
            Update.ALL_TYPES,

        drop_pending_updates=
            True,
    )


if __name__ == "__main__":
    main()
