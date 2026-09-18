# -*- coding: utf-8 -*-

import os
import sys
import re
import html
import asyncio
import logging
import random
import tempfile
import shutil
from pathlib import Path
from urllib.parse import urlparse

# =========================================================
# AUTO INSTALL REQUIRED PACKAGE
# =========================================================

try:
    import yt_dlp
except ImportError:
    print("Installing yt-dlp...")
    os.system(
        f'"{sys.executable}" -m pip install -U yt-dlp'
    )
    import yt_dlp


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

MAX_PHOTOS = 20
MAX_VIDEO_SIZE_MB = 49
MAX_AUDIO_SIZE_MB = 49

DOWNLOAD_TIMEOUT = 120

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
        "Mozilla/5.0 (Linux; Android 13) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/137.0.0.0 Mobile Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/139.0.0.0 Safari/537.36"
    ),
]


def random_user_agent():
    return random.choice(USER_AGENTS)


# =========================================================
# TIKTOK URL
# =========================================================

TIKTOK_DOMAINS = {
    "tiktok.com",
    "www.tiktok.com",
    "m.tiktok.com",
    "vm.tiktok.com",
    "vt.tiktok.com",
}


def is_tiktok_url(url):
    try:
        host = (urlparse(url).hostname or "").lower()

        return (
            host in TIKTOK_DOMAINS
            or host.endswith(".tiktok.com")
        )

    except Exception:
        return False


def extract_tiktok_url(text):
    if not text:
        return None

    pattern = r"https?://[^\s<>\"]+"

    for url in re.findall(pattern, text):

        url = url.rstrip(
            ".,!?؛،)>]}\"'"
        )

        if is_tiktok_url(url):
            return url

    return None


# =========================================================
# TELEGRAM KEYBOARD
# =========================================================

def main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "👨‍💻 DEV",
                url="https://t.me/FDF01"
            )
        ],
        [
            InlineKeyboardButton(
                "ℹ️ طريقة الاستخدام",
                callback_data="help"
            )
        ],
    ])


# =========================================================
# WELCOME
# =========================================================

def welcome_text(user):

    name = html.escape(
        user.full_name or "User"
    )

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
        "📥 أرسل رابط TikTok الآن\n"
        "وسأحاول استخراج الفيديو أو الصور "
        "والصوت والمعلومات المتاحة."
    )


async def send_welcome(update):

    user = update.effective_user

    if not user:
        return

    text = welcome_text(user)

    if WELCOME_IMAGE:

        try:
            await update.message.reply_photo(
                photo=WELCOME_IMAGE,
                caption=text,
                parse_mode="HTML",
                reply_markup=main_keyboard(),
            )
            return

        except Exception as exc:
            logger.warning(
                "Welcome image failed: %s",
                exc
            )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=main_keyboard(),
        disable_web_page_preview=True,
    )


# =========================================================
# /START
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await send_welcome(update)


# =========================================================
# HELP
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
            "1️⃣ انسخ رابط TikTok.\n"
            "2️⃣ أرسله إلى البوت.\n"
            "3️⃣ الروابط المختصرة مدعومة:\n"
            "   • vt.tiktok.com\n"
            "   • vm.tiktok.com\n"
            "4️⃣ يحاول البوت استخراج:\n"
            "   🎬 الفيديو\n"
            "   🎵 الصوت\n"
            "   🖼️ الصور\n"
            "   📊 المعلومات\n\n"
            "⚠️ يجب أن يكون المحتوى متاحًا "
            "للوصول العام."
        )

        await query.message.reply_text(
            text,
            reply_markup=main_keyboard(),
        )


# =========================================================
# RESOLVE SHORT URL
# =========================================================

async def resolve_short_url(url):

    import urllib.request

    if not is_tiktok_url(url):
        return None

    host = (
        urlparse(url).hostname or ""
    ).lower()

    if host not in {
        "vt.tiktok.com",
        "vm.tiktok.com",
    }:
        return url

    logger.info(
        "Resolving short URL: %s",
        url
    )

    def resolve():

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": random_user_agent(),
            },
        )

        with urllib.request.urlopen(
            request,
            timeout=25,
        ) as response:

            return response.geturl()

    try:

        final_url = await asyncio.to_thread(
            resolve
        )

        if is_tiktok_url(final_url):

            logger.info(
                "Resolved URL: %s",
                final_url
            )

            return final_url

    except Exception as exc:

        logger.warning(
            "Short URL resolve failed: %s",
            exc
        )

    return url


# =========================================================
# YT-DLP INFO
# =========================================================

def extract_info_sync(url):

    ua = random_user_agent()

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "nocheckcertificate": True,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "http_headers": {
            "User-Agent": ua,
            "Accept-Language": "en-US,en;q=0.9",
        },
    }

    with yt_dlp.YoutubeDL(options) as ydl:

        info = ydl.extract_info(
            url,
            download=False,
        )

        return info


async def extract_info(url):

    return await asyncio.to_thread(
        extract_info_sync,
        url,
    )


# =========================================================
# FORMAT INFO
# =========================================================

def safe_int(value):

    try:
        return int(value or 0)

    except Exception:
        return 0


def format_number(value):

    number = safe_int(value)

    if not number:
        return "غير متوفر"

    if number >= 1_000_000_000:
        return f"{number / 1_000_000_000:.1f}B"

    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"

    if number >= 1_000:
        return f"{number / 1_000:.1f}K"

    return f"{number:,}"


def format_duration(seconds):

    seconds = safe_int(seconds)

    if not seconds:
        return "غير متوفر"

    minutes = seconds // 60
    seconds = seconds % 60

    return f"{minutes:02d}:{seconds:02d}"


# =========================================================
# METADATA
# =========================================================

def build_metadata(info):

    title = (
        info.get("title")
        or info.get("description")
        or "غير متوفر"
    )

    uploader = (
        info.get("uploader")
        or info.get("creator")
        or "غير متوفر"
    )

    username = (
        info.get("uploader_id")
        or info.get("channel_id")
        or info.get("uploader")
        or "غير متوفر"
    )

    return {
        "title": str(title),
        "author": str(uploader),
        "username": str(username),
        "duration": format_duration(
            info.get("duration")
        ),
        "views": format_number(
            info.get("view_count")
        ),
        "likes": format_number(
            info.get("like_count")
        ),
        "comments": format_number(
            info.get("comment_count")
        ),
        "shares": format_number(
            info.get("repost_count")
            or info.get("share_count")
        ),
        "id": str(
            info.get("id")
            or "غير متوفر"
        ),
    }


def result_caption(meta):

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
        f"🔖 المستخدم: @{username.lstrip('@')}\n"
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
# PHOTO MODE
# =========================================================

def get_images(info):

    images = []

    # صور مباشرة
    for key in (
        "thumbnails",
        "images",
        "photos",
    ):

        value = info.get(key)

        if isinstance(value, list):

            for item in value:

                if isinstance(item, str):
                    images.append(item)

                elif isinstance(item, dict):

                    url = (
                        item.get("url")
                        or item.get("src")
                    )

                    if url:
                        images.append(url)

    # entries الخاصة ببعض أنواع المحتوى
    entries = info.get("entries")

    if isinstance(entries, list):

        for entry in entries:

            if not isinstance(entry, dict):
                continue

            for key in (
                "url",
                "thumbnail",
            ):

                value = entry.get(key)

                if (
                    isinstance(value, str)
                    and (
                        "image" in value
                        or "jpg" in value
                        or "jpeg" in value
                        or "png" in value
                        or "webp" in value
                    )
                ):
                    images.append(value)

    # حذف التكرار
    result = []

    seen = set()

    for image in images:

        if image in seen:
            continue

        seen.add(image)
        result.append(image)

    return result[:MAX_PHOTOS]


# =========================================================
# DOWNLOAD DIRECTORY
# =========================================================

def create_temp_directory():

    return tempfile.mkdtemp(
        prefix="soko_tk_"
    )


# =========================================================
# DOWNLOAD VIDEO
# =========================================================

def download_video_sync(
    url,
    directory,
):

    ua = random_user_agent()

    output_template = os.path.join(
        directory,
        "%(id)s.%(ext)s"
    )

    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "nocheckcertificate": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 40,

        # نحاول اختيار فيديو + صوت
        "format": (
            "bv*[ext=mp4]+ba[ext=m4a]/"
            "b[ext=mp4]/"
            "b"
        ),

        "outtmpl": output_template,

        "http_headers": {
            "User-Agent": ua,
            "Accept-Language": "en-US,en;q=0.9",
        },

        "merge_output_format": "mp4",
    }

    with yt_dlp.YoutubeDL(options) as ydl:

        info = ydl.extract_info(
            url,
            download=True,
        )

        requested = ydl.prepare_filename(
            info
        )

        candidates = [
            requested,
            os.path.splitext(
                requested
            )[0] + ".mp4",
        ]

        for candidate in candidates:

            if os.path.isfile(candidate):
                return candidate

        # البحث عن أي ملف ناتج
        for filename in os.listdir(
            directory
        ):

            path = os.path.join(
                directory,
                filename
            )

            if os.path.isfile(path):

                lower = filename.lower()

                if lower.endswith(
                    (
                        ".mp4",
                        ".webm",
                        ".mkv",
                        ".mov",
                    )
                ):
                    return path

    return None


async def download_video(
    url,
    directory,
):

    return await asyncio.to_thread(
        download_video_sync,
        url,
        directory,
    )


# =========================================================
# DOWNLOAD AUDIO
# =========================================================

def download_audio_sync(
    url,
    directory,
):

    ua = random_user_agent()

    output_template = os.path.join(
        directory,
        "%(id)s_audio.%(ext)s"
    )

    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "nocheckcertificate": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 40,

        "format": (
            "ba[ext=m4a]/"
            "ba[ext=mp3]/"
            "bestaudio/best"
        ),

        "outtmpl": output_template,

        "http_headers": {
            "User-Agent": ua,
            "Accept-Language": "en-US,en;q=0.9",
        },

        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
    }

    with yt_dlp.YoutubeDL(options) as ydl:

        info = ydl.extract_info(
            url,
            download=True,
        )

        requested = ydl.prepare_filename(
            info
        )

        base = os.path.splitext(
            requested
        )[0]

        mp3 = base + ".mp3"

        if os.path.isfile(mp3):
            return mp3

        # fallback
        for filename in os.listdir(
            directory
        ):

            path = os.path.join(
                directory,
                filename
            )

            if (
                os.path.isfile(path)
                and filename.lower().endswith(
                    (
                        ".mp3",
                        ".m4a",
                        ".aac",
                        ".opus",
                    )
                )
            ):
                return path

    return None


async def download_audio(
    url,
    directory,
):

    return await asyncio.to_thread(
        download_audio_sync,
        url,
        directory,
    )


# =========================================================
# FILE SIZE
# =========================================================

def file_size_mb(path):

    try:
        return (
            os.path.getsize(path)
            / 1024
            / 1024
        )

    except Exception:
        return 0


# =========================================================
# SEND PHOTOS
# =========================================================

async def send_photos(
    bot,
    chat_id,
    images,
):

    if not images:
        return False

    images = images[:MAX_PHOTOS]

    for start in range(
        0,
        len(images),
        10,
    ):

        chunk = images[
            start:start + 10
        ]

        if len(chunk) == 1:

            try:

                await bot.send_photo(
                    chat_id=chat_id,
                    photo=chunk[0],
                )

                continue

            except Exception as exc:

                logger.warning(
                    "Photo send failed: %s",
                    exc
                )

        media = []

        for image in chunk:

            media.append(
                InputMediaPhoto(
                    media=image
                )
            )

        try:

            await bot.send_media_group(
                chat_id=chat_id,
                media=media,
            )

        except Exception as exc:

            logger.warning(
                "Media group failed: %s",
                exc
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
# SEND VIDEO
# =========================================================

async def send_video_file(
    bot,
    chat_id,
    path,
    caption,
):

    size = file_size_mb(path)

    if size > MAX_VIDEO_SIZE_MB:

        return False

    try:

        await bot.send_chat_action(
            chat_id=chat_id,
            action=ChatAction.UPLOAD_VIDEO,
        )

        with open(
            path,
            "rb"
        ) as video:

            await bot.send_video(
                chat_id=chat_id,
                video=video,
                caption=caption,
                parse_mode="HTML",
                supports_streaming=True,
                read_timeout=120,
                write_timeout=120,
                connect_timeout=60,
            )

        return True

    except Exception as exc:

        logger.warning(
            "Video upload failed: %s",
            exc
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

    size = file_size_mb(path)

    if size > MAX_AUDIO_SIZE_MB:

        return False

    try:

        await bot.send_chat_action(
            chat_id=chat_id,
            action=ChatAction.UPLOAD_AUDIO,
        )

        with open(
            path,
            "rb"
        ) as audio:

            await bot.send_audio(
                chat_id=chat_id,
                audio=audio,
                title="SOKO TK",
                performer="SOKO",
                read_timeout=120,
                write_timeout=120,
                connect_timeout=60,
            )

        return True

    except Exception as exc:

        logger.warning(
            "Audio upload failed: %s",
            exc
        )

        return False


# =========================================================
# MAIN TIKTOK HANDLER
# =========================================================

async def tiktok_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.message:
        return

    text = update.message.text or ""

    original_url = extract_tiktok_url(
        text
    )

    if not original_url:

        await update.message.reply_text(
            "❌ أرسل رابط TikTok صالح.\n\n"
            "مثال:\n"
            "https://www.tiktok.com/@user/video/123456789\n\n"
            "وتقدر أيضًا ترسل:\n"
            "https://vt.tiktok.com/...\n"
            "أو:\n"
            "https://vm.tiktok.com/..."
        )

        return

    processing = await update.message.reply_text(
        "🚀 <b>SOKO TK</b>\n\n"
        "🔗 تم استلام الرابط.\n"
        "⏳ جاري التحقق من TikTok...\n"
        "📡 انتظر قليلًا...",
        parse_mode="HTML",
    )

    temp_dir = None

    try:

        # =================================================
        # STEP 1 — Resolve short URL
        # =================================================

        final_url = await resolve_short_url(
            original_url
        )

        if not final_url:

            await processing.edit_text(
                "❌ لم أستطع تحويل رابط TikTok."
            )

            return

        logger.info(
            "Original: %s",
            original_url
        )

        logger.info(
            "Final: %s",
            final_url
        )

        await processing.edit_text(
            "🚀 <b>SOKO TK</b>\n\n"
            "✅ تم التعرف على الرابط.\n"
            "🔍 جاري قراءة بيانات المنشور...",
            parse_mode="HTML",
        )

        # =================================================
        # STEP 2 — Extract metadata
        # =================================================

        info = None
        last_error = None

        for attempt in range(1, 4):

            try:

                logger.info(
                    "yt-dlp extraction attempt %s",
                    attempt
                )

                info = await extract_info(
                    final_url
                )

                if info:
                    break

            except Exception as exc:

                last_error = exc

                logger.warning(
                    "Extraction attempt %s failed: %s",
                    attempt,
                    exc
                )

                await asyncio.sleep(
                    attempt
                )

        if not info:

            logger.error(
                "TikTok extraction failed: %s",
                last_error
            )

            await processing.edit_text(
                "❌ لم أستطع استخراج هذا المنشور.\n\n"
                "قد يكون TikTok غيّر طريقة الوصول "
                "إلى المنشور أو أن المحتوى غير متاح "
                "للوصول العام.\n\n"
                "🔄 جرّب إرسال الرابط مرة أخرى."
            )

            return

        # =================================================
        # STEP 3 — Metadata
        # =================================================

        meta = build_metadata(
            info
        )

        caption = result_caption(
            meta
        )

        images = get_images(
            info
        )

        # =================================================
        # STEP 4 — Photo Mode
        # =================================================

        is_photo_post = (
            info.get("_type") == "playlist"
            or info.get("extractor_key") == "TikTok"
            and (
                info.get("entries")
                and not info.get("url")
            )
        )

        if images and (
            is_photo_post
            or info.get("duration") is None
        ):

            await processing.edit_text(
                caption,
                parse_mode="HTML",
            )

            await send_photos(
                context.bot,
                update.effective_chat.id,
                images,
            )

            return

        # =================================================
        # STEP 5 — Download video
        # =================================================

        temp_dir = create_temp_directory()

        await processing.edit_text(
            "🚀 <b>SOKO TK</b>\n\n"
            "✅ تم العثور على المنشور.\n"
            "🎬 جاري تجهيز الفيديو...\n"
            "📦 تحميل الملف...",
            parse_mode="HTML",
        )

        video_path = None

        try:

            video_path = await download_video(
                final_url,
                temp_dir,
            )

        except Exception as exc:

            logger.warning(
                "Video download failed: %s",
                exc
            )

        # =================================================
        # STEP 6 — Send metadata
        # =================================================

        await processing.edit_text(
            caption,
            parse_mode="HTML",
        )

        # =================================================
        # STEP 7 — Send video
        # =================================================

        video_sent = False

        if video_path and os.path.isfile(
            video_path
        ):

            video_sent = await send_video_file(
                context.bot,
                update.effective_chat.id,
                video_path,
                None,
            )

        # =================================================
        # STEP 8 — Audio
        # =================================================

        audio_path = None

        try:

            audio_path = await download_audio(
                final_url,
                temp_dir,
            )

        except Exception as exc:

            logger.warning(
                "Audio download failed: %s",
                exc
            )

        if audio_path and os.path.isfile(
            audio_path
        ):

            await send_audio_file(
                context.bot,
                update.effective_chat.id,
                audio_path,
            )

        # =================================================
        # STEP 9 — Cover
        # =================================================

        thumbnail = (
            info.get("thumbnail")
            or info.get("thumbnail_url")
        )

        if thumbnail:

            try:

                await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=thumbnail,
                )

            except Exception as exc:

                logger.warning(
                    "Thumbnail failed: %s",
                    exc
                )

        # =================================================
        # FINAL ERROR
        # =================================================

        if not video_sent and not audio_path:

            await update.message.reply_text(
                "⚠️ حصلت على معلومات المنشور، "
                "لكن لم أستطع إرسال ملف الوسائط.\n\n"
                "قد يكون حجم الملف كبيرًا أو أن "
                "TikTok يمنع تنزيله حاليًا."
            )

    except asyncio.CancelledError:

        raise

    except Exception as exc:

        logger.exception(
            "SOKO TK unexpected error"
        )

        try:

            await processing.edit_text(
                "❌ حدث خطأ أثناء معالجة الرابط.\n\n"
                "🔄 جرّب إرسال الرابط مرة أخرى."
            )

        except Exception:
            pass

    finally:

        # =================================================
        # CLEAN TEMP FILES
        # =================================================

        if temp_dir:

            try:

                shutil.rmtree(
                    temp_dir,
                    ignore_errors=True,
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
            "BOT_TOKEN غير موجود.\n"
            "أضفه في GitHub Actions Secrets."
        )

    logger.info(
        "Starting SOKO TK..."
    )

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

    logger.info(
        "SOKO TK is running."
    )

    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()
