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

from urllib.parse import urlparse, quote

import aiohttp

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
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
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WELCOME_IMAGE = os.getenv("JPG", "").strip()

DEV_USERNAME = "FDF01"

TIMEOUT = aiohttp.ClientTimeout(
    total=90,
    connect=20,
    sock_read=60,
)

MAX_PHOTOS = 20

TEMP_PREFIX = "soko_tk_"


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("SOKO-TK")


# =========================================================
# USER AGENTS
# =========================================================

USER_AGENTS = [
    (
        "Mozilla/5.0 (Linux; Android 15) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/139.0.0.0 Mobile Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Linux; Android 14) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/138.0.0.0 Mobile Safari/537.36"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/139.0.0.0 Safari/537.36"
    ),
]


def ua():
    return random.choice(USER_AGENTS)


# =========================================================
# HEADERS
# =========================================================

def browser_headers(
    referer="https://snaptik.app/"
):
    return {
        "User-Agent": ua(),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": (
            "ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7"
        ),
        "Referer": referer,
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }


# =========================================================
# TIKTOK URL
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


def is_tiktok_url(url):

    try:
        host = (
            urlparse(url).hostname or ""
        ).lower()

        return (
            host in TIKTOK_HOSTS
            or host.endswith(".tiktok.com")
            or host.endswith(".tiktokv.com")
        )

    except Exception:
        return False


def extract_tiktok_url(text):

    if not text:
        return None

    matches = re.findall(
        r"https?://[^\s<>\"]+",
        text,
        flags=re.I,
    )

    for url in matches:

        url = url.rstrip(
            ".,!?؛،)>]}\"'"
        )

        if is_tiktok_url(url):
            return url

    return None


# =========================================================
# RESOLVE SHORT URL
# =========================================================

async def resolve_url(
    session,
    url,
):

    host = (
        urlparse(url).hostname or ""
    ).lower()

    if host not in {
        "vt.tiktok.com",
        "vm.tiktok.com",
    }:
        return url

    try:

        async with session.get(
            url,
            headers=browser_headers(),
            allow_redirects=True,
        ) as response:

            final_url = str(
                response.url
            )

            if is_tiktok_url(
                final_url
            ):

                logger.info(
                    "SHORT URL -> %s",
                    final_url,
                )

                return final_url

    except Exception as exc:

        logger.warning(
            "Short URL resolve failed: %s",
            exc,
        )

    return url


# =========================================================
# OEmbed
# =========================================================

async def get_oembed(
    session,
    tiktok_url,
):

    endpoint = (
        "https://www.tiktok.com/oembed"
    )

    try:

        async with session.get(
            endpoint,
            params={
                "url": tiktok_url,
            },
            headers={
                **browser_headers(
                    "https://snaptik.app/"
                ),
                "Accept": "*/*",
                "Origin": "https://snaptik.app",
            },
            timeout=TIMEOUT,
        ) as response:

            text = await response.text()

            logger.info(
                "oEmbed status=%s",
                response.status,
            )

            if response.status != 200:
                return None

            try:
                return json.loads(text)

            except Exception:
                return None

    except Exception as exc:

        logger.warning(
            "oEmbed failed: %s",
            exc,
        )

        return None


# =========================================================
# SNAP TIK TOKEN
# =========================================================

async def get_snaptik_token(
    session,
):

    try:

        headers = {
            **browser_headers(
                "https://snaptik.app/ar3"
            ),
            "Accept": "*/*",
            "Content-Type": "application/json",
            "Origin": "https://snaptik.app",
            "X-Requested-With": "XMLHttpRequest",
        }

        async with session.post(
            "https://snaptik.app/api/token",
            headers=headers,
            data=b"",
            timeout=TIMEOUT,
        ) as response:

            text = await response.text()

            logger.info(
                "SnapTik token status=%s",
                response.status,
            )

            if response.status >= 400:
                return None

            try:
                data = json.loads(text)

            except Exception:

                # أحيانًا قد يرجع النص مباشرة
                if (
                    len(text.strip()) > 10
                    and len(text.strip()) < 10000
                ):
                    return text.strip()

                return None

            # البحث المرن عن token
            return find_token(data)

    except Exception as exc:

        logger.warning(
            "SnapTik token error: %s",
            exc,
        )

        return None


def find_token(data):

    if isinstance(data, str):

        value = data.strip()

        if value:
            return value

        return None

    if isinstance(data, dict):

        preferred = [
            "token",
            "x-token",
            "x_token",
            "key",
            "value",
        ]

        for key in preferred:

            value = data.get(key)

            if isinstance(value, str):
                if value.strip():
                    return value.strip()

        for value in data.values():

            result = find_token(value)

            if result:
                return result

    elif isinstance(data, list):

        for item in data:

            result = find_token(item)

            if result:
                return result

    return None


# =========================================================
# GENERIC URL FINDER
# =========================================================

def find_urls(
    value,
    found=None,
):

    if found is None:
        found = []

    if isinstance(value, str):

        for match in re.findall(
            r'https?://[^\s"\'<>]+',
            value,
            flags=re.I,
        ):

            match = match.rstrip(
                "\"'<>),"
            )

            if match not in found:
                found.append(match)

    elif isinstance(value, dict):

        for item in value.values():
            find_urls(
                item,
                found,
            )

    elif isinstance(value, list):

        for item in value:
            find_urls(
                item,
                found,
            )

    return found


# =========================================================
# MEDIA CLASSIFIER
# =========================================================

def media_type_from_url(url):

    lower = url.lower()

    if (
        ".mp4" in lower
        or "video" in lower
        or "rapidcdn" in lower
        or "snapxcdn" in lower
        or "cdn.snaptik" in lower
    ):
        return "video"

    if (
        ".mp3" in lower
        or ".m4a" in lower
        or "audio" in lower
        or "music" in lower
    ):
        return "audio"

    if (
        ".jpg" in lower
        or ".jpeg" in lower
        or ".png" in lower
        or ".webp" in lower
        or ".avif" in lower
        or "image" in lower
        or "cover" in lower
        or "photo" in lower
    ):
        return "image"

    return None


# =========================================================
# SNAP TIK EXTRACT
# =========================================================

async def snaptik_extract(
    session,
    tiktok_url,
    token=None,
):

    endpoint = (
        "https://snaptik.app/api/extract"
    )

    attempts = []

    # الطلب الأساسي
    attempts.append(
        {
            "url": endpoint,
            "params": {
                "url": tiktok_url,
            },
        }
    )

    # مع token
    if token:

        attempts.append(
            {
                "url": endpoint,
                "params": {
                    "url": tiktok_url,
                    "token": token,
                },
            }
        )

        attempts.append(
            {
                "url": endpoint,
                "params": {
                    "url": tiktok_url,
                    "x-token": token,
                },
            }
        )

    for attempt in attempts:

        try:

            headers = {
                **browser_headers(
                    "https://snaptik.app/ar3"
                ),
                "Accept": (
                    "application/json,"
                    "text/plain,*/*"
                ),
                "Origin": "https://snaptik.app",
                "X-Requested-With":
                    "XMLHttpRequest",
            }

            if token:
                headers["X-Token"] = token

            async with session.get(
                attempt["url"],
                params=attempt["params"],
                headers=headers,
                timeout=TIMEOUT,
            ) as response:

                text = await response.text()

                logger.info(
                    "SnapTik extract status=%s",
                    response.status,
                )

                if response.status >= 400:
                    continue

                try:

                    data = json.loads(
                        text
                    )

                    if data:
                        return data

                except Exception:

                    # قد تكون HTML تحتوي روابط CDN
                    if (
                        "rapidcdn"
                        in text.lower()
                        or "snapxcdn"
                        in text.lower()
                        or "tiktokcdn"
                        in text.lower()
                    ):
                        return {
                            "_raw_html":
                                text
                        }

        except Exception as exc:

            logger.warning(
                "SnapTik extraction failed: %s",
                exc,
            )

    return None


# =========================================================
# MEDIA FROM SNAPTIK
# =========================================================

def extract_snap_media(data):

    videos = []
    audios = []
    images = []

    urls = find_urls(data)

    for url in urls:

        kind = media_type_from_url(
            url
        )

        if kind == "video":
            videos.append(url)

        elif kind == "audio":
            audios.append(url)

        elif kind == "image":
            images.append(url)

    # raw HTML fallback
    if isinstance(data, dict):

        raw = data.get(
            "_raw_html",
            "",
        )

        if raw:

            for url in re.findall(
                r'https?://[^"\'<>\s]+',
                raw,
                flags=re.I,
            ):

                url = url.rstrip(
                    "\"'<>),"
                )

                kind = media_type_from_url(
                    url
                )

                if kind == "video":
                    videos.append(url)

                elif kind == "audio":
                    audios.append(url)

                elif kind == "image":
                    images.append(url)

    return (
        unique(videos),
        unique(audios),
        unique(images),
    )


# =========================================================
# UNIQUE
# =========================================================

def unique(items):

    result = []
    seen = set()

    for item in items:

        if not item:
            continue

        if item in seen:
            continue

        seen.add(item)
        result.append(item)

    return result


# =========================================================
# EXTRACT OEmbed METADATA
# =========================================================

def metadata_from_oembed(
    oembed,
):

    if not isinstance(
        oembed,
        dict,
    ):
        return {}

    return {
        "title":
            oembed.get(
                "title"
            )
            or "غير متوفر",

        "author":
            oembed.get(
                "author_name"
            )
            or "غير متوفر",

        "username":
            oembed.get(
                "author_url"
            )
            or "غير متوفر",

        "thumbnail":
            oembed.get(
                "thumbnail_url"
            ),
    }


# =========================================================
# METADATA GENERIC
# =========================================================

def find_first(
    data,
    keys,
):

    keys = {
        x.lower()
        for x in keys
    }

    def walk(value):

        if isinstance(
            value,
            dict,
        ):

            for key, item in value.items():

                if (
                    str(key).lower()
                    in keys
                ):

                    if isinstance(
                        item,
                        (
                            str,
                            int,
                            float,
                        ),
                    ):

                        text = str(
                            item
                        ).strip()

                        if text:
                            return text

                result = walk(item)

                if result:
                    return result

        elif isinstance(
            value,
            list,
        ):

            for item in value:

                result = walk(item)

                if result:
                    return result

        return None

    return walk(data)


def format_number(value):

    try:

        number = int(
            float(value)
        )

    except Exception:

        return (
            str(value)
            if value
            else "غير متوفر"
        )

    if number >= 1_000_000_000:
        return f"{number / 1_000_000_000:.1f}B"

    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"

    if number >= 1_000:
        return f"{number / 1_000:.1f}K"

    return f"{number:,}"


def format_duration(value):

    try:

        seconds = int(
            float(value)
        )

    except Exception:

        return "غير متوفر"

    minutes = seconds // 60
    seconds = seconds % 60

    return (
        f"{minutes:02d}:"
        f"{seconds:02d}"
    )


def build_metadata(
    oembed,
    data,
):

    result = (
        metadata_from_oembed(
            oembed
        )
    )

    title = (
        find_first(
            data,
            {
                "title",
                "desc",
                "description",
                "caption",
            },
        )
        or result.get(
            "title"
        )
        or "غير متوفر"
    )

    author = (
        find_first(
            data,
            {
                "author_name",
                "nickname",
                "author",
                "creator",
                "uploader",
            },
        )
        or result.get(
            "author"
        )
        or "غير متوفر"
    )

    username = (
        find_first(
            data,
            {
                "username",
                "unique_id",
                "uniqueid",
                "author_unique_id",
            },
        )
        or result.get(
            "username"
        )
        or "غير متوفر"
    )

    duration = find_first(
        data,
        {
            "duration",
            "duration_ms",
        },
    )

    views = find_first(
        data,
        {
            "views",
            "view_count",
            "play_count",
        },
    )

    likes = find_first(
        data,
        {
            "likes",
            "like_count",
            "digg_count",
        },
    )

    comments = find_first(
        data,
        {
            "comments",
            "comment_count",
        },
    )

    shares = find_first(
        data,
        {
            "shares",
            "share_count",
        },
    )

    thumbnail = (
        result.get(
            "thumbnail"
        )
        or find_first(
            data,
            {
                "thumbnail",
                "thumbnail_url",
                "cover",
                "cover_url",
            },
        )
    )

    return {
        "title": str(title),
        "author": str(author),
        "username": str(username),
        "duration":
            format_duration(
                duration
            )
            if duration
            else "غير متوفر",
        "views":
            format_number(
                views
            ),
        "likes":
            format_number(
                likes
            ),
        "comments":
            format_number(
                comments
            ),
        "shares":
            format_number(
                shares
            ),
        "thumbnail":
            thumbnail,
    }


# =========================================================
# RESULT TEXT
# =========================================================

def result_text(meta):

    title = html.escape(
        meta["title"]
    )

    author = html.escape(
        meta["author"]
    )

    username = html.escape(
        meta["username"]
    )

    return (
        "╔════════════════════╗\n"
        "      🌌 SOKO TK RESULT\n"
        "╚════════════════════╝\n\n"
        f"👤 صاحب المنشور: {author}\n"
        f"🔖 المستخدم: {username}\n"
        f"⏱️ المدة: {meta['duration']}\n"
        f"👁️ المشاهدات: {meta['views']}\n"
        f"❤️ الإعجابات: {meta['likes']}\n"
        f"💬 التعليقات: {meta['comments']}\n"
        f"🔁 المشاركات: {meta['shares']}\n\n"
        f"📝 الوصف:\n{title}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🚀 SOKO TK"
    )


# =========================================================
# DIRECT RAPIDCDN DOWNLOAD
# =========================================================

async def download_direct(
    session,
    url,
    directory,
):

    try:

        filename = (
            "soko_media_"
            + str(
                random.randint(
                    100000,
                    999999,
                )
            )
        )

        path = os.path.join(
            directory,
            filename,
        )

        headers = {
            "User-Agent": (
                "TelegramBot "
                "(like TwitterBot)"
            ),
            "Referer":
                "https://snaptik.app/",
        }

        async with session.get(
            url,
            headers=headers,
            allow_redirects=True,
            timeout=aiohttp.ClientTimeout(
                total=180,
                connect=30,
                sock_read=120,
            ),
        ) as response:

            if response.status >= 400:

                logger.warning(
                    "CDN status=%s",
                    response.status,
                )

                return None

            content_type = (
                response.headers.get(
                    "Content-Type",
                    "",
                ).lower()
            )

            extension = ".mp4"

            if (
                "audio" in content_type
            ):
                extension = ".mp3"

            elif (
                "jpeg" in content_type
            ):
                extension = ".jpg"

            elif (
                "png" in content_type
            ):
                extension = ".png"

            path += extension

            with open(
                path,
                "wb",
            ) as file:

                async for chunk in response.content.iter_chunked(
                    1024 * 256
                ):

                    if chunk:
                        file.write(
                            chunk
                        )

            if (
                os.path.exists(path)
                and os.path.getsize(path)
                > 0
            ):

                logger.info(
                    "Downloaded CDN media: %s",
                    path,
                )

                return path

    except Exception as exc:

        logger.warning(
            "Direct CDN download failed: %s",
            exc,
        )

    return None


# =========================================================
# YT-DLP FALLBACK
# =========================================================

def ensure_ytdlp():

    try:

        import yt_dlp

        return yt_dlp

    except ImportError:

        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "-U",
                "yt-dlp",
            ],
            check=False,
        )

        import yt_dlp

        return yt_dlp


def ytdlp_download(
    url,
    directory,
):

    try:

        yt_dlp = ensure_ytdlp()

        output = os.path.join(
            directory,
            "%(id)s.%(ext)s",
        )

        options = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "nocheckcertificate": True,
            "retries": 2,
            "fragment_retries": 2,
            "socket_timeout": 30,
            "outtmpl": output,
            "format":
                "bv*+ba/b",
            "merge_output_format":
                "mp4",
            "http_headers": {
                "User-Agent": ua(),
                "Accept-Language":
                    "en-US,en;q=0.9",
            },
        }

        with yt_dlp.YoutubeDL(
            options
        ) as ydl:

            info = ydl.extract_info(
                url,
                download=True,
            )

            prepared = (
                ydl.prepare_filename(
                    info
                )
            )

            mp4 = os.path.splitext(
                prepared
            )[0] + ".mp4"

            if os.path.isfile(
                mp4
            ):
                return mp4

            if os.path.isfile(
                prepared
            ):
                return prepared

        for name in os.listdir(
            directory
        ):

            path = os.path.join(
                directory,
                name,
            )

            if (
                os.path.isfile(path)
                and name.lower().endswith(
                    (
                        ".mp4",
                        ".webm",
                        ".mkv",
                        ".mov",
                    )
                )
            ):
                return path

    except Exception as exc:

        logger.warning(
            "yt-dlp fallback failed: %s",
            exc,
        )

    return None


# =========================================================
# SEND VIDEO
# =========================================================

async def send_video_file(
    bot,
    chat_id,
    path,
):

    try:

        await bot.send_chat_action(
            chat_id=chat_id,
            action=ChatAction.UPLOAD_VIDEO,
        )

        with open(
            path,
            "rb",
        ) as file:

            await bot.send_video(
                chat_id=chat_id,
                video=file,
                supports_streaming=True,
                read_timeout=180,
                write_timeout=180,
                connect_timeout=60,
            )

        return True

    except Exception as exc:

        logger.warning(
            "Telegram video upload failed: %s",
            exc,
        )

        return False


# =========================================================
# SEND AUDIO
# =========================================================

async def send_audio_file(
    bot,
    chat_id,
    path,
):

    try:

        await bot.send_chat_action(
            chat_id=chat_id,
            action=ChatAction.UPLOAD_AUDIO,
        )

        with open(
            path,
            "rb",
        ) as file:

            await bot.send_audio(
                chat_id=chat_id,
                audio=file,
                performer="SOKO TK",
                title="TikTok Audio",
                read_timeout=180,
                write_timeout=180,
                connect_timeout=60,
            )

        return True

    except Exception as exc:

        logger.warning(
            "Telegram audio upload failed: %s",
            exc,
        )

        return False


# =========================================================
# SEND PHOTOS
# =========================================================

async def send_photos(
    bot,
    chat_id,
    images,
):

    images = unique(
        images
    )[:MAX_PHOTOS]

    if not images:
        return False

    for start in range(
        0,
        len(images),
        10,
    ):

        chunk = images[
            start:start + 10
        ]

        try:

            if len(chunk) == 1:

                await bot.send_photo(
                    chat_id=chat_id,
                    photo=chunk[0],
                )

            else:

                media = [
                    InputMediaPhoto(
                        media=url
                    )
                    for url in chunk
                ]

                await bot.send_media_group(
                    chat_id=chat_id,
                    media=media,
                )

        except Exception as exc:

            logger.warning(
                "Photo group failed: %s",
                exc,
            )

            for image in chunk:

                try:

                    await bot.send_photo(
                        chat_id=chat_id,
                        photo=image,
                    )

                except Exception:
                    pass

    return True


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user = update.effective_user

    if not user:
        return

    text = (
        "𐚁‌⇄❮𝗦𝗢𝗞𝗢❯\n"
        "╔════════════════════╗\n"
        "      🌌 DOWNLOAD VIDEO TIKTOK\n"
        "╚════════════════════╝\n\n"
        "𝗦𝗢𝗞𝗢・TK 🎧 ❮❯\n"
        "🎵 تنزيل صوت - فيديو - ستوري - تيكتوك\n"
        "🖼️ صور ومعلومات كاملة\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 الاسم: "
        f"{html.escape(user.full_name or 'User')}\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📥 أرسل رابط TikTok الآن."
    )

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "👨‍💻 DEV",
                    url="https://t.me/FDF01",
                )
            ],
            [
                InlineKeyboardButton(
                    "ℹ️ طريقة الاستخدام",
                    callback_data="help",
                )
            ],
        ]
    )

    if WELCOME_IMAGE:

        try:

            await update.message.reply_photo(
                photo=WELCOME_IMAGE,
                caption=text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )

            return

        except Exception as exc:

            logger.warning(
                "Welcome image failed: %s",
                exc,
            )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# =========================================================
# HELP
# =========================================================

async def callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    if not query:
        return

    await query.answer()

    if query.data == "help":

        await query.message.reply_text(
            "📥 أرسل أي رابط TikTok.\n\n"
            "مدعوم:\n"
            "• www.tiktok.com\n"
            "• tiktok.com\n"
            "• vt.tiktok.com\n"
            "• vm.tiktok.com\n"
            "• m.tiktok.com\n"
            "• روابط المشاركة والتحويل\n\n"
            "SOKO يحاول عدة طرق لاستخراج "
            "الفيديو أو الصوت أو الصور."
        )


# =========================================================
# MAIN TIKTOK HANDLER
# =========================================================

async def handle_tiktok(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.message:
        return

    url = extract_tiktok_url(
        update.message.text or ""
    )

    if not url:

        await update.message.reply_text(
            "❌ أرسل رابط TikTok صالح."
        )

        return

    processing = await update.message.reply_text(
        "🚀 <b>SOKO TK</b>\n\n"
        "🔗 تم استلام الرابط.\n"
        "🔍 جاري التحليل...\n"
        "⚡ سأجرب أكثر من طريقة تلقائيًا.",
        parse_mode="HTML",
    )

    temp_dir = tempfile.mkdtemp(
        prefix=TEMP_PREFIX
    )

    try:

        async with aiohttp.ClientSession(
            timeout=TIMEOUT
        ) as session:

            # =============================================
            # 1. Resolve URL
            # =============================================

            final_url = await resolve_url(
                session,
                url,
            )

            # =============================================
            # 2. OEmbed
            # =============================================

            oembed = await get_oembed(
                session,
                final_url,
            )

            # =============================================
            # 3. SnapTik token
            # =============================================

            token = await get_snaptik_token(
                session
            )

            # =============================================
            # 4. SnapTik extraction
            # =============================================

            snap_data = await snaptik_extract(
                session,
                final_url,
                token,
            )

            # =============================================
            # 5. Media extraction
            # =============================================

            videos = []
            audios = []
            images = []

            if snap_data:

                (
                    videos,
                    audios,
                    images,
                ) = extract_snap_media(
                    snap_data
                )

            # =============================================
            # 6. Metadata
            # =============================================

            meta = build_metadata(
                oembed,
                snap_data or {},
            )

            caption = result_text(
                meta
            )

            # =============================================
            # 7. Photo mode
            # =============================================

            if images:

                await processing.edit_text(
                    caption,
                    parse_mode="HTML",
                )

                await send_photos(
                    context.bot,
                    update.effective_chat.id,
                    images,
                )

                # لا نرجع هنا لأن بعض استجابات
                # TikTok قد تحتوي صور + فيديو.
                if not videos:
                    return

            # =============================================
            # 8. Download CDN video
            # =============================================

            video_path = None

            for video_url in videos[:5]:

                video_path = (
                    await download_direct(
                        session,
                        video_url,
                        temp_dir,
                    )
                )

                if video_path:
                    break

            # =============================================
            # 9. Fallback yt-dlp
            # =============================================

            if not video_path:

                await processing.edit_text(
                    "⚡ <b>SOKO TK</b>\n\n"
                    "🔄 الطريقة الأولى لم تُرجع "
                    "ملفًا صالحًا.\n"
                    "🛠️ جاري تجربة محرك احتياطي...",
                    parse_mode="HTML",
                )

                video_path = await asyncio.to_thread(
                    ytdlp_download,
                    final_url,
                    temp_dir,
                )

            # =============================================
            # 10. Send metadata
            # =============================================

            await processing.edit_text(
                caption,
                parse_mode="HTML",
            )

            # =============================================
            # 11. Send video
            # =============================================

            video_sent = False

            if video_path:

                video_sent = (
                    await send_video_file(
                        context.bot,
                        update.effective_chat.id,
                        video_path,
                    )
                )

            # =============================================
            # 12. Audio
            # =============================================

            audio_path = None

            # إذا كان المصدر أعاد Audio URL
            for audio_url in audios[:3]:

                audio_path = (
                    await download_direct(
                        session,
                        audio_url,
                        temp_dir,
                    )
                )

                if audio_path:
                    break

            if audio_path:

                await send_audio_file(
                    context.bot,
                    update.effective_chat.id,
                    audio_path,
                )

            # =============================================
            # 13. Thumbnail
            # =============================================

            if (
                meta.get(
                    "thumbnail"
                )
                and not images
            ):

                try:

                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        photo=meta[
                            "thumbnail"
                        ],
                    )

                except Exception:
                    pass

            # =============================================
            # 14. Nothing worked
            # =============================================

            if (
                not video_sent
                and not audio_path
                and not images
            ):

                await update.message.reply_text(
                    "❌ لم يتم استخراج الوسائط.\n\n"
                    "🔍 تم تجربة:\n"
                    "• TikTok oEmbed\n"
                    "• SnapTik Token\n"
                    "• SnapTik Extract\n"
                    "• CDN/RapidCDN\n"
                    "• yt-dlp\n\n"
                    "قد تكون استجابة TikTok/SnapTik "
                    "تغيّرت أو أن هذا المنشور يحتاج "
                    "طريقة وصول مختلفة."
                )

    except Exception as exc:

        logger.exception(
            "SOKO TK error: %s",
            exc,
        )

        try:

            await processing.edit_text(
                "❌ حدث خطأ أثناء معالجة الرابط.\n\n"
                "🔄 جرّب الرابط مرة أخرى."
            )

        except Exception:
            pass

    finally:

        shutil.rmtree(
            temp_dir,
            ignore_errors=True,
        )


# =========================================================
# ERROR
# =========================================================

async def error_handler(
    update,
    context,
):

    logger.error(
        "Telegram error: %s",
        context.error,
    )


# =========================================================
# MAIN
# =========================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN غير موجود في GitHub Secrets."
        )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            callback,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            handle_tiktok,
        )
    )

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "SOKO TK started."
    )

    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
