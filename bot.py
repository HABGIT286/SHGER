# -*- coding: utf-8 -*-

import os
import re
import json
import html
import asyncio
import logging
from urllib.parse import quote, urlparse

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

# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WELCOME_IMAGE = os.getenv("JPG", "").strip()

DEV_USERNAME = "FDF01"

SNAPTIK_TOKEN_URL = "https://snaptik.app/api/token"
SNAPTIK_EXTRACT_URL = "https://snaptik.app/api/extract"

REQUEST_TIMEOUT = aiohttp.ClientTimeout(
    total=45,
    connect=15,
    sock_read=35,
)

MAX_TELEGRAM_CAPTION = 1024

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)

log = logging.getLogger("SOKO-TK")


# ============================================================
# TEXT
# ============================================================

WELCOME_TEXT = """𐚁‌⇄❮𝗦𝗢𝗞𝗢❯
╔════════════════════╗
      🌌 DOWNLOAD VIDEO TIKTOK
╚════════════════════╝

𝗦𝗢𝗞𝗢・TK 🎧 ❮❯
🎵 تنزيل صوت - فيديو - ستوري - تيكتوك
🖼️ صور ومعلومات كاملة

━━━━━━━━━━━━━━━━━━━━
👤 المستخدم: {name}
🆔 ID: <code>{user_id}</code>
━━━━━━━━━━━━━━━━━━━━

🚀 <b>طريقة الاستخدام</b>

أرسل رابط TikTok فقط، والبوت يقوم بمحاولة استخراج:
🎬 الفيديو
🎵 الصوت
🖼️ الصور
📊 معلومات المنشور
⏱️ مدة الفيديو
👤 معلومات الحساب
❤️ الإعجابات والمشاهدات والتعليقات

📌 مثال:
https://www.tiktok.com/@user/video/123456789

أرسل الرابط الآن 🚀
"""

HELP_TEXT = """𐚁‌⇄❮𝗦𝗢𝗞𝗢❯

📥 <b>طريقة استخدام البوت</b>

1️⃣ انسخ رابط منشور TikTok.
2️⃣ أرسله هنا.
3️⃣ انتظر قليلاً أثناء استخراج المنشور.
4️⃣ سيحاول البوت إرسال الفيديو والصوت والصور والمعلومات المتوفرة.

⚠️ أرسل رابط TikTok مباشر فقط.
"""

PROCESSING_TEXT = "🚀 <b>جارِ تحليل رابط TikTok...</b>\n\n⏳ لحظات ونجهز المحتوى."

ERROR_TEXT = """❌ <b>الرابط غير صالح أو لم أستطع استخراج المنشور.</b>

تأكد من:
• الرابط من TikTok.
• المنشور متاح للعامة.
• أرسلت الرابط كاملًا وليس جزءًا منه.

📌 مثال:
<code>https://www.tiktok.com/@user/video/123456789</code>
"""


# ============================================================
# KEYBOARD
# ============================================================

def main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "👨‍💻 DEV",
                url=f"https://t.me/{DEV_USERNAME}"
            )
        ],
        [
            InlineKeyboardButton("ℹ️ طريقة الاستخدام", callback_data="help")
        ]
    ])


def result_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "👨‍💻 DEV",
                url=f"https://t.me/{DEV_USERNAME}"
            )
        ]
    ])


# ============================================================
# HELPERS
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    if isinstance(value, (dict, list)):
        return ""

    value = str(value)
    value = html.unescape(value)
    return value.strip()


def first_value(data, keys):
    """
    يبحث بشكل مرن داخل JSON عن أول قيمة من المفاتيح المطلوبة.
    """
    if isinstance(data, dict):
        for key in keys:
            if key in data:
                value = data[key]

                if value not in (None, "", [], {}):
                    return value

        for value in data.values():
            found = first_value(value, keys)
            if found not in (None, "", [], {}):
                return found

    elif isinstance(data, list):
        for item in data:
            found = first_value(item, keys)
            if found not in (None, "", [], {}):
                return found

    return None


def collect_values(data, wanted_keys):
    """
    يجمع كل القيم الموجودة تحت مفاتيح معينة من JSON.
    """
    result = []

    if isinstance(data, dict):
        for key, value in data.items():

            if key.lower() in wanted_keys:
                if isinstance(value, str):
                    result.append(value)

                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, str):
                            result.append(item)

            result.extend(
                collect_values(value, wanted_keys)
            )

    elif isinstance(data, list):
        for item in data:
            result.extend(
                collect_values(item, wanted_keys)
            )

    return result


def is_url(value):
    if not isinstance(value, str):
        return False

    return value.startswith("http://") or value.startswith("https://")


def is_tiktok_url(url):
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower().split(":")[0]

        return (
            host == "tiktok.com"
            or host.endswith(".tiktok.com")
            or host == "vt.tiktok.com"
            or host == "vm.tiktok.com"
        )
    except Exception:
        return False


def normalize_tiktok_url(text):
    """
    يستخرج رابط TikTok من الرسالة حتى لو كان معه نص إضافي.
    """
    if not text:
        return None

    matches = re.findall(
        r'https?://[^\s<>"\']+',
        text
    )

    for url in matches:
        url = url.rstrip(".,!?)]}>")

        if is_tiktok_url(url):
            return url

    return None


def format_duration(value):
    if value in (None, "", 0):
        return "غير متوفر"

    try:
        seconds = float(value)

        if seconds <= 0:
            return "غير متوفر"

        seconds = int(seconds)

        minutes = seconds // 60
        secs = seconds % 60

        if minutes:
            return f"{minutes}:{secs:02d}"

        return f"{secs} ثانية"

    except Exception:
        return str(value)


def format_number(value):
    if value in (None, ""):
        return "غير متوفر"

    try:
        return f"{int(value):,}"
    except Exception:
        return str(value)


def safe_caption(text):
    if len(text) <= MAX_TELEGRAM_CAPTION:
        return text

    return text[:MAX_TELEGRAM_CAPTION - 3] + "..."


# ============================================================
# HTTP
# ============================================================

def browser_headers():
    return {
        "accept": "*/*",
        "accept-language": "ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7",
        "origin": "https://snaptik.app",
        "referer": "https://snaptik.app/",
        "user-agent": (
            "Mozilla/5.0 (Linux; Android 10; K) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/139.0.0.0 "
            "Mobile Safari/537.36"
        ),
        "x-requested-with": "XMLHttpRequest",
    }


async def get_json(session, url, **kwargs):
    async with session.get(
        url,
        timeout=REQUEST_TIMEOUT,
        **kwargs
    ) as response:

        content_type = response.headers.get(
            "content-type",
            ""
        ).lower()

        text = await response.text(errors="ignore")

        if response.status >= 400:
            raise RuntimeError(
                f"HTTP {response.status}"
            )

        if "json" in content_type:
            try:
                return json.loads(text)
            except Exception:
                pass

        try:
            return json.loads(text)
        except Exception:
            return {
                "_raw": text,
                "_status": response.status,
            }


async def post_json(session, url, **kwargs):
    async with session.post(
        url,
        timeout=REQUEST_TIMEOUT,
        **kwargs
    ) as response:

        text = await response.text(errors="ignore")

        if response.status >= 400:
            raise RuntimeError(
                f"HTTP {response.status}"
            )

        try:
            return json.loads(text)
        except Exception:
            return {
                "_raw": text,
                "_status": response.status,
            }


# ============================================================
# SNAPTik EXTRACTION
# ============================================================

async def get_snap_token(session):
    """
    يطلب Token من SnapTik.
    """
    headers = browser_headers()
    headers.update({
        "content-type": "application/json",
        "accept": "*/*",
    })

    try:
        data = await post_json(
            session,
            SNAPTIK_TOKEN_URL,
            headers=headers,
        )

        return data

    except Exception as e:
        log.warning(
            "Token request failed: %s",
            e
        )

        return None


async def extract_snaptik(session, tiktok_url):
    """
    يحاول استخدام endpoint الاستخراج.
    لا يعتمد على شكل JSON واحد فقط.
    """

    token_data = await get_snap_token(session)

    headers = browser_headers()

    # بعض إصدارات SnapTik لا تحتاج Token في query،
    # لذلك نحاول الطلب الأساسي أولاً.
    encoded = quote(
        tiktok_url,
        safe=""
    )

    url = (
        f"{SNAPTIK_EXTRACT_URL}"
        f"?url={encoded}"
    )

    attempts = []

    # المحاولة الأولى
    attempts.append({
        "url": url,
        "headers": headers,
    })

    # إذا كان token عبارة عن نص أو dict نضيفه بعدة أشكال مرنة.
    if token_data:

        token_candidates = []

        if isinstance(token_data, str):
            token_candidates.append(token_data)

        elif isinstance(token_data, dict):
            for key in (
                "token",
                "data",
                "key",
                "value",
                "access_token",
            ):
                value = token_data.get(key)

                if isinstance(value, str):
                    token_candidates.append(value)

                elif isinstance(value, dict):
                    for nested_key in (
                        "token",
                        "value",
                        "key",
                    ):
                        nested = value.get(nested_key)

                        if isinstance(nested, str):
                            token_candidates.append(nested)

        for token in token_candidates:
            attempts.append({
                "url": url,
                "headers": {
                    **headers,
                    "x-token": token,
                },
            })

    last_error = None

    for attempt in attempts:

        try:
            data = await get_json(
                session,
                attempt["url"],
                headers=attempt["headers"],
            )

            if data:
                return data

        except Exception as e:
            last_error = e

    if last_error:
        raise last_error

    return None


# ============================================================
# FIND MEDIA
# ============================================================

VIDEO_KEYS = {
    "video",
    "play",
    "play_url",
    "playurl",
    "download",
    "download_url",
    "downloadurl",
    "hdplay",
    "hd_play",
    "video_url",
    "video_url_no_watermark",
    "nowatermark",
    "no_watermark",
    "wmplay",
}

AUDIO_KEYS = {
    "music",
    "music_url",
    "musicurl",
    "audio",
    "audio_url",
    "audio_url_list",
}

IMAGE_KEYS = {
    "image",
    "images",
    "image_url",
    "image_urls",
    "cover",
    "cover_url",
    "origin_cover",
    "origincover",
    "dynamic_cover",
}


def classify_url(url):
    lower = url.lower()

    if not is_url(url):
        return None

    if any(
        x in lower
        for x in (
            ".mp4",
            "/video/",
            "mime_type=video",
            "video_mp4",
        )
    ):
        return "video"

    if any(
        x in lower
        for x in (
            ".mp3",
            ".m4a",
            ".aac",
            ".wav",
            "audio",
        )
    ):
        return "audio"

    if any(
        x in lower
        for x in (
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".avif",
            "/image",
        )
    ):
        return "image"

    return None


def collect_media(data):
    videos = []
    audios = []
    images = []

    def add_unique(target, value):
        if (
            isinstance(value, str)
            and is_url(value)
            and value not in target
        ):
            target.append(value)

    # 1. القيم الموجودة تحت مفاتيح معروفة
    for key in VIDEO_KEYS:
        values = collect_values(
            data,
            {key}
        )

        for value in values:
            kind = classify_url(value)

            if kind == "video" or (
                "video" in key
                or "play" in key
                or "download" in key
                or "nowatermark" in key
            ):
                add_unique(videos, value)

    for key in AUDIO_KEYS:
        values = collect_values(
            data,
            {key}
        )

        for value in values:
            kind = classify_url(value)

            if kind == "audio" or "music" in key or "audio" in key:
                add_unique(audios, value)

    # الصور
    def recursive_images(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():

                key_l = key.lower()

                if (
                    key_l in IMAGE_KEYS
                    or "cover" in key_l
                    or "image" in key_l
                ):
                    if isinstance(value, str):
                        add_unique(images, value)

                    elif isinstance(value, list):
                        for item in value:
                            if isinstance(item, str):
                                add_unique(images, item)

                recursive_images(value)

        elif isinstance(obj, list):
            for item in obj:
                recursive_images(item)

    recursive_images(data)

    # 2. اجمع أي URL ظاهر بالـJSON كخطة احتياطية
    all_strings = []

    def gather_strings(obj):
        if isinstance(obj, dict):
            for value in obj.values():
                gather_strings(value)

        elif isinstance(obj, list):
            for value in obj:
                gather_strings(value)

        elif isinstance(obj, str):
            all_strings.append(obj)

    gather_strings(data)

    for value in all_strings:

        if not is_url(value):
            continue

        kind = classify_url(value)

        if kind == "video":
            add_unique(videos, value)

        elif kind == "audio":
            add_unique(audios, value)

        elif kind == "image":
            add_unique(images, value)

    return {
        "videos": videos,
        "audios": audios,
        "images": images,
    }


# ============================================================
# METADATA
# ============================================================

def extract_metadata(data):
    title = first_value(
        data,
        {
            "title",
            "desc",
            "description",
            "caption",
            "text",
        }
    )

    author = first_value(
        data,
        {
            "author",
            "author_name",
            "nickname",
            "unique_id",
            "username",
            "user_name",
        }
    )

    username = first_value(
        data,
        {
            "unique_id",
            "username",
            "user_name",
        }
    )

    duration = first_value(
        data,
        {
            "duration",
            "duration_sec",
            "duration_seconds",
        }
    )

    views = first_value(
        data,
        {
            "play_count",
            "playcount",
            "views",
            "view_count",
        }
    )

    likes = first_value(
        data,
        {
            "digg_count",
            "like_count",
            "likes",
        }
    )

    comments = first_value(
        data,
        {
            "comment_count",
            "comments",
        }
    )

    shares = first_value(
        data,
        {
            "share_count",
            "shares",
        }
    )

    video_id = first_value(
        data,
        {
            "id",
            "video_id",
        }
    )

    return {
        "title": clean_text(title),
        "author": clean_text(author),
        "username": clean_text(username),
        "duration": duration,
        "views": views,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "video_id": clean_text(video_id),
    }


def metadata_caption(meta):
    lines = [
        "╔════════════════════╗",
        "       🚀 SOKO・TK",
        "╚════════════════════╝",
        "",
    ]

    if meta["title"]:
        lines.append(
            f"📝 <b>الوصف:</b> {html.escape(meta['title'])}"
        )

    if meta["author"]:
        lines.append(
            f"👤 <b>الناشر:</b> {html.escape(meta['author'])}"
        )

    if meta["username"]:
        lines.append(
            f"🔗 <b>Username:</b> @{html.escape(meta['username'].lstrip('@'))}"
        )

    lines.append(
        f"⏱️ <b>المدة:</b> {format_duration(meta['duration'])}"
    )

    lines.append(
        f"👁️ <b>المشاهدات:</b> {format_number(meta['views'])}"
    )

    lines.append(
        f"❤️ <b>الإعجابات:</b> {format_number(meta['likes'])}"
    )

    lines.append(
        f"💬 <b>التعليقات:</b> {format_number(meta['comments'])}"
    )

    lines.append(
        f"🔁 <b>المشاركات:</b> {format_number(meta['shares'])}"
    )

    return "\n".join(lines)


# ============================================================
# DOWNLOAD / SEND MEDIA
# ============================================================

async def send_url_as_video(
    bot,
    chat_id,
    url,
    caption=None,
):
    try:
        await bot.send_video(
            chat_id=chat_id,
            video=url,
            caption=caption,
            parse_mode="HTML",
            supports_streaming=True,
            read_timeout=45,
            write_timeout=45,
            connect_timeout=20,
        )

        return True

    except Exception as e:
        log.warning(
            "send_video failed: %s",
            e
        )

        return False


async def send_url_as_audio(
    bot,
    chat_id,
    url,
):
    try:
        await bot.send_audio(
            chat_id=chat_id,
            audio=url,
            read_timeout=45,
            write_timeout=45,
            connect_timeout=20,
        )

        return True

    except Exception as e:
        log.warning(
            "send_audio failed: %s",
            e
        )

        return False


async def send_image(
    bot,
    chat_id,
    url,
):
    try:
        await bot.send_photo(
            chat_id=chat_id,
            photo=url,
            read_timeout=45,
            write_timeout=45,
            connect_timeout=20,
        )

        return True

    except Exception as e:
        log.warning(
            "send_photo failed: %s",
            e
        )

        return False


async def send_images(
    bot,
    chat_id,
    images,
):
    """
    يرسل الصور كـ media group عندما تكون عدة صور.
    """
    if not images:
        return 0

    sent = 0

    # Telegram يسمح بحد أقصى 10 عناصر في media group.
    for start in range(0, len(images), 10):

        batch = images[start:start + 10]

        if len(batch) == 1:
            if await send_image(
                bot,
                chat_id,
                batch[0]
            ):
                sent += 1

            continue

        try:
            from telegram import InputMediaPhoto

            media = [
                InputMediaPhoto(media=url)
                for url in batch
            ]

            await bot.send_media_group(
                chat_id=chat_id,
                media=media,
                read_timeout=45,
                write_timeout=45,
                connect_timeout=20,
            )

            sent += len(batch)

        except Exception as e:
            log.warning(
                "media group failed: %s",
                e
            )

            for url in batch:
                if await send_image(
                    bot,
                    chat_id,
                    url
                ):
                    sent += 1

    return sent


# ============================================================
# HANDLERS
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    name = html.escape(
        user.full_name or "User"
    )

    text = WELCOME_TEXT.format(
        name=name,
        user_id=user.id,
    )

    if WELCOME_IMAGE:

        try:
            await update.message.reply_photo(
                photo=WELCOME_IMAGE,
                caption=text,
                parse_mode="HTML",
                reply_markup=main_keyboard(),
            )

            return

        except Exception as e:
            log.warning(
                "Welcome image failed: %s",
                e
            )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )


async def help_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    await query.answer()

    await query.message.reply_text(
        HELP_TEXT,
        parse_mode="HTML",
        reply_markup=result_keyboard(),
    )


async def unknown_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text(
        "👋 أهلاً بك!\n\n"
        "📥 أرسل رابط منشور TikTok حتى أبدأ الاستخراج.\n\n"
        "مثال:\n"
        "<code>https://www.tiktok.com/@user/video/123456789</code>",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )


async def process_tiktok(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    message = update.message

    if not message or not message.text:
        return

    tiktok_url = normalize_tiktok_url(
        message.text
    )

    if not tiktok_url:
        await message.reply_text(
            "⚠️ <b>هذا ليس رابط TikTok صحيحاً.</b>\n\n"
            "📌 أرسل رابط المنشور مباشرة وسأقوم بالباقي 🚀",
            parse_mode="HTML",
            reply_markup=main_keyboard(),
        )
        return

    processing = await message.reply_text(
        PROCESSING_TEXT,
        parse_mode="HTML",
    )

    await context.bot.send_chat_action(
        chat_id=message.chat_id,
        action=ChatAction.TYPING,
    )

    try:
        headers = {
            "user-agent": (
                "Mozilla/5.0 (Linux; Android 10; K) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/139.0.0.0 "
                "Mobile Safari/537.36"
            )
        }

        connector = aiohttp.TCPConnector(
            ssl=False,
            limit=20,
        )

        async with aiohttp.ClientSession(
            connector=connector,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        ) as session:

            data = await extract_snaptik(
                session,
                tiktok_url,
            )

        if not data:
            raise RuntimeError(
                "Empty extraction response"
            )

        media = collect_media(data)
        meta = extract_metadata(data)

        videos = media["videos"]
        audios = media["audios"]
        images = media["images"]

        log.info(
            "Result: videos=%s audios=%s images=%s",
            len(videos),
            len(audios),
            len(images),
        )

        if not videos and not audios and not images:
            raise RuntimeError(
                "No media URLs found"
            )

        try:
            await processing.delete()
        except Exception:
            pass

        # ----------------------------------------------------
        # INFORMATION
        # ----------------------------------------------------

        info = metadata_caption(meta)

        await message.reply_text(
            info,
            parse_mode="HTML",
            reply_markup=result_keyboard(),
            disable_web_page_preview=True,
        )

        # ----------------------------------------------------
        # VIDEO
        # ----------------------------------------------------

        sent_video = False

        for video_url in videos[:3]:

            await context.bot.send_chat_action(
                chat_id=message.chat_id,
                action=ChatAction.UPLOAD_VIDEO,
            )

            ok = await send_url_as_video(
                context.bot,
                message.chat_id,
                video_url,
            )

            if ok:
                sent_video = True

        # ----------------------------------------------------
        # AUDIO
        # ----------------------------------------------------

        for audio_url in audios[:3]:

            await context.bot.send_chat_action(
                chat_id=message.chat_id,
                action=ChatAction.UPLOAD_AUDIO,
            )

            await send_url_as_audio(
                context.bot,
                message.chat_id,
                audio_url,
            )

        # ----------------------------------------------------
        # IMAGES
        # ----------------------------------------------------

        if images:
            await context.bot.send_chat_action(
                chat_id=message.chat_id,
                action=ChatAction.UPLOAD_PHOTO,
            )

            await send_images(
                context.bot,
                message.chat_id,
                images[:20],
            )

        # ----------------------------------------------------
        # FALLBACK
        # ----------------------------------------------------

        if not sent_video and not audios and not images:
            await message.reply_text(
                "⚠️ تم استخراج المنشور، لكن Telegram لم يستطع "
                "استلام ملف الوسائط من المصدر حالياً.",
                parse_mode="HTML",
                reply_markup=main_keyboard(),
            )

    except asyncio.TimeoutError:

        try:
            await processing.edit_text(
                "⏱️ <b>انتهت مهلة الاتصال.</b>\n\n"
                "حاول إرسال الرابط مرة ثانية بعد قليل 🚀",
                parse_mode="HTML",
                reply_markup=main_keyboard(),
            )
        except Exception:
            pass

    except Exception as e:

        log.exception(
            "TikTok processing failed: %s",
            e
        )

        try:
            await processing.edit_text(
                ERROR_TEXT,
                parse_mode="HTML",
                reply_markup=main_keyboard(),
            )
        except Exception:
            pass


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):
    log.exception(
        "Unhandled Telegram error",
        exc_info=context.error,
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is missing. "
            "Add BOT_TOKEN to GitHub Actions Secrets."
        )

    if not WELCOME_IMAGE:
        log.warning(
            "JPG secret is empty. "
            "The bot will send the welcome message without image."
        )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            help_callback,
            pattern="^help$"
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            process_tiktok,
        )
    )

    application.add_handler(
        MessageHandler(
            ~filters.TEXT & ~filters.COMMAND,
            unknown_message,
        )
    )

    application.add_error_handler(
        error_handler
    )

    log.info(
        "SOKO TK is starting..."
    )

    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
