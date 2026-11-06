# server.py
# Islamic Exam - Flask API + SQLite + Telegram
# Python 3.11+

import os
import io
import csv
import time
import hmac
import secrets
import sqlite3
import threading
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, request, jsonify, send_file, send_from_directory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "exam.sqlite3"))

PORT = int(os.environ.get("PORT", "8080"))
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

MAX_STUDENTS = 23
EXAM_MINUTES = 60

QUESTIONS = [
    "﴿ وَيْلٌ لِلْمُطَفِّفِينَ ۝ ﴾",
    "﴿ الَّذِينَ إِذَا اكْتَالُوا عَلَى النَّاسِ يَسْتَوْفُونَ ۝ ﴾",
    "﴿ وَإِذَا كَالُوهُمْ أَوْ وَزَنُوهُمْ يُخْسِرُونَ ۝ ﴾",
    "﴿ أَلَا يَظُنُّ أُولَٰئِكَ أَنَّهُمْ مَبْعُوثُونَ ۝ ﴾",
    "﴿ لِيَوْمٍ عَظِيمٍ ۝ ﴾",
]

app = Flask(__name__)
DB_LOCK = threading.RLock()


def now_text():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def connect_db():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_db():
    with DB_LOCK:
        con = connect_db()
        con.executescript("""
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            normalized_name TEXT NOT NULL UNIQUE,
            code TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            token TEXT NOT NULL UNIQUE,
            opened_at TEXT NOT NULL,
            started_at TEXT,
            submitted_at TEXT,
            banned INTEGER NOT NULL DEFAULT 0,
            reset_count INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            question_no INTEGER NOT NULL,
            question_text TEXT NOT NULL,
            answer TEXT NOT NULL DEFAULT '',
            answered_at TEXT NOT NULL,
            duration_seconds INTEGER NOT NULL DEFAULT 0,
            UNIQUE(student_id, question_no),
            FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        INSERT OR IGNORE INTO settings(key, value)
        VALUES ('site_enabled', '1');
        """)
        con.commit()
        con.close()


def normalized_name(name):
    return " ".join(str(name or "").strip().casefold().split())


def site_enabled(con=None):
    own = con is None
    if own:
        con = connect_db()
    row = con.execute(
        "SELECT value FROM settings WHERE key='site_enabled'"
    ).fetchone()
    enabled = bool(row and row["value"] == "1")
    if own:
        con.close()
    return enabled


def get_token():
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()

    token = request.headers.get("X-Session-Token")
    if token:
        return token.strip()

    data = request.get_json(silent=True) or {}
    return str(data.get("token", "")).strip()


def get_student(con, token):
    if not token:
        return None
    return con.execute(
        "SELECT * FROM students WHERE token=?", (token,)
    ).fetchone()


def student_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = get_token()
        con = connect_db()
        student = get_student(con, token)

        if not student:
            con.close()
            return jsonify(error="انتهت الجلسة، سجّلي الدخول من جديد."), 401

        if student["banned"]:
            con.close()
            return jsonify(error="تم إيقاف هذا الحساب من قبل الأستاذة."), 403

        if student["submitted_at"]:
            # يسمح بقراءة الحالة فقط، لكن نقاط الحفظ ستمنع التعديل.
            pass

        request.student_id = student["id"]
        request.student_token = token
        con.close()
        return fn(*args, **kwargs)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = get_token()
        if not token:
            return jsonify(error="غير مصرح."), 401

        con = connect_db()
        row = con.execute(
            "SELECT 1 FROM admin_sessions WHERE token=?", (token,)
        ).fetchone() if table_exists(con, "admin_sessions") else None
        con.close()

        if not row:
            return jsonify(error="جلسة الأستاذة غير صالحة."), 401
        return fn(*args, **kwargs)
    return wrapper


def table_exists(con, table):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,)
    ).fetchone() is not None


def create_admin_table():
    con = connect_db()
    con.execute("""
        CREATE TABLE IF NOT EXISTS admin_sessions (
            token TEXT PRIMARY KEY,
            created_at TEXT NOT NULL
        )
    """)
    con.commit()
    con.close()


def elapsed_seconds(started_at):
    started = parse_date(started_at)
    if not started:
        return 0
    return max(0, int((datetime.now().astimezone() - started).total_seconds()))


def exam_expired(student):
    started = parse_date(student["started_at"])
    if not started:
        return False
    return datetime.now().astimezone() >= started + timedelta(minutes=EXAM_MINUTES)


def telegram_send_report(student_id):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        app.logger.warning("Telegram secrets are not configured.")
        return

    con = connect_db()
    student = con.execute(
        "SELECT * FROM students WHERE id=?", (student_id,)
    ).fetchone()
    answers = con.execute("""
        SELECT question_no, question_text, answer, answered_at, duration_seconds
        FROM answers WHERE student_id=? ORDER BY question_no
    """, (student_id,)).fetchall()
    con.close()

    if not student:
        return

    lines = [
        "اختبار التربية الإسلامية",
        "مدرسة الحسينية للبنات",
        "الصف الثاني متوسط",
        "--------------------------------",
        f"اسم الطالبة: {student['name']}",
        f"رمز الدخول: {student['code']}",
        f"وقت الدخول: {student['opened_at']}",
        f"وقت البدء: {student['started_at'] or 'لم تبدأ'}",
        f"وقت التسليم: {student['submitted_at'] or now_text()}",
        "--------------------------------",
    ]

    for item in answers:
        lines.extend([
            f"السؤال {item['question_no']}:",
            item["question_text"],
            f"الإجابة: {item['answer'] or 'لم تتم الإجابة'}",
            f"وقت الإجابة: {item['answered_at']}",
            f"مدة السؤال: {item['duration_seconds']} ثانية",
            "--------------------------------",
        ])

    report = "\n".join(lines).encode("utf-8")
    filename = f"exam_student_{student_id}.txt"

    boundary = "----ExamBoundary" + secrets.token_hex(12)
    fields = {
        "chat_id": TELEGRAM_CHAT_ID,
        "caption": f"تم تسليم اختبار الطالبة: {student['name']}",
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
    body.extend(report)
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    req = urllib.request.Request(
        url,
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            app.logger.info("Telegram report sent: %s", response.status)
    except Exception:
        app.logger.exception("Telegram report could not be sent.")


def finish_student(student_id):
    con = connect_db()
    student = con.execute(
        "SELECT * FROM students WHERE id=?", (student_id,)
    ).fetchone()

    if not student or student["submitted_at"]:
        con.close()
        return False

    con.execute(
        "UPDATE students SET submitted_at=? WHERE id=?",
        (now_text(), student_id)
    )
    con.commit()
    con.close()

    threading.Thread(
        target=telegram_send_report,
        args=(student_id,),
        daemon=True
    ).start()
    return True


@app.after_request
def add_headers(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


@app.get("/")
def home():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/health")
def health():
    return jsonify(status="ok", service="Islamic Exam", time=now_text())


@app.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}
    code = str(data.get("code", "")).strip()

    if not code:
        return jsonify(error="أدخلي رمز الدخول."), 400

    if ADMIN_PASSWORD and hmac.compare_digest(code, ADMIN_PASSWORD):
        token = secrets.token_urlsafe(36)
        con = connect_db()
        con.execute(
            "INSERT INTO admin_sessions(token, created_at) VALUES (?, ?)",
            (token, now_text())
        )
        con.commit()
        con.close()
        return jsonify(token=token, role="admin", existing=True)

    con = connect_db()
    if not site_enabled(con):
        con.close()
        return jsonify(error="الاختبار متوقف حالياً من قبل الأستاذة."), 503

    token = secrets.token_urlsafe(36)
    fingerprint = str(data.get("fingerprint") or secrets.token_urlsafe(18))
    con.close()
    return jsonify(
        token=token,
        role="student",
        existing=False,
        fingerprint=fingerprint
    )


@app.post("/api/student/register")
def register_student():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    token = get_token()

    if len(name.split()) < 3:
        return jsonify(error="يرجى كتابة الاسم الثلاثي."), 400

    if len(name) > 100:
        return jsonify(error="الاسم أطول من الحد المسموح."), 400

    con = connect_db()

    if not site_enabled(con):
        con.close()
        return jsonify(error="الاختبار متوقف حالياً."), 503

    existing_session = get_student(con, token)
    if existing_session:
        con.close()
        return jsonify(error="هذا الدخول مسجل مسبقاً."), 409

    count = con.execute("SELECT COUNT(*) AS n FROM students").fetchone()["n"]
    if count >= MAX_STUDENTS:
        con.close()
        return jsonify(error="اكتمل العدد المسموح به للطالبات."), 403

    normalized = normalized_name(name)
    duplicate = con.execute(
        "SELECT id FROM students WHERE normalized_name=?",
        (normalized,)
    ).fetchone()

    if duplicate:
        con.close()
        return jsonify(error="هذا الاسم مسجل مسبقاً ولا يمكن تكراره."), 409

    # رمز الدخول والفingerprint يربطان الجلسة الحالية بالطالبة.
    fingerprint = str(data.get("fingerprint") or secrets.token_urlsafe(18))
    code = str(data.get("code") or "STUDENT")

    try:
        cur = con.execute("""
            INSERT INTO students
            (name, normalized_name, code, fingerprint, token, opened_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (name, normalized, code, fingerprint, token, now_text()))
        student_id = cur.lastrowid
        con.commit()
    except sqlite3.IntegrityError:
        con.close()
        return jsonify(error="تعذر تسجيل الطالبة، الاسم أو الجلسة مستخدمة."), 409

    con.close()
    return jsonify(
        success=True,
        student={
            "id": student_id,
            "name": name,
            "fingerprint": fingerprint,
            "status": "لم تبدأ",
            "current_question": 1,
            "deadline": None
        }
    )


@app.get("/api/student/me")
@student_required
def student_me():
    con = connect_db()
    student = con.execute(
        "SELECT * FROM students WHERE id=?", (request.student_id,)
    ).fetchone()

    if not student:
        con.close()
        return jsonify(error="الطالبة غير موجودة."), 404

    count = con.execute(
        "SELECT COUNT(*) AS n FROM answers WHERE student_id=?",
        (student["id"],)
    ).fetchone()["n"]

    current_question = min(count + 1, len(QUESTIONS))
    if student["submitted_at"]:
        status = "تم التسليم"
    elif student["started_at"]:
        status = "قيد الاختبار"
    else:
        status = "لم تبدأ"

    deadline = None
    started = parse_date(student["started_at"])
    if started:
        deadline = (started + timedelta(minutes=EXAM_MINUTES)).isoformat()

    expired = exam_expired(student)
    student_id = student["id"]
    con.close()

    if expired and not student["submitted_at"]:
        finish_student(student_id)
        status = "تم التسليم تلقائياً"

    return jsonify(
        name=student["name"],
        fingerprint=student["fingerprint"],
        status=status,
        current_question=current_question,
        deadline=deadline,
        submitted=bool(student["submitted_at"]) or expired,
        questions=QUESTIONS
    )


@app.post("/api/student/answer")
@student_required
def save_answer():
    data = request.get_json(silent=True) or {}
    try:
        question_no = int(data.get("question", 0))
    except (TypeError, ValueError):
        return jsonify(error="رقم السؤال غير صحيح."), 400

    answer = str(data.get("answer", "")).strip()

    if question_no < 1 or question_no > len(QUESTIONS):
        return jsonify(error="السؤال غير موجود."), 400

    con = connect_db()
    student = con.execute(
        "SELECT * FROM students WHERE id=?", (request.student_id,)
    ).fetchone()

    if not student or student["submitted_at"]:
        con.close()
        return jsonify(error="تم تسليم الاختبار ولا يمكن تعديل الإجابات."), 403

    if student["banned"]:
        con.close()
        return jsonify(error="تم حظر هذا الحساب."), 403

    if exam_expired(student):
        con.close()
        finish_student(student["id"])
        return jsonify(error="انتهى وقت الاختبار."), 403

    if not student["started_at"]:
        started_at = now_text()
        con.execute(
            "UPDATE students SET started_at=? WHERE id=?",
            (started_at, student["id"])
        )
        student_started = parse_date(started_at)
    else:
        student_started = parse_date(student["started_at"])

    existing = con.execute(
        "SELECT id FROM answers WHERE student_id=? AND question_no=?",
        (student["id"], question_no)
    ).fetchone()

    if existing:
        con.close()
        return jsonify(error="تم حفظ إجابة هذا السؤال مسبقاً."), 409

    previous = con.execute(
        "SELECT COUNT(*) AS n FROM answers WHERE student_id=?",
        (student["id"],)
    ).fetchone()["n"]

    if question_no != previous + 1:
        con.close()
        return jsonify(error="يجب الإجابة عن الأسئلة بالترتيب."), 409

    duration = elapsed_seconds(student_started)
    con.execute("""
        INSERT INTO answers
        (student_id, question_no, question_text, answer, answered_at, duration_seconds)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        student["id"],
        question_no,
        QUESTIONS[question_no - 1],
        answer,
        now_text(),
        duration
    ))
    con.commit()
    con.close()

    if question_no == len(QUESTIONS) or bool(data.get("auto")):
        finish_student(student["id"])

    return jsonify(success=True, saved=True, next_question=question_no + 1)


@app.post("/api/student/finish")
@student_required
def finish():
    finish_student(request.student_id)
    return jsonify(success=True, submitted=True)


@app.get("/api/admin/overview")
@admin_required
def admin_overview():
    con = connect_db()
    rows = con.execute("""
        SELECT s.*,
        (SELECT COUNT(*) FROM answers a WHERE a.student_id=s.id) AS answer_count
        FROM students s ORDER BY s.id ASC
    """).fetchall()

    students = []
    started_count = 0
    submitted_count = 0
    active_count = 0

    for s in rows:
        started = bool(s["started_at"])
        submitted = bool(s["submitted_at"])
        if started:
            started_count += 1
        if submitted:
            submitted_count += 1
        if started and not submitted and not s["banned"]:
            active_count += 1

        if s["banned"]:
            label = "محظورة"
        elif submitted:
            label = "تم التسليم"
        elif started:
            label = "بدأت الاختبار"
        else:
            label = "لم تبدأ"

        students.append({
            "id": s["id"],
            "name": s["name"],
            "fingerprint": s["fingerprint"],
            "opened_at": s["opened_at"],
            "started_at": s["started_at"],
            "status_label": label,
            "elapsed_label": (
                f"{elapsed_seconds(s['started_at']) // 60} دقيقة"
                if started else "—"
            ),
            "banned": bool(s["banned"]),
            "answer_count": s["answer_count"]
        })

    enabled = site_enabled(con)
    con.close()

    return jsonify(
        enabled=enabled,
        count=len(students),
        started=started_count,
        submitted=submitted_count,
        active=active_count,
        students=students
    )


@app.get("/api/admin/student/<int:student_id>")
@admin_required
def admin_student(student_id):
    con = connect_db()
    student = con.execute(
        "SELECT * FROM students WHERE id=?", (student_id,)
    ).fetchone()

    if not student:
        con.close()
        return jsonify(error="الطالبة غير موجودة."), 404

    answers = con.execute("""
        SELECT question_no, question_text, answer, answered_at, duration_seconds
        FROM answers WHERE student_id=? ORDER BY question_no
    """, (student_id,)).fetchall()

    started = bool(student["started_at"])
    submitted = bool(student["submitted_at"])

    if student["banned"]:
        label = "محظورة"
    elif submitted:
        label = "تم التسليم"
    elif started:
        label = "بدأت الاختبار"
    else:
        label = "لم تبدأ"

    result = {
        "student": {
            "id": student["id"],
            "name": student["name"],
            "fingerprint": student["fingerprint"],
            "opened_at": student["opened_at"],
            "started_at": student["started_at"],
            "submitted_at": student["submitted_at"],
            "status_label": label,
            "elapsed_label": (
                f"{elapsed_seconds(student['started_at']) // 60} دقيقة"
                if started else "—"
            ),
            "banned": bool(student["banned"])
        },
        "answers": [dict(a) for a in answers]
    }
    con.close()
    return jsonify(result)


@app.post("/api/admin/student/<int:student_id>/ban")
@admin_required
def ban_student(student_id):
    data = request.get_json(silent=True) or {}
    banned = 1 if bool(data.get("banned")) else 0

    con = connect_db()
    cur = con.execute(
        "UPDATE students SET banned=? WHERE id=?",
        (banned, student_id)
    )
    con.commit()
    con.close()

    if cur.rowcount == 0:
        return jsonify(error="الطالبة غير موجودة."), 404
    return jsonify(success=True, banned=bool(banned))


@app.post("/api/admin/student/<int:student_id>/reset")
@admin_required
def reset_student(student_id):
    con = connect_db()
    student = con.execute(
        "SELECT id FROM students WHERE id=?", (student_id,)
    ).fetchone()

    if not student:
        con.close()
        return jsonify(error="الطالبة غير موجودة."), 404

    con.execute("DELETE FROM answers WHERE student_id=?", (student_id,))
    con.execute("""
        UPDATE students
        SET started_at=NULL, submitted_at=NULL, banned=0,
            token=?, reset_count=reset_count+1
        WHERE id=?
    """, (secrets.token_urlsafe(36), student_id))
    con.commit()
    con.close()
    return jsonify(success=True, message="تمت إعادة الاختبار.")


@app.post("/api/admin/site")
@admin_required
def set_site():
    data = request.get_json(silent=True) or {}
    enabled = "1" if bool(data.get("enabled")) else "0"

    con = connect_db()
    con.execute(
        "INSERT OR REPLACE INTO settings(key, value) VALUES ('site_enabled', ?)",
        (enabled,)
    )
    con.commit()
    con.close()
    return jsonify(success=True, enabled=enabled == "1")


@app.get("/api/admin/export")
@admin_required
def export_csv():
    con = connect_db()
    students = con.execute(
        "SELECT * FROM students ORDER BY id"
    ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID", "Name", "Code", "Opened At", "Started At",
        "Submitted At", "Status", "Question", "Answer",
        "Answered At", "Duration Seconds"
    ])

    for s in students:
        answers = con.execute("""
            SELECT * FROM answers WHERE student_id=? ORDER BY question_no
        """, (s["id"],)).fetchall()

        status = (
            "Banned" if s["banned"] else
            "Submitted" if s["submitted_at"] else
            "Started" if s["started_at"] else "Not started"
        )

        if not answers:
            writer.writerow([
                s["id"], s["name"], s["code"], s["opened_at"],
                s["started_at"], s["submitted_at"], status,
                "", "", "", ""
            ])
        else:
            for a in answers:
                writer.writerow([
                    s["id"], s["name"], s["code"], s["opened_at"],
                    s["started_at"], s["submitted_at"], status,
                    a["question_no"], a["answer"], a["answered_at"],
                    a["duration_seconds"]
                ])

    con.close()
    payload = io.BytesIO(("\ufeff" + output.getvalue()).encode("utf-8"))
    payload.seek(0)

    return send_file(
        payload,
        mimetype="text/csv; charset=utf-8",
        as_attachment=True,
        download_name="islamic_exam_results.csv"
    )


@app.errorhandler(404)
def not_found(_error):
    if request.path.startswith("/api/"):
        return jsonify(error="المسار غير موجود."), 404
    return "Page not found", 404


@app.errorhandler(500)
def server_error(_error):
    app.logger.exception("Internal server error")
    return jsonify(error="حدث خطأ داخلي في الخادم."), 500


init_db()
create_admin_table()

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        threaded=True
    )
