import os
import re
import io
import json
import time
import hmac
import sqlite3
import secrets
import hashlib
import threading
from datetime import datetime, timezone, timedelta
from functools import wraps
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from flask import Flask, request, jsonify, send_file, make_response


# =========================================================
# إعدادات المشروع
# =========================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "islamic_exam.db")

PORT = int(os.environ.get("PORT", "8080"))
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "").strip()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

MAX_STUDENTS = 23
EXAM_MINUTES = 60
EXAM_SECONDS = EXAM_MINUTES * 60

SCHOOL_NAME = "مدرسة الحسينية للبنات"
GRADE_NAME = "الثاني متوسط"
SUBJECT_NAME = "التربية الإسلامية"
EXAM_DATE = "2026/11/6"

QUESTIONS = [
    "﴿ وَيْلٌ لِلْمُطَفِّفِينَ ۝ ﴾",
    "﴿ الَّذِينَ إِذَا اكْتَالُوا عَلَى النَّاسِ يَسْتَوْفُونَ ۝ ﴾",
    "﴿ وَإِذَا كَالُوهُمْ أَوْ وَزَنُوهُمْ يُخْسِرُونَ ۝ ﴾",
    "﴿ أَلَا يَظُنُّ أُولَٰئِكَ أَنَّهُمْ مَبْعُوثُونَ ۝ ﴾",
    "﴿ لِيَوْمٍ عَظِيمٍ ۝ ﴾",
]

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

db_lock = threading.RLock()


# =========================================================
# أدوات عامة
# =========================================================

def now_utc():
    return datetime.now(timezone.utc)


def now_text():
    return now_utc().isoformat(timespec="seconds")


def parse_date(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (ValueError, TypeError):
        return None


def make_token():
    return secrets.token_urlsafe(40)


def fingerprint_hash(value):
    value = str(value or "").strip()
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def get_db():
    connection = sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def response_ok(**kwargs):
    return jsonify({"ok": True, **kwargs})


def response_error(message, status=400, **kwargs):
    return jsonify({
        "ok": False,
        "error": message,
        "message": message,
        **kwargs
    }), status


def get_request_token():
    auth = request.headers.get("Authorization", "").strip()

    if auth.lower().startswith("bearer "):
        return auth[7:].strip()

    token = request.headers.get("X-Session-Token", "").strip()

    if token:
        return token

    data = request.get_json(silent=True) or {}
    return str(data.get("token", "")).strip()


def clean_name(value):
    value = str(value or "").strip()
    value = re.sub(r"\s+", " ", value)
    return value


def valid_full_name(name):
    parts = [part for part in name.split(" ") if part]
    return len(parts) >= 3 and len(name) <= 120


def elapsed_seconds(started_at):
    start = parse_date(started_at)
    if not start:
        return 0
    return max(0, int((now_utc() - start).total_seconds()))


def student_time_left(student):
    if not student["started_at"]:
        return EXAM_SECONDS

    remaining = EXAM_SECONDS - elapsed_seconds(student["started_at"])
    return max(0, remaining)


def student_status(student):
    if student["is_banned"]:
        return "محظورة"
    if student["finished_at"]:
        return "سلّمت الاختبار"
    if student["started_at"]:
        if student_time_left(student) <= 0:
            return "انتهى الوقت"
        return "بدأت الاختبار"
    return "لم تبدأ"


def student_public_data(student):
    return {
        "id": student["id"],
        "name": student["name"],
        "code": student["login_code"],
        "login_code": student["login_code"],
        "fingerprint": student["fingerprint"],
        "created_at": student["created_at"],
        "login_at": student["login_at"],
        "started_at": student["started_at"],
        "finished_at": student["finished_at"],
        "is_banned": bool(student["is_banned"]),
        "status": student_status(student),
        "remaining_seconds": student_time_left(student),
        "remaining_minutes": max(0, student_time_left(student) // 60),
    }


# =========================================================
# إنشاء قاعدة البيانات
# =========================================================

def init_db():
    with db_lock:
        db = get_db()
        try:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS students (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    name_key TEXT NOT NULL UNIQUE,
                    login_code TEXT NOT NULL,
                    fingerprint TEXT,
                    session_token TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    login_at TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    is_banned INTEGER NOT NULL DEFAULT 0,
                    ban_reason TEXT DEFAULT '',
                    last_seen TEXT
                );

                CREATE TABLE IF NOT EXISTS answers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id INTEGER NOT NULL,
                    question_index INTEGER NOT NULL,
                    question_text TEXT NOT NULL,
                    answer_text TEXT NOT NULL DEFAULT '',
                    answered_at TEXT NOT NULL,
                    duration_seconds INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(student_id, question_index),
                    FOREIGN KEY(student_id)
                        REFERENCES students(id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS admin_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    last_seen TEXT NOT NULL
                );
            """)

            db.execute("""
                INSERT OR IGNORE INTO settings (key, value)
                VALUES ('site_enabled', '1')
            """)

            db.commit()
        finally:
            db.close()


# =========================================================
# المصادقة والصلاحيات
# =========================================================

def get_student_by_token(token):
    if not token:
        return None

    db = get_db()
    try:
        return db.execute(
            "SELECT * FROM students WHERE session_token = ?",
            (token,)
        ).fetchone()
    finally:
        db.close()


def get_admin_by_token(token):
    if not token:
        return None

    db = get_db()
    try:
        return db.execute(
            "SELECT * FROM admin_sessions WHERE token = ?",
            (token,)
        ).fetchone()
    finally:
        db.close()


def require_admin(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        token = get_request_token()

        if not get_admin_by_token(token):
            return response_error(
                "جلسة الأستاذة غير صالحة، يرجى تسجيل الدخول مجدداً",
                401
            )

        db = get_db()
        try:
            db.execute(
                "UPDATE admin_sessions SET last_seen = ? WHERE token = ?",
                (now_text(), token)
            )
            db.commit()
        finally:
            db.close()

        return function(*args, **kwargs)

    return wrapper


def require_student(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        token = get_request_token()
        student = get_student_by_token(token)

        if not student:
            return response_error(
                "جلسة الطالبة غير صالحة، يرجى تسجيل الدخول مجدداً",
                401
            )

        if student["is_banned"]:
            return response_error("تم حظر هذا الحساب من الاختبار", 403)

        request.student = student
        request.student_token = token

        db = get_db()
        try:
            db.execute(
                "UPDATE students SET last_seen = ? WHERE id = ?",
                (now_text(), student["id"])
            )
            db.commit()
        finally:
            db.close()

        return function(*args, **kwargs)

    return wrapper


# =========================================================
# إعدادات الموقع
# =========================================================

def is_site_enabled():
    db = get_db()
    try:
        row = db.execute(
            "SELECT value FROM settings WHERE key = 'site_enabled'"
        ).fetchone()

        return row is None or row["value"] == "1"
    finally:
        db.close()


def set_site_enabled(enabled):
    db = get_db()
    try:
        db.execute("""
            INSERT INTO settings (key, value)
            VALUES ('site_enabled', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, ("1" if enabled else "0",))
        db.commit()
    finally:
        db.close()


# =========================================================
# Telegram
# =========================================================

def send_telegram_document(filename, content):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False, "إعدادات Telegram غير مكتملة"

    boundary = "----IslamicExam" + secrets.token_hex(12)

    fields = {
        "chat_id": TELEGRAM_CHAT_ID,
        "caption": "تقرير اختبار التربية الإسلامية"
    }

    body = bytearray()

    for key, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode()
        )
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")

    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'.encode()
    )
    body.extend(b"Content-Type: text/plain; charset=utf-8\r\n\r\n")
    body.extend(content.encode("utf-8"))
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())

    url = (
        "https://api.telegram.org/bot"
        + TELEGRAM_BOT_TOKEN
        + "/sendDocument"
    )

    req = Request(
        url,
        data=bytes(body),
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}"
        },
        method="POST"
    )

    try:
        with urlopen(req, timeout=25) as response:
            result = json.loads(response.read().decode("utf-8"))
            if result.get("ok"):
                return True, "تم إرسال التقرير"
            return False, result.get("description", "فشل إرسال التقرير")

    except (HTTPError, URLError, TimeoutError, ValueError) as error:
        return False, str(error)


def build_student_report(student_id):
    db = get_db()
    try:
        student = db.execute(
            "SELECT * FROM students WHERE id = ?",
            (student_id,)
        ).fetchone()

        if not student:
            return None

        answers = db.execute("""
            SELECT *
            FROM answers
            WHERE student_id = ?
            ORDER BY question_index ASC
        """, (student_id,)).fetchall()

        lines = [
            SCHOOL_NAME,
            SUBJECT_NAME,
            GRADE_NAME,
            "تقرير إجابات الطالبة",
            "=" * 45,
            f"اسم الطالبة: {student['name']}",
            f"رمز الدخول: {student['login_code']}",
            f"وقت الدخول: {student['login_at'] or 'غير متوفر'}",
            f"وقت بدء الاختبار: {student['started_at'] or 'لم تبدأ'}",
            f"وقت التسليم: {student['finished_at'] or 'لم تسلّم'}",
            f"الحالة: {student_status(student)}",
            "=" * 45,
        ]

        answer_map = {
            row["question_index"]: row for row in answers
        }

        for index, question in enumerate(QUESTIONS):
            lines.append("")
            lines.append(f"السؤال {index + 1}:")
            lines.append(question)

            answer = answer_map.get(index)

            if answer:
                lines.append(
                    "الإجابة: " + (answer["answer_text"] or "بدون إجابة")
                )
                lines.append("وقت الإجابة: " + answer["answered_at"])
            else:
                lines.append("الإجابة: لم تتم الإجابة")

        lines.extend([
            "",
            "=" * 45,
            "تم إنشاء التقرير آلياً من نظام الاختبار."
        ])

        return "\n".join(lines)

    finally:
        db.close()


# =========================================================
# الصفحة الرئيسية وفحص الاتصال
# =========================================================

@app.get("/")
def home():
    index_path = os.path.join(BASE_DIR, "index.html")

    if not os.path.isfile(index_path):
        return "ملف index.html غير موجود", 404

    response = make_response(
        app.send_static_file("index.html")
        if False else open(index_path, "r", encoding="utf-8").read()
    )
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    return response


@app.get("/health")
def health():
    try:
        db = get_db()
        db.execute("SELECT 1").fetchone()
        db.close()

        return response_ok(
            status="online",
            server="Islamic Exam Server",
            database="connected",
            time=now_text()
        )

    except Exception:
        app.logger.exception("Health check failed")
        return response_error("قاعدة البيانات غير متاحة", 500)


@app.get("/api/config")
def public_config():
    return response_ok(
        school=SCHOOL_NAME,
        grade=GRADE_NAME,
        subject=SUBJECT_NAME,
        exam_date=EXAM_DATE,
        duration_minutes=EXAM_MINUTES,
        max_students=MAX_STUDENTS,
        questions=QUESTIONS,
        site_enabled=is_site_enabled()
    )


# =========================================================
# تسجيل الدخول
# =========================================================

@app.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}
    code = str(data.get("code", "")).strip()

    if not code:
        return response_error("يرجى إدخال رمز الدخول")

    # دخول الأستاذة
    if ADMIN_PASSWORD and hmac.compare_digest(code, ADMIN_PASSWORD):
        token = make_token()
        db = get_db()

        try:
            db.execute(
                "INSERT INTO admin_sessions (token, created_at, last_seen) VALUES (?, ?, ?)",
                (token, now_text(), now_text())
            )
            db.commit()
        finally:
            db.close()

        return response_ok(
            token=token,
            role="admin",
            existing=True,
            message="تم تسجيل دخول الأستاذة"
        )

    # دخول الطالبة
    if not is_site_enabled():
        return response_error("الاختبار مغلق حالياً من قبل الأستاذة", 403)

    token = make_token()

    fingerprint = str(data.get("fingerprint", "")).strip()
    fp_hash = fingerprint_hash(fingerprint)

    db = get_db()
    try:
        existing_student = None

        if fp_hash:
            existing_student = db.execute(
                "SELECT * FROM students WHERE fingerprint = ?",
                (fp_hash,)
            ).fetchone()

        if existing_student:
            if existing_student["is_banned"]:
                return response_error("هذا الحساب محظور", 403)

            # إعادة استخدام حساب الجهاز نفسه
            db.execute("""
                UPDATE students
                SET session_token = ?, login_code = ?, login_at = ?, last_seen = ?
                WHERE id = ?
            """, (
                token,
                code,
                now_text(),
                now_text(),
                existing_student["id"]
            ))
            db.commit()

            return response_ok(
                token=token,
                role="student",
                existing=True,
                student=student_public_data(existing_student),
                message="تم العثور على حساب سابق"
            )

        return response_ok(
            token=token,
            role="student",
            existing=False,
            fingerprint=fp_hash,
            message="يرجى إدخال الاسم الثلاثي"
        )

    finally:
        db.close()


# =========================================================
# تسجيل الطالبة بالاسم الثلاثي
# =========================================================

@app.post("/api/student/register")
def register_student():
    token = get_request_token()
    data = request.get_json(silent=True) or {}

    if not token:
        return response_error("جلسة الدخول غير موجودة", 401)

    name = clean_name(data.get("name", ""))

    if not valid_full_name(name):
        return response_error("يرجى كتابة الاسم الثلاثي بشكل صحيح")

    name_key = name.casefold()
    db = get_db()

    try:
        # إذا كان هذا الرمز قد سجل سابقاً، نعيد بيانات حسابه
        existing_token_student = db.execute(
            "SELECT * FROM students WHERE session_token = ?",
            (token,)
        ).fetchone()

        if existing_token_student:
            if existing_token_student["is_banned"]:
                return response_error("هذا الحساب محظور", 403)

            return response_ok(
                student=student_public_data(existing_token_student),
                existing=True,
                message="الحساب مسجل مسبقاً"
            )

        fingerprint = str(
            data.get("fingerprint", "")
        ).strip()

        fp_hash = fingerprint_hash(fingerprint)

        if fp_hash:
            previous = db.execute(
                "SELECT * FROM students WHERE fingerprint = ?",
                (fp_hash,)
            ).fetchone()

            if previous:
                if previous["is_banned"]:
                    return response_error("هذا الحساب محظور", 403)

                db.execute("""
                    UPDATE students
                    SET session_token = ?, login_at = ?, last_seen = ?
                    WHERE id = ?
                """, (
                    token,
                    now_text(),
                    now_text(),
                    previous["id"]
                ))
                db.commit()

                updated = db.execute(
                    "SELECT * FROM students WHERE id = ?",
                    (previous["id"],)
                ).fetchone()

                return response_ok(
                    student=student_public_data(updated),
                    existing=True,
                    message="تم استرجاع حساب الطالبة"
                )

        duplicate = db.execute(
            "SELECT id FROM students WHERE name_key = ?",
            (name_key,)
        ).fetchone()

        if duplicate:
            return response_error(
                "هذا الاسم مسجل مسبقاً، لا يمكن تسجيله مرة ثانية",
                409
            )

        count = db.execute(
            "SELECT COUNT(*) AS total FROM students"
        ).fetchone()["total"]

        if count >= MAX_STUDENTS:
            return response_error(
                "اكتمل العدد المسموح به للطالبات",
                403
            )

        timestamp = now_text()

        cursor = db.execute("""
            INSERT INTO students (
                name,
                name_key,
                login_code,
                fingerprint,
                session_token,
                created_at,
                login_at,
                started_at,
                last_seen
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            name,
            name_key,
            data.get("code", ""),
            fp_hash,
            token,
            timestamp,
            timestamp,
            timestamp,
            timestamp
        ))

        student_id = cursor.lastrowid
        db.commit()

        student = db.execute(
            "SELECT * FROM students WHERE id = ?",
            (student_id,)
        ).fetchone()

        return response_ok(
            student=student_public_data(student),
            existing=False,
            message="تم تسجيل الطالبة بنجاح"
        )

    except sqlite3.IntegrityError:
        db.rollback()
        return response_error("تعذر تسجيل الاسم، ربما تم تسجيله مسبقاً", 409)

    finally:
        db.close()


# =========================================================
# بيانات الطالبة واسترجاع الجلسة
# =========================================================

@app.get("/api/student/me")
@require_student
def student_me():
    student = get_student_by_token(request.student_token)

    if not student:
        return response_error("الحساب غير موجود", 404)

    db = get_db()
    try:
        answers = db.execute("""
            SELECT question_index, answer_text, answered_at, duration_seconds
            FROM answers
            WHERE student_id = ?
            ORDER BY question_index ASC
        """, (student["id"],)).fetchall()

        answer_list = [
            {
                "question_index": row["question_index"],
                "questionIndex": row["question_index"],
                "answer": row["answer_text"],
                "answer_text": row["answer_text"],
                "answered_at": row["answered_at"],
                "duration_seconds": row["duration_seconds"]
            }
            for row in answers
        ]

        return response_ok(
            student=student_public_data(student),
            answers=answer_list,
            questions=QUESTIONS,
            duration_minutes=EXAM_MINUTES,
            remaining_seconds=student_time_left(student),
            finished=bool(student["finished_at"])
        )

    finally:
        db.close()


# =========================================================
# حفظ إجابة سؤال
# =========================================================

@app.post("/api/student/answer")
@require_student
def save_answer():
    data = request.get_json(silent=True) or {}
    student = get_student_by_token(request.student_token)

    if not student:
        return response_error("الطالبة غير موجودة", 404)

    if student["finished_at"]:
        return response_error("تم تسليم الاختبار مسبقاً", 403)

    if not student["started_at"]:
        return response_error("لم يبدأ الاختبار بعد", 403)

    remaining = student_time_left(student)

    if remaining <= 0:
        return response_error("انتهى وقت الاختبار", 403)

    raw_index = data.get(
        "question_index",
        data.get("questionIndex", data.get("index"))
    )

    try:
        question_index = int(raw_index)
    except (ValueError, TypeError):
        return response_error("رقم السؤال غير صحيح")

    if question_index < 0 or question_index >= len(QUESTIONS):
        return response_error("السؤال غير موجود")

    answer_text = data.get(
        "answer",
        data.get("answer_text", data.get("text", ""))
    )
    answer_text = str(answer_text or "").strip()

    if len(answer_text) > 5000:
        return response_error("الإجابة طويلة جداً")

    db = get_db()
    try:
        # يمنع تخطي ترتيب الأسئلة
        previous = db.execute("""
            SELECT question_index
            FROM answers
            WHERE student_id = ?
            ORDER BY question_index ASC
        """, (student["id"],)).fetchall()

        answered_indices = {row["question_index"] for row in previous}

        first_unanswered = 0
        while first_unanswered in answered_indices:
            first_unanswered += 1

        already_answered = question_index in answered_indices

        if question_index > first_unanswered and not already_answered:
            return response_error(
                "يجب الإجابة عن الأسئلة بالترتيب",
                403,
                next_question=first_unanswered
            )

        timestamp = now_text()

        old_answer = db.execute("""
            SELECT id FROM answers
            WHERE student_id = ? AND question_index = ?
        """, (student["id"], question_index)).fetchone()

        if old_answer:
            db.execute("""
                UPDATE answers
                SET answer_text = ?, answered_at = ?
                WHERE student_id = ? AND question_index = ?
            """, (
                answer_text,
                timestamp,
                student["id"],
                question_index
            ))
        else:
            db.execute("""
                INSERT INTO answers (
                    student_id,
                    question_index,
                    question_text,
                    answer_text,
                    answered_at,
                    duration_seconds
                )
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                student["id"],
                question_index,
                QUESTIONS[question_index],
                answer_text,
                timestamp,
                elapsed_seconds(student["started_at"])
            ))

        db.commit()

        return response_ok(
            saved=True,
            question_index=question_index,
            answered_at=timestamp,
            remaining_seconds=remaining,
            message="تم حفظ الإجابة بنجاح"
        )

    finally:
        db.close()


# =========================================================
# تسليم الاختبار
# =========================================================

@app.post("/api/student/finish")
@require_student
def finish_exam():
    student = get_student_by_token(request.student_token)

    if not student:
        return response_error("الطالبة غير موجودة", 404)

    if student["finished_at"]:
        return response_ok(
            finished=True,
            already_finished=True,
            message="تم تسليم الاختبار مسبقاً"
        )

    timestamp = now_text()

    db = get_db()
    try:
        db.execute("""
            UPDATE students
            SET finished_at = ?
            WHERE id = ? AND finished_at IS NULL
        """, (timestamp, student["id"]))

        db.commit()
    finally:
        db.close()

    report = build_student_report(student["id"])
    telegram_sent = False
    telegram_message = "لم يتم إعداد Telegram"

    if report:
        telegram_sent, telegram_message = send_telegram_document(
            f"exam_student_{student['id']}.txt",
            report
        )

    return response_ok(
        finished=True,
        finished_at=timestamp,
        telegram_sent=telegram_sent,
        telegram_message=telegram_message,
        message="تم تسليم الاختبار بنجاح"
    )


# =========================================================
# لوحة الأستاذة: ملخص الطالبات
# =========================================================

@app.get("/api/admin/overview")
@require_admin
def admin_overview():
    db = get_db()
    try:
        students = db.execute("""
            SELECT *
            FROM students
            ORDER BY created_at ASC
        """).fetchall()

        total = len(students)
        started = sum(1 for s in students if s["started_at"])
        finished = sum(1 for s in students if s["finished_at"])
        banned = sum(1 for s in students if s["is_banned"])

        student_list = []

        for student in students:
            item = student_public_data(student)

            answer_count = db.execute("""
                SELECT COUNT(*) AS total
                FROM answers
                WHERE student_id = ?
            """, (student["id"],)).fetchone()["total"]

            item["answers_count"] = answer_count
            item["answered_count"] = answer_count
            student_list.append(item)

        return response_ok(
            school=SCHOOL_NAME,
            grade=GRADE_NAME,
            subject=SUBJECT_NAME,
            exam_date=EXAM_DATE,
            total=total,
            total_students=total,
            max_students=MAX_STUDENTS,
            started=started,
            started_count=started,
            finished=finished,
            finished_count=finished,
            banned=banned,
            banned_count=banned,
            site_enabled=is_site_enabled(),
            duration_minutes=EXAM_MINUTES,
            questions=QUESTIONS,
            students=student_list
        )

    finally:
        db.close()


# =========================================================
# تفاصيل طالبة محددة
# =========================================================

@app.get("/api/admin/student/<int:student_id>")
@require_admin
def admin_student_details(student_id):
    db = get_db()
    try:
        student = db.execute(
            "SELECT * FROM students WHERE id = ?",
            (student_id,)
        ).fetchone()

        if not student:
            return response_error("الطالبة غير موجودة", 404)

        answers = db.execute("""
            SELECT *
            FROM answers
            WHERE student_id = ?
            ORDER BY question_index ASC
        """, (student_id,)).fetchall()

        answer_map = {
            row["question_index"]: row for row in answers
        }

        detailed_answers = []

        for index, question in enumerate(QUESTIONS):
            row = answer_map.get(index)

            detailed_answers.append({
                "question_index": index,
                "questionIndex": index,
                "question": question,
                "question_text": question,
                "answer": row["answer_text"] if row else "",
                "answer_text": row["answer_text"] if row else "",
                "answered": bool(row),
                "answered_at": row["answered_at"] if row else None,
                "duration_seconds": row["duration_seconds"] if row else 0
            })

        return response_ok(
            student=student_public_data(student),
            answers=detailed_answers,
            questions=QUESTIONS
        )

    finally:
        db.close()


# =========================================================
# حظر طالبة / رفع الحظر
# =========================================================

@app.post("/api/admin/student/<int:student_id>/ban")
@require_admin
def admin_ban_student(student_id):
    data = request.get_json(silent=True) or {}
    banned = data.get("banned", True)

    if isinstance(banned, str):
        banned = banned.lower() in ("true", "1", "yes", "on")

    reason = str(data.get("reason", "")).strip()[:300]

    db = get_db()
    try:
        student = db.execute(
            "SELECT id FROM students WHERE id = ?",
            (student_id,)
        ).fetchone()

        if not student:
            return response_error("الطالبة غير موجودة", 404)

        db.execute("""
            UPDATE students
            SET is_banned = ?, ban_reason = ?
            WHERE id = ?
        """, (1 if banned else 0, reason, student_id))

        db.commit()

        return response_ok(
            banned=bool(banned),
            message="تم حظر الطالبة" if banned else "تم رفع الحظر"
        )

    finally:
        db.close()


# =========================================================
# إعادة الاختبار
# =========================================================

@app.post("/api/admin/student/<int:student_id>/reset")
@require_admin
def admin_reset_student(student_id):
    db = get_db()
    try:
        student = db.execute(
            "SELECT * FROM students WHERE id = ?",
            (student_id,)
        ).fetchone()

        if not student:
            return response_error("الطالبة غير موجودة", 404)

        # نحافظ على رمز الجلسة حتى لا تنقطع الطالبة عن حسابها
        db.execute(
            "DELETE FROM answers WHERE student_id = ?",
            (student_id,)
        )

        db.execute("""
            UPDATE students
            SET started_at = NULL,
                finished_at = NULL,
                is_banned = 0,
                ban_reason = '',
                login_at = ?
            WHERE id = ?
        """, (now_text(), student_id))

        db.commit()

        return response_ok(
            reset=True,
            message="تمت إعادة الاختبار وحذف إجابات هذه الطالبة"
        )

    finally:
        db.close()


# =========================================================
# تشغيل وإيقاف استقبال الطالبات
# =========================================================

@app.post("/api/admin/site")
@require_admin
def admin_site_toggle():
    data = request.get_json(silent=True) or {}

    enabled = data.get(
        "enabled",
        data.get("site_enabled", None)
    )

    if enabled is None:
        return response_error("لم يتم تحديد حالة الموقع")

    if isinstance(enabled, str):
        enabled = enabled.lower() in ("true", "1", "yes", "on")

    set_site_enabled(bool(enabled))

    return response_ok(
        site_enabled=bool(enabled),
        enabled=bool(enabled),
        message="تم فتح استقبال الطالبات" if enabled
        else "تم إيقاف استقبال الطالبات"
    )


@app.post("/api/admin/site-status")
@require_admin
def admin_site_status_alias():
    return admin_site_toggle()


# =========================================================
# تصدير تقرير جميع الطالبات
# =========================================================

@app.get("/api/admin/export")
@require_admin
def admin_export():
    db = get_db()
    try:
        students = db.execute("""
            SELECT *
            FROM students
            ORDER BY created_at ASC
        """).fetchall()

        lines = [
            SCHOOL_NAME,
            GRADE_NAME,
            SUBJECT_NAME,
            f"تاريخ الاختبار: {EXAM_DATE}",
            "=" * 55,
            f"عدد الطالبات: {len(students)}",
            ""
        ]

        for student in students:
            report = build_student_report(student["id"])
            if report:
                lines.append(report)
                lines.append("\n\n" + "#" * 60 + "\n\n")

        content = "\ufeff" + "\n".join(lines)
        file_data = io.BytesIO(content.encode("utf-8"))
        file_data.seek(0)

        return send_file(
            file_data,
            mimetype="text/plain; charset=utf-8",
            as_attachment=True,
            download_name="islamic_exam_report.txt"
        )

    finally:
        db.close()


# =========================================================
# تسجيل خروج الأستاذة
# =========================================================

@app.post("/api/admin/logout")
@require_admin
def admin_logout():
    token = get_request_token()

    db = get_db()
    try:
        db.execute(
            "DELETE FROM admin_sessions WHERE token = ?",
            (token,)
        )
        db.commit()
    finally:
        db.close()

    return response_ok(message="تم تسجيل الخروج")


# =========================================================
# معالجة الأخطاء
# =========================================================

@app.errorhandler(404)
def not_found(error):
    if request.path.startswith("/api/"):
        return response_error("المسار المطلوب غير موجود", 404)
    return "الصفحة غير موجودة", 404


@app.errorhandler(405)
def method_not_allowed(error):
    if request.path.startswith("/api/"):
        return response_error("طريقة الطلب غير مسموحة", 405)
    return "Method Not Allowed", 405


@app.errorhandler(413)
def file_too_large(error):
    return response_error("حجم البيانات المرسلة أكبر من المسموح", 413)


@app.errorhandler(500)
def internal_error(error):
    app.logger.exception("Internal server error")
    if request.path.startswith("/api/"):
        return response_error("حدث خطأ داخلي في الخادم", 500)
    return "حدث خطأ داخلي في الخادم", 500


# =========================================================
# بدء تشغيل الخادم
# =========================================================

if __name__ == "__main__":
    if not ADMIN_PASSWORD:
        raise RuntimeError(
            "لم يتم العثور على GitHub Secret باسم ADMIN_PASSWORD"
        )

    init_db()

    print("=" * 55)
    print("ISLAMIC EXAM SERVER")
    print("School:", SCHOOL_NAME)
    print("Grade:", GRADE_NAME)
    print("Subject:", SUBJECT_NAME)
    print("Port:", PORT)
    print("Database:", DB_PATH)
    print("Telegram:", "Configured" if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID else "Not configured")
    print("=" * 55)

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        threaded=True
    )
