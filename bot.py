# -*- coding: utf-8 -*-

import os
import re
import json
import html
import asyncio
import logging
from urllib.parse import urlparse, urlencode

import aiohttp

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
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
# TikTok Downloader Bot
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WELCOME_IMAGE = os.getenv("JPG", "").strip()

DEV_USERNAME = "FDF01"

SNAPTIK_TOKEN_URL = "https://snaptik.app/api/token"
SNAPTIK_EXTRACT_URL = "https://snaptik.app/api/extract"

REQUEST_TIMEOUT = aiohttp.ClientTimeout(
    total=60,
    connect=20,
    sock_read=45,
)

MAX_PHOTOS = 20

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("SOKO-TK")


# =========================================================
# HTTP HEADERS
# =========================================================

def browser_headers(referer=None):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 15) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/140.0.0.0 Mobile Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,image/avif,image/webp,"
            "image/apng,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }

    if referer:
        headers["Referer"] = referer

    return headers


# =========================================================
# TELEGRAM UI
# =========================================================

def main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "👨‍💻 DEV",
                url=f"https://t.me/{DEV_USERNAME}"
            )
        ],
        [
            InlineKeyboardButton(
                "ℹ️ طريقة الاستخدام",
                callback_data="help"
            )
        ],
    ])


def welcome_text(user):
    name = html.escape(user.full_name or "User")

    return (
        "𐚁‌⇄❮𝗦𝗢𝗞𝗢❯\n"
        "╔════════════════════╗\n"
        "      🌌 DOWNLOAD VIDEO TIKTOK\n"
        "╚════════════════════╝\n\n"
        "𝗦𝗢𝗞𝗢・TK 🎧 ❮❯\n"
        "🎵 تنزيل صوت - فيديو - ستوري - تيكتوك\n"
        "🖼️ صور ومعلومات كاملة\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 الاسم: {name}\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📥 أرسل الآن رابط منشور TikTok\n"
        "وسأحاول استخراج الفيديو والمعلومات "
        "والصور والصوت المتاح."
    )


# =========================================================
# URL EXTRACTION
# =========================================================

TIKTOK_HOSTS = {
    "tiktok.com",
    "www.tiktok.com",
    "m.tiktok.com",
    "vm.tiktok.com",
    "vt.tiktok.com",
}


def extract_url(text):
    if not text:
        return None

    pattern = r"https?://[^\s<>\"]+"

    matches = re.findall(pattern, text.strip())

    if not matches:
        return None

    for url in matches:
        url = url.rstrip(".,!?؛،)>]}")

        try:
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()

            if (
                host in TIKTOK_HOSTS
                or host.endswith(".tiktok.com")
            ):
                return url

        except Exception:
            continue

    return None


def is_tiktok_url(url):
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()

        return (
            host in TIKTOK_HOSTS
            or host.endswith(".tiktok.com")
        )

    except Exception:
        return False


# =========================================================
# RESOLVE SHORT TIKTOK URL
# =========================================================

async def resolve_tiktok_url(session, original_url):
    """
    يحل:
        vt.tiktok.com/...
        vm.tiktok.com/...
    إلى الرابط النهائي لمنشور TikTok.

    لا نسمح بالتحويل إلى دومين غير TikTok.
    """

    if not is_tiktok_url(original_url):
        return None

    parsed = urlparse(original_url)
    host = (parsed.hostname or "").lower()

    # الرابط الكامل لا يحتاج Resolve
    if host not in {
        "vt.tiktok.com",
        "vm.tiktok.com",
    }:
        return original_url

    logger.info("Resolving TikTok short URL: %s", original_url)

    try:
        async with session.get(
            original_url,
            headers=browser_headers(),
            allow_redirects=True,
        ) as response:

            final_url = str(response.url)

            logger.info(
                "TikTok redirect: %s -> %s",
                original_url,
                final_url
            )

            if not is_tiktok_url(final_url):
                logger.warning(
                    "Redirect destination is not TikTok: %s",
                    final_url
                )
                return None

            return final_url

    except Exception as exc:
        logger.warning(
            "Short URL resolve failed: %s",
            exc
        )

    # محاولة ثانية عبر HEAD
    try:
        async with session.head(
            original_url,
            headers=browser_headers(),
            allow_redirects=True,
        ) as response:

            final_url = str(response.url)

            if is_tiktok_url(final_url):
                logger.info(
                    "TikTok HEAD redirect: %s -> %s",
                    original_url,
                    final_url
                )
                return final_url

    except Exception as exc:
        logger.warning(
            "HEAD resolve failed: %s",
            exc
        )

    return None


# =========================================================
# CANONICAL TIKTOK URL
# =========================================================

def clean_tiktok_url(url):
    """
    يحتفظ بالرابط الأساسي للمنشور.
    نزيل معاملات التتبع الشائعة فقط.
    """

    try:
        parsed = urlparse(url)

        host = (parsed.hostname or "").lower()

        if host not in {
            "www.tiktok.com",
            "tiktok.com",
            "m.tiktok.com",
        }:
            return url

        path = parsed.path or "/"

        # معاملات مشاركة/تتبع شائعة.
        # إذا كان الرابط يحتوي معاملات أخرى مهمة
        # نتركها كما هي.
        query = parsed.query

        if query:
            params = []

            for item in query.split("&"):
                if not item:
                    continue

                key = item.split("=", 1)[0].lower()

                if key in {
                    "_t",
                    "_r",
                    "refer",
                    "tt_from",
                    "tt_ref",
                    "is_from_webapp",
                    "sender_device",
                    "sender_web_id",
                }:
                    continue

                params.append(item)

            query = "&".join(params)

        result = "https://www.tiktok.com" + path

        if query:
            result += "?" + query

        return result

    except Exception:
        return url


# =========================================================
# SNAP TIK TOKEN
# =========================================================

async def get_snap_token(session):
    try:
        async with session.post(
            SNAPTIK_TOKEN_URL,
            headers={
                **browser_headers(),
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": "https://snaptik.app",
                "Referer": "https://snaptik.app/",
            },
            data={},
        ) as response:

            text = await response.text()

            logger.info(
                "SnapTik token status: %s",
                response.status
            )

            if response.status >= 400:
                return None

            try:
                data = json.loads(text)
            except Exception:
                return None

            if isinstance(data, dict):
                for key in (
                    "token",
                    "data",
                    "key",
                    "x-token",
                    "x_token",
                ):
                    value = data.get(key)

                    if isinstance(value, str) and value.strip():
                        return value.strip()

                    if isinstance(value, dict):
                        for subkey in (
                            "token",
                            "key",
                            "value",
                        ):
                            subvalue = value.get(subkey)

                            if (
                                isinstance(subvalue, str)
                                and subvalue.strip()
                            ):
                                return subvalue.strip()

    except Exception as exc:
        logger.warning(
            "SnapTik token error: %s",
            exc
        )

    return None


# =========================================================
# SNAP TIK EXTRACT
# =========================================================

async def extract_snaptik(session, tiktok_url):
    """
    يحاول أكثر من شكل للطلب لأن استجابة SnapTik
    قد تتغير من وقت لآخر.
    """

    token = await get_snap_token(session)

    attempts = []

    # المحاولة الأساسية
    attempts.append({
        "url": SNAPTIK_EXTRACT_URL,
        "params": {
            "url": tiktok_url
        },
    })

    # محاولة x-token
    if token:
        attempts.append({
            "url": SNAPTIK_EXTRACT_URL,
            "params": {
                "url": tiktok_url,
                "x-token": token,
            },
        })

        attempts.append({
            "url": SNAPTIK_EXTRACT_URL,
            "params": {
                "url": tiktok_url,
                "token": token,
            },
        })

    for attempt in attempts:

        try:
            headers = {
                **browser_headers(
                    "https://snaptik.app/"
                ),
                "Accept": "application/json,text/plain,*/*",
                "X-Requested-With": "XMLHttpRequest",
            }

            if token:
                headers["X-Token"] = token

            async with session.get(
                attempt["url"],
                params=attempt["params"],
                headers=headers,
                allow_redirects=True,
            ) as response:

                text = await response.text()

                logger.info(
                    "SnapTik extract status: %s",
                    response.status
                )

                if response.status >= 400:
                    continue

                try:
                    data = json.loads(text)
                except Exception:
                    logger.warning(
                        "SnapTik returned non-JSON response"
                    )
                    continue

                if data:
                    return data

        except Exception as exc:
            logger.warning(
                "SnapTik extract attempt failed: %s",
                exc
            )

    return None


# =========================================================
# JSON WALKER
# =========================================================

def walk_json(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key, item

            yield from walk_json(item)

    elif isinstance(value, list):
        for item in value:
            yield from walk_json(item)


# =========================================================
# URL CLASSIFICATION
# =========================================================

IMAGE_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
)

VIDEO_EXTENSIONS = (
    ".mp4",
    ".m3u8",
    ".mov",
)

AUDIO_EXTENSIONS = (
    ".mp3",
    ".m4a",
    ".aac",
    ".wav",
)


def looks_like_url(value):
    if not isinstance(value, str):
        return False

    return (
        value.startswith("http://")
        or value.startswith("https://")
    )


def classify_url(url):
    lower = url.lower()

    if any(
        ext in lower
        for ext in VIDEO_EXTENSIONS
    ):
        return "video"

    if any(
        ext in lower
        for ext in AUDIO_EXTENSIONS
    ):
        return "audio"

    if any(
        ext in lower
        for ext in IMAGE_EXTENSIONS
    ):
        return "image"

    if "video" in lower:
        return "video"

    if "audio" in lower:
        return "audio"

    if (
        "music" in lower
        or "playurl" in lower
        or "download" in lower
    ):
        return "video"

    if (
        "image" in lower
        or "cover" in lower
        or "photo" in lower
    ):
        return "image"

    return None


# =========================================================
# MEDIA EXTRACTION
# =========================================================

def extract_media(data):
    videos = []
    audios = []
    images = []

    seen = set()

    preferred_video_keys = {
        "video",
        "video_url",
        "video_url_no_watermark",
        "download_url",
        "download",
        "play",
        "play_url",
        "playurl",
        "nowatermark",
        "nowatermark_url",
        "hdplay",
        "hdplay_url",
    }

    preferred_audio_keys = {
        "audio",
        "audio_url",
        "music",
        "music_url",
        "music_play_url",
        "sound",
    }

    preferred_image_keys = {
        "image",
        "images",
        "image_url",
        "image_urls",
        "cover",
        "cover_url",
        "origin_cover",
        "origin_cover_url",
        "dynamic_cover",
        "thumbnail",
        "thumbnails",
        "photo",
        "photos",
    }

    def add_url(target, value):
        if not isinstance(value, str):
            return

        value = value.strip()

        if not looks_like_url(value):
            return

        if value in seen:
            return

        seen.add(value)
        target.append(value)

    # أولاً: المفاتيح المعروفة
    for key, value in walk_json(data):

        key_lower = str(key).lower()

        if isinstance(value, str):

            if key_lower in preferred_video_keys:
                add_url(videos, value)

            elif key_lower in preferred_audio_keys:
                add_url(audios, value)

            elif key_lower in preferred_image_keys:
                add_url(images, value)

        elif isinstance(value, list):

            for item in value:
                if isinstance(item, str):

                    if key_lower in preferred_video_keys:
                        add_url(videos, item)

                    elif key_lower in preferred_audio_keys:
                        add_url(audios, item)

                    elif key_lower in preferred_image_keys:
                        add_url(images, item)

    # ثانياً: فحص جميع النصوص
    def scan_all(value):

        if isinstance(value, dict):
            for item in value.values():
                yield from scan_all(item)

        elif isinstance(value, list):
            for item in value:
                yield from scan_all(item)

        elif isinstance(value, str):
            yield value

    for value in scan_all(data):

        if not looks_like_url(value):
            continue

        kind = classify_url(value)

        if kind == "video":
            add_url(videos, value)

        elif kind == "audio":
            add_url(audios, value)

        elif kind == "image":
            add_url(images, value)

    return {
        "videos": videos,
        "audios": audios,
        "images": images,
    }


# =========================================================
# METADATA EXTRACTION
# =========================================================

def first_value(data, keys):
    keys = {
        str(key).lower()
        for key in keys
    }

    for key, value in walk_json(data):

        if str(key).lower() not in keys:
            continue

        if isinstance(value, (str, int, float)):
            text = str(value).strip()

            if text:
                return text

    return None


def format_number(value):
    if not value:
        return "غير متوفر"

    try:
        number = int(float(value))

        if number >= 1_000_000_000:
            return f"{number / 1_000_000_000:.1f}B"

        if number >= 1_000_000:
            return f"{number / 1_000_000:.1f}M"

        if number >= 1_000:
            return f"{number / 1_000:.1f}K"

        return f"{number:,}"

    except Exception:
        return str(value)


def format_duration(value):
    if not value:
        return "غير متوفر"

    try:
        seconds = int(float(value))

        minutes = seconds // 60
        seconds = seconds % 60

        return f"{minutes:02d}:{seconds:02d}"

    except Exception:
        return str(value)


def extract_metadata(data):
    title = first_value(
        data,
        {
            "title",
            "desc",
            "description",
            "caption",
            "text",
        },
    )

    author = first_value(
        data,
        {
            "author",
            "author_name",
            "nickname",
            "display_name",
            "creator",
        },
    )

    username = first_value(
        data,
        {
            "username",
            "unique_id",
            "uniqueid",
            "user_name",
            "author_unique_id",
        },
    )

    duration = first_value(
        data,
        {
            "duration",
            "duration_ms",
            "video_duration",
        },
    )

    views = first_value(
        data,
        {
            "views",
            "view_count",
            "play_count",
            "playcount",
        },
    )

    likes = first_value(
        data,
        {
            "likes",
            "like_count",
            "digg_count",
        },
    )

    comments = first_value(
        data,
        {
            "comments",
            "comment_count",
        },
    )

    shares = first_value(
        data,
        {
            "shares",
            "share_count",
        },
    )

    video_id = first_value(
        data,
        {
            "video_id",
            "aweme_id",
            "id",
        },
    )

    return {
        "title": title or "غير متوفر",
        "author": author or "غير متوفر",
        "username": username or "غير متوفر",
        "duration": format_duration(duration),
        "views": format_number(views),
        "likes": format_number(likes),
        "comments": format_number(comments),
        "shares": format_number(shares),
        "video_id": video_id or "غير متوفر",
    }


# =========================================================
# RESULT MESSAGE
# =========================================================

def build_result_message(meta, original_url, final_url):
    title = html.escape(str(meta["title"]))
    author = html.escape(str(meta["author"]))
    username = html.escape(str(meta["username"]))
    duration = html.escape(str(meta["duration"]))
    views = html.escape(str(meta["views"]))
    likes = html.escape(str(meta["likes"]))
    comments = html.escape(str(meta["comments"]))
    shares = html.escape(str(meta["shares"]))

    return (
        "╔════════════════════╗\n"
        "      🌌 SOKO TK RESULT\n"
        "╚════════════════════╝\n\n"
        f"👤 الاسم: {author}\n"
        f"🔖 المستخدم: @{username.lstrip('@')}\n"
        f"⏱️ المدة: {duration}\n"
        f"👁️ المشاهدات: {views}\n"
        f"❤️ الإعجابات: {likes}\n"
        f"💬 التعليقات: {comments}\n"
        f"🔁 المشاركات: {shares}\n\n"
        f"📝 الوصف:\n{title}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🚀 تم استخراج المنشور."
    )


# =========================================================
# SEND MEDIA
# =========================================================

async def send_images(bot, chat_id, images):
    if not images:
        return False

    images = images[:MAX_PHOTOS]

    # Telegram media groups تسمح بحد أقصى 10 عناصر
    for start in range(0, len(images), 10):

        chunk = images[start:start + 10]

        if len(chunk) == 1:
            try:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=chunk[0],
                )
                continue
            except Exception as exc:
                logger.warning(
                    "send_photo failed: %s",
                    exc
                )

        media = []

        from telegram import InputMediaPhoto

        for image_url in chunk:
            media.append(
                InputMediaPhoto(
                    media=image_url
                )
            )

        try:
            await bot.send_media_group(
                chat_id=chat_id,
                media=media,
            )

        except Exception as exc:
            logger.warning(
                "send_media_group failed: %s",
                exc
            )

            for image_url in chunk:
                try:
                    await bot.send_photo(
                        chat_id=chat_id,
                        photo=image_url,
                    )
                except Exception:
                    pass

    return True


async def send_videos(bot, chat_id, videos):
    if not videos:
        return False

    for video_url in videos[:3]:

        try:
            await bot.send_chat_action(
                chat_id=chat_id,
                action=ChatAction.UPLOAD_VIDEO,
            )

            await bot.send_video(
                chat_id=chat_id,
                video=video_url,
                supports_streaming=True,
                read_timeout=60,
                write_timeout=60,
                connect_timeout=30,
            )

            return True

        except Exception as exc:
            logger.warning(
                "send_video failed: %s",
                exc
            )

    return False


async def send_audios(bot, chat_id, audios):
    if not audios:
        return False

    for audio_url in audios[:3]:

        try:
            await bot.send_chat_action(
                chat_id=chat_id,
                action=ChatAction.UPLOAD_AUDIO,
            )

            await bot.send_audio(
                chat_id=chat_id,
                audio=audio_url,
                read_timeout=60,
                write_timeout=60,
                connect_timeout=30,
            )

            return True

        except Exception as exc:
            logger.warning(
                "send_audio failed: %s",
                exc
            )

    return False


# =========================================================
# COMMANDS
# =========================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return

    user = update.effective_user

    await update.message.reply_text(
        welcome_text(user),
        parse_mode="HTML",
        reply_markup=main_keyboard(),
        disable_web_page_preview=True,
    )


# =========================================================
# HELP BUTTON
# =========================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if not query:
        return

    await query.answer()

    if query.data == "help":

        text = (
            "╔════════════════════╗\n"
            "        ℹ️ SOKO TK\n"
            "╚════════════════════╝\n\n"
            "📌 طريقة الاستخدام:\n\n"
            "1️⃣ انسخ رابط منشور TikTok.\n"
            "2️⃣ أرسله هنا كما هو.\n"
            "3️⃣ إذا كان الرابط مختصرًا مثل:\n"
            "   vt.tiktok.com أو vm.tiktok.com\n"
            "   سيحاول البوت تحويله تلقائيًا.\n"
            "4️⃣ بعد ذلك يحاول استخراج:\n"
            "   🎬 الفيديو\n"
            "   🎵 الصوت\n"
            "   🖼️ الصور\n"
            "   📊 معلومات المنشور\n\n"
            "⚠️ يجب أن يكون المنشور متاحًا للعامة."
        )

        await query.message.reply_text(
            text,
            reply_markup=main_keyboard(),
        )


# =========================================================
# TIKTOK MESSAGE
# =========================================================

async def tiktok_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message:
        return

    text = update.message.text or ""

    original_url = extract_url(text)

    if not original_url:
        await update.message.reply_text(
            "❌ ما حصلت رابط TikTok صالح.\n\n"
            "أرسل رابط المنشور كاملًا، مثل:\n"
            "https://www.tiktok.com/@user/video/123456789\n\n"
            "وتقدر أيضًا ترسل رابط TikTok المختصر مثل:\n"
            "https://vt.tiktok.com/xxxx/\n"
            "أو:\n"
            "https://vm.tiktok.com/xxxx/"
        )
        return

    if not is_tiktok_url(original_url):
        await update.message.reply_text(
            "❌ الرابط ليس من TikTok."
        )
        return

    processing = await update.message.reply_text(
        "🚀 <b>SOKO TK</b>\n\n"
        "⏳ جاري فحص الرابط...\n"
        "🔗 حل رابط TikTok\n"
        "📡 الاتصال بالمستخرج...\n\n"
        "انتظر قليلًا...",
        parse_mode="HTML",
    )

    try:

        async with aiohttp.ClientSession(
            timeout=REQUEST_TIMEOUT
        ) as session:

            # =============================================
            # STEP 1: Resolve short link
            # =============================================

            final_url = await resolve_tiktok_url(
                session,
                original_url,
            )

            if not final_url:
                await processing.edit_text(
                    "❌ لم أستطع تحويل رابط TikTok المختصر.\n\n"
                    "جرّب إرسال الرابط الكامل للمنشور."
                )
                return

            final_url = clean_tiktok_url(final_url)

            logger.info(
                "Original URL: %s",
                original_url
            )

            logger.info(
                "Final URL: %s",
                final_url
            )

            # =============================================
            # STEP 2: Extract
            # =============================================

            await processing.edit_text(
                "🚀 <b>SOKO TK</b>\n\n"
                "✅ تم التعرف على رابط TikTok.\n"
                "📡 جاري استخراج بيانات المنشور...\n"
                "🎬 البحث عن الفيديو والصور والصوت...",
                parse_mode="HTML",
            )

            data = await extract_snaptik(
                session,
                final_url,
            )

            if not data:

                await processing.edit_text(
                    "❌ الرابط وصل إلى TikTok بنجاح، "
                    "لكن المستخرج لم يُرجع بيانات للمنشور.\n\n"
                    "قد يكون السبب:\n"
                    "• المنشور غير متاح للعامة.\n"
                    "• TikTok منع الطلب مؤقتًا.\n"
                    "• المستخرج غيّر طريقة الاستجابة.\n\n"
                    "جرّب إرسال الرابط مرة أخرى."
                )
                return

            # =============================================
            # STEP 3: Parse
            # =============================================

            media = extract_media(data)
            metadata = extract_metadata(data)

            videos = media["videos"]
            audios = media["audios"]
            images = media["images"]

            logger.info(
                "Extracted: videos=%s audios=%s images=%s",
                len(videos),
                len(audios),
                len(images),
            )

            # =============================================
            # STEP 4: Check result
            # =============================================

            if not videos and not audios and not images:

                await processing.edit_text(
                    "❌ تم الوصول إلى المستخرج، "
                    "لكن لم يتم العثور على ملف وسائط.\n\n"
                    "قد تكون استجابة المصدر تغيرت أو أن "
                    "المنشور غير متاح حاليًا."
                )
                return

            # =============================================
            # STEP 5: Metadata
            # =============================================

            result_text = build_result_message(
                metadata,
                original_url,
                final_url,
            )

            try:
                await processing.edit_text(
                    result_text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            except Exception:
                await processing.delete()

                await update.message.reply_text(
                    result_text,
                    parse_mode="HTML",
                )

            # =============================================
            # STEP 6: Images
            # =============================================

            if images:
                await send_images(
                    context.bot,
                    update.effective_chat.id,
                    images,
                )

            # =============================================
            # STEP 7: Video
            # =============================================

            video_sent = False

            if videos:
                video_sent = await send_videos(
                    context.bot,
                    update.effective_chat.id,
                    videos,
                )

            # =============================================
            # STEP 8: Audio
            # =============================================

            if audios:
                await send_audios(
                    context.bot,
                    update.effective_chat.id,
                    audios,
                )

            # =============================================
            # Final fallback
            # =============================================

            if not video_sent and not audios and not images:

                await update.message.reply_text(
                    "⚠️ حصلت على بيانات من المصدر، "
                    "لكن Telegram لم يستطع إرسال الملف."
                )

    except asyncio.CancelledError:
        raise

    except Exception as exc:

        logger.exception(
            "Unhandled TikTok error: %s",
            exc,
        )

        try:
            await processing.edit_text(
                "❌ حدث خطأ أثناء معالجة الرابط.\n\n"
                "🔄 جرّب إرسال الرابط مرة أخرى."
            )
        except Exception:
            try:
                await update.message.reply_text(
                    "❌ حدث خطأ أثناء معالجة الرابط.\n"
                    "🔄 جرّب مرة أخرى."
                )
            except Exception:
                pass


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.exception(
        "Telegram error:",
        exc_info=context.error,
    )


# =========================================================
# MAIN
# =========================================================

def main():

    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN غير موجود. "
            "أضفه في GitHub Actions Secrets."
        )

    logger.info("Starting SOKO TK...")

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start_command,
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            callback_handler,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            tiktok_handler,
        )
    )

    application.add_error_handler(
        error_handler
    )

    logger.info("SOKO TK is running.")

    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
