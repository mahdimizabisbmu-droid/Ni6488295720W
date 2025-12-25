import os
from pathlib import Path
from typing import Dict, List

import psycopg
from psycopg.rows import dict_row

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)
from telegram.error import NetworkError

# =========================
# CONFIG
# =========================
ADMIN_IDS = {6474515118}
ARCHIVE_CHANNEL_ID = -1003387982513
BOT_PUBLIC_LINK = "@SBMUchatBot"

# =========================
# Read secrets from env OR files (robust)
# =========================
def read_first_existing(paths):
    for p in paths:
        try:
            if p.exists() and p.is_file():
                txt = p.read_text(encoding="utf-8").strip()
                if txt:
                    return txt
        except Exception:
            pass
    return None

BASE_DIR = Path(__file__).resolve().parent          # .../src
ROOT_DIR = BASE_DIR.parent                          # project root

BOT_TOKEN = os.environ.get("BOT_TOKEN") or read_first_existing([
    ROOT_DIR / "Token.txt", BASE_DIR / "Token.txt",
    ROOT_DIR / "token.txt", BASE_DIR / "token.txt",
])

DATABASE_URL = os.environ.get("DATABASE_URL") or read_first_existing([
    ROOT_DIR / "Database.txt", BASE_DIR / "Database.txt",
    ROOT_DIR / "database.txt", BASE_DIR / "database.txt",
])

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not found. Put Token.txt near web.py/bot.py or set env BOT_TOKEN.")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL not found. Put Database.txt near web.py/bot.py or set env DATABASE_URL.")

# =========================
# DB connect + reconnect
# =========================
def db_connect():
    return psycopg.connect(DATABASE_URL, autocommit=True, row_factory=dict_row)

db = db_connect()

def q(sql: str, params: tuple = ()):
    """Execute query with auto-reconnect on dropped connection."""
    global db
    try:
        with db.cursor() as cur:
            cur.execute(sql, params)
            return cur
    except psycopg.OperationalError:
        db = db_connect()
        with db.cursor() as cur:
            cur.execute(sql, params)
            return cur

def init_db():
    q("""
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY,
        username TEXT,
        full_name TEXT,
        faculty TEXT,
        major TEXT,
        entry_year TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        last_seen TIMESTAMPTZ DEFAULT NOW()
    )
    """)
    q("""
    CREATE TABLE IF NOT EXISTS pending_uploads (
        upload_id BIGSERIAL PRIMARY KEY,
        submitter_id BIGINT NOT NULL,
        faculty TEXT NOT NULL,
        major TEXT NOT NULL,
        entry_year TEXT NOT NULL,
        course_name TEXT NOT NULL,
        professor_name TEXT,
        user_chat_id BIGINT NOT NULL,
        user_message_id BIGINT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', -- pending|approved|rejected
        created_at TIMESTAMPTZ DEFAULT NOW()
    )
    """)
    q("""
    CREATE TABLE IF NOT EXISTS materials (
        material_id BIGSERIAL PRIMARY KEY,
        faculty TEXT NOT NULL,
        major TEXT NOT NULL,
        entry_year TEXT NOT NULL,
        course_name TEXT NOT NULL,
        professor_name TEXT,
        archive_channel_id BIGINT NOT NULL,
        archive_message_id BIGINT NOT NULL,
        added_by BIGINT NOT NULL,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )
    """)
    q("CREATE INDEX IF NOT EXISTS idx_materials_search ON materials (faculty, major, course_name)")

    q("""
    CREATE TABLE IF NOT EXISTS user_stats (
        user_id BIGINT PRIMARY KEY,
        approved_uploads INT NOT NULL DEFAULT 0,
        chat_used BOOLEAN NOT NULL DEFAULT FALSE
    )
    """)

    q("""
    CREATE TABLE IF NOT EXISTS chat_sessions (
        session_id BIGSERIAL PRIMARY KEY,
        user_a BIGINT NOT NULL,
        user_b BIGINT NOT NULL,
        started_at TIMESTAMPTZ DEFAULT NOW(),
        ended_at TIMESTAMPTZ,
        status TEXT NOT NULL DEFAULT 'active'
    )
    """)

    q("""
    CREATE TABLE IF NOT EXISTS chat_messages (
        id BIGSERIAL PRIMARY KEY,
        session_id BIGINT NOT NULL,
        sender_id BIGINT NOT NULL,
        msg_text TEXT,
        ts TIMESTAMPTZ DEFAULT NOW()
    )
    """)

init_db()

# =========================
# Faculties & majors
# =========================
FACULTIES = [
    "دانشکده پزشکی",
    "دانشکده دندان‌پزشکی",
    "دانشکده داروسازی",
    "دانشکده بهداشت و ایمنی",
    "دانشکده توانبخشی",
    "دانشکده علوم تغذیه",
    "دانشکده پیراپزشکی",
    "دانشکده پرستاری و مامایی",
    "دانشکده فن‌آوری‌های نوین پزشکی",
    "دانشکده طب سنتی",
]

MAJORS_BY_FACULTY = {
    "دانشکده پزشکی": ["پزشکی"],
    "دانشکده دندان‌پزشکی": ["دندان‌پزشکی"],
    "دانشکده داروسازی": ["داروسازی"],
    "دانشکده بهداشت و ایمنی": ["بهداشت عمومی", "بهداشت محیط", "مهندسی بهداشت حرفه‌ای و ایمنی", "آموزش بهداشت و ارتقای سلامت"],
    "دانشکده توانبخشی": ["فیزیوتراپی", "کاردرمانی", "شنوایی‌شناسی", "گفتاردرمانی", "بینایی‌سنجی"],
    "دانشکده علوم تغذیه": ["علوم تغذیه", "علوم و صنایع غذایی"],
    "دانشکده پیراپزشکی": ["علوم آزمایشگاهی", "تکنولوژی اتاق عمل", "هوشبری", "فوریت‌های پزشکی", "تکنولوژی پرتوشناسی", "تکنولوژی پرتو درمانی"],
    "دانشکده پرستاری و مامایی": ["پرستاری", "مامایی"],
    "دانشکده فن‌آوری‌های نوین پزشکی": ["فناوری اطلاعات سلامت", "مهندسی پزشکی", "فناوری‌های نوین پزشکی"],
    "دانشکده طب سنتی": ["طب سنتی ایرانی"],
}

ENTRY_YEARS = [str(y) for y in range(1398, 1411)]

# =========================
# States (in-memory)
# =========================
user_state: Dict[int, str] = {}
tmp: Dict[int, dict] = {}
search_state: Dict[int, bool] = {}

# anonymous chat
waiting_queue: List[int] = []
active_chat: Dict[int, int] = {}        # uid -> partner
active_session: Dict[int, int] = {}     # uid -> session_id

# admin class filter state
admin_filter_state: Dict[int, Dict[str, str]] = {}

# =========================
# Texts
# =========================
WELCOME_TEXT = (
    "سلام 👋🌱\n"
    "این ربات با کلی زحمت ساخته شده تا بین بچه‌های دانشگاه **دوستی، اتحاد و کمک به هم** بیشتر بشه.\n\n"
    "اینجا می‌تونیم:\n"
    "📚 جزوه پیدا کنیم\n"
    "🤝 به همدیگه کمک کنیم\n"
    "💬 با چت ناشناس با بچه‌های دانشگاه آشنا بشیم و دوست پیدا کنیم\n\n"
    "اگه جزوه داری و می‌تونی به بقیه کمک کنی، حتماً به اشتراک بذارش 💙\n\n"
    "برای شروع فقط کافیه چندتا انتخاب ساده انجام بدی 👇"
)

CHAT_INTRO_TEXT = (
    "👀\n"
    "الان قراره با یه آدم رندوم از دانشگاه چت کنی\n\n"
    "همه‌چی ناشناسه و خصوصی\n"
    "اگه حال کردید، می‌تونید آیدی بدید به همدیگه\n\n"
    "😂 فقط قبل از اینکه برید تو فاز عمیق،\n"
    "یه «دخترم / پسرم» بگید که بعداً سورپرایز نشه"
)

COURSE_NAME_TEXT = (
    "✍️ اسم درس رو **خیلی دقیق و درست** بنویس\n"
    "چون قراره با همین اسم، دکمه‌ی درس توی لیست جزوه‌ها ساخته بشه 😊\n\n"
    "🔢 لطفاً **اعداد رو انگلیسی** بنویس (مثلاً 2 نه ۲)\n\n"
    "✅ مثال‌ها:\n"
    "• فیزیولوژی اعتصاب\n"
    "• کینزیولوژی 2"
)

INVITE_TEXT = (
    "بچه‌ها سلام 👋🌱\n"
    "یه ربات جزوه‌یاب برای علوم پزشکی شهید بهشتی راه افتاده که خیلی به کارمون میاد 😄\n\n"
    "✅ سرچ جزوه با اسم درس\n"
    "✅ ارسال جزوه (فقط PDF) و بعد از تایید ادمین برای همه قابل استفاده می‌شه\n"
    "✅ چت ناشناس برای آشنایی با بچه‌های دانشگاه 😂\n\n"
    "اگه جزوه دارید، لطفاً بفرستید تا دست به دست هم ترم رو نجات بدیم 💙\n\n"
    f"لینک ربات: {BOT_PUBLIC_LINK}"
)

# =========================
# Helpers
# =========================
def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def ensure_stats(uid: int):
    q("INSERT INTO user_stats (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (uid,))

def approved_count(uid: int) -> int:
    ensure_stats(uid)
    row = q("SELECT approved_uploads FROM user_stats WHERE user_id=%s", (uid,)).fetchone()
    return int(row["approved_uploads"]) if row else 0

def badge(uid: int) -> str:
    return " 🏅جزوه‌یار" if approved_count(uid) >= 1 else ""

def save_user_basic(update: Update):
    u = update.effective_user
    q("""
    INSERT INTO users (user_id, username, full_name, last_seen)
    VALUES (%s,%s,%s,NOW())
    ON CONFLICT (user_id) DO UPDATE SET
      username=EXCLUDED.username,
      full_name=EXCLUDED.full_name,
      last_seen=NOW()
    """, (u.id, u.username, (u.full_name or "").strip()))
    ensure_stats(u.id)

def user_configured(uid: int) -> bool:
    row = q("SELECT faculty, major, entry_year FROM users WHERE user_id=%s", (uid,)).fetchone()
    return bool(row and row["faculty"] and row["major"] and row["entry_year"])

# =========================
# Keyboards
# =========================
def start_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("➡️ شروع", callback_data="onboard")]])

def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔎 جستجوی جزوه", callback_data="menu_search")],
        [InlineKeyboardButton("📤 ارسال جزوه (فقط PDF)", callback_data="menu_upload")],
        [InlineKeyboardButton("💬 شروع چت ناشناس", callback_data="menu_chat")],
        [InlineKeyboardButton("📣 معرفی به دوستان", callback_data="menu_invite")],
        [InlineKeyboardButton("👤 پروفایل من", callback_data="menu_profile")],
    ])

def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗂 جزوه‌های در انتظار تایید", callback_data="admin_pending")],
        [InlineKeyboardButton("📊 آمار کاربران", callback_data="admin_stats")],
        [InlineKeyboardButton("👥 ۱۵ کاربر جدید", callback_data="admin_latest")],
        [InlineKeyboardButton("🏫 لیست دانشجوها بر اساس کلاس", callback_data="admin_classlist")],
        [InlineKeyboardButton("👤 رفتن به منوی کاربر", callback_data="go_user_menu")],
    ])

def back_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 منوی اصلی", callback_data="back_menu")]])

def faculty_kb(prefix: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(f, callback_data=f"{prefix}fac|{f}")] for f in FACULTIES]
    rows.append([InlineKeyboardButton("🔙 منوی اصلی", callback_data="back_menu")])
    return InlineKeyboardMarkup(rows)

def major_kb(prefix: str, faculty: str) -> InlineKeyboardMarkup:
    majors = MAJORS_BY_FACULTY.get(faculty, [])
    rows = [[InlineKeyboardButton(m, callback_data=f"{prefix}maj|{m}")] for m in majors]
    rows.append([InlineKeyboardButton("🔙 برگشت", callback_data=f"{prefix}back_fac")])
    return InlineKeyboardMarkup(rows)

def year_kb(prefix: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(y, callback_data=f"{prefix}year|{y}")] for y in ENTRY_YEARS]
    rows.append([InlineKeyboardButton("🔙 برگشت", callback_data=f"{prefix}back_maj")])
    return InlineKeyboardMarkup(rows)

def search_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 ارسال جزوه (فقط PDF)", callback_data="menu_upload")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_menu")]
    ])

# =========================
# Commands
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_basic(update)
    uid = update.effective_user.id

    if is_admin(uid):
        await update.message.reply_text("🛠 پنل ادمین", reply_markup=admin_menu())
        return

    await update.message.reply_text(WELCOME_TEXT, parse_mode="Markdown", reply_markup=start_kb())

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_basic(update)
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text("🛠 پنل ادمین", reply_markup=admin_menu())

# =========================
# Buttons handler
# =========================
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cq = update.callback_query
    await cq.answer()
    uid = cq.from_user.id
    save_user_basic(update)
    data = cq.data

    # ---- Admin quick jump to user menu ----
    if data == "go_user_menu":
        if not user_configured(uid):
            await cq.message.reply_text("برای شروع، اول دانشکده/رشته/ورودی رو انتخاب کن 👇", reply_markup=start_kb())
        else:
            await cq.message.reply_text("منوی اصلی 👇", reply_markup=main_menu())
        return

    # ---- back menu ----
    if data == "back_menu":
        if is_admin(uid):
            await cq.message.reply_text("🛠 پنل ادمین", reply_markup=admin_menu())
            return
        if not user_configured(uid):
            await cq.message.reply_text("برای شروع فقط چندتا انتخاب ساده داریم 👇", reply_markup=start_kb())
            return
        await cq.message.reply_text("منوی اصلی 👇", reply_markup=main_menu())
        return

    # ---- invite ----
    if data == "menu_invite":
        await cq.message.reply_text(INVITE_TEXT, reply_markup=back_menu_kb())
        return

    # ---- onboarding ----
    if data == "onboard":
        await cq.message.reply_text("🎓\nاول دانشکده‌ت رو انتخاب کن:", reply_markup=faculty_kb("usr_"))
        return

    if data.startswith("usr_fac|"):
        faculty = data.split("|", 1)[1]
        q("UPDATE users SET faculty=%s WHERE user_id=%s", (faculty, uid))
        await cq.message.reply_text("📌\nحالا رشته‌ت رو انتخاب کن:", reply_markup=major_kb("usr_", faculty))
        return

    if data == "usr_back_fac":
        await cq.message.reply_text("🎓\nاول دانشکده‌ت رو انتخاب کن:", reply_markup=faculty_kb("usr_"))
        return

    if data.startswith("usr_maj|"):
        major = data.split("|", 1)[1]
        q("UPDATE users SET major=%s WHERE user_id=%s", (major, uid))
        await cq.message.reply_text("🗓\nورودی‌ت رو انتخاب کن:", reply_markup=year_kb("usr_"))
        return

    if data == "usr_back_maj":
        row = q("SELECT faculty FROM users WHERE user_id=%s", (uid,)).fetchone()
        faculty = row["faculty"] if row and row["faculty"] else None
        if not faculty:
            await cq.message.reply_text("🎓\nاول دانشکده‌ت رو انتخاب کن:", reply_markup=faculty_kb("usr_"))
            return
        await cq.message.reply_text("📌\nحالا رشته‌ت رو انتخاب کن:", reply_markup=major_kb("usr_", faculty))
        return

    if data.startswith("usr_year|"):
        year = data.split("|", 1)[1]
        q("UPDATE users SET entry_year=%s WHERE user_id=%s", (year, uid))
        await cq.message.reply_text("✅\nهمه‌چی آماده‌ست! خوش اومدی 💙\n\nاز اینجا شروع کن 👇", reply_markup=main_menu())
        return

    # ---- Admin: pending uploads ----
    if data == "admin_pending" and is_admin(uid):
        row = q("SELECT * FROM pending_uploads WHERE status='pending' ORDER BY created_at ASC LIMIT 1").fetchone()
        if not row:
            await cq.message.reply_text("فعلاً چیزی برای تایید نداریم ✅", reply_markup=back_menu_kb())
            return
        await send_pending_to_admin(context, uid, row)
        return

    # ---- Admin: stats ----
    if data == "admin_stats" and is_admin(uid):
        total = q("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        fac_rows = q("SELECT faculty, COUNT(*) AS c FROM users GROUP BY faculty ORDER BY c DESC").fetchall() or []
        lines = [f"📊 آمار کاربران\n\n👥 کل کاربران: {total}\n"]
        for r in fac_rows:
            if r["faculty"]:
                lines.append(f"• {r['faculty']}: {r['c']}")
        await cq.message.reply_text("\n".join(lines), reply_markup=back_menu_kb())
        return

    # ---- Admin: latest users ----
    if data == "admin_latest" and is_admin(uid):
        rows = q("""
            SELECT user_id, username, full_name, faculty, major, entry_year
            FROM users
            ORDER BY created_at DESC
            LIMIT 15
        """).fetchall() or []
        if not rows:
            await cq.message.reply_text("هنوز کسی نیومده 🙂", reply_markup=back_menu_kb())
            return
        out = ["👥 ۱۵ کاربر جدید:\n"]
        for r in rows:
            out.append(f"• {r['full_name'] or 'بدون‌نام'} | @{r['username'] or '-'} | {r['user_id']}")
            out.append(f"  {r.get('faculty') or '-'} / {r.get('major') or '-'} / {r.get('entry_year') or '-'}\n")
        await cq.message.reply_text("\n".join(out), reply_markup=back_menu_kb())
        return

    # ---- Admin: class list wizard ----
    if data == "admin_classlist" and is_admin(uid):
        admin_filter_state[uid] = {"step": "faculty"}
        await cq.message.reply_text("🏫\nاول دانشکده رو انتخاب کن:", reply_markup=faculty_kb("cls_"))
        return

    if data.startswith("cls_fac|") and is_admin(uid):
        faculty = data.split("|", 1)[1]
        admin_filter_state[uid] = {"step": "major", "faculty": faculty}
        await cq.message.reply_text("حالا رشته رو انتخاب کن:", reply_markup=major_kb("cls_", faculty))
        return

    if data == "cls_back_fac" and is_admin(uid):
        admin_filter_state[uid] = {"step": "faculty"}
        await cq.message.reply_text("🏫\nاول دانشکده رو انتخاب کن:", reply_markup=faculty_kb("cls_"))
        return

    if data.startswith("cls_maj|") and is_admin(uid):
        major = data.split("|", 1)[1]
        st = admin_filter_state.get(uid, {})
        faculty = st.get("faculty")
        admin_filter_state[uid] = {"step": "year", "faculty": faculty, "major": major}
        await cq.message.reply_text("ورودی رو انتخاب کن:", reply_markup=year_kb("cls_"))
        return

    if data == "cls_back_maj" and is_admin(uid):
        st = admin_filter_state.get(uid, {})
        faculty = st.get("faculty")
        admin_filter_state[uid] = {"step": "major", "faculty": faculty}
        await cq.message.reply_text("حالا رشته رو انتخاب کن:", reply_markup=major_kb("cls_", faculty))
        return

    if data.startswith("cls_year|") and is_admin(uid):
        year = data.split("|", 1)[1]
        st = admin_filter_state.get(uid, {})
        faculty = st.get("faculty")
        major = st.get("major")
        rows = q("""
            SELECT user_id, username, full_name
            FROM users
            WHERE faculty=%s AND major=%s AND entry_year=%s
            ORDER BY created_at DESC
            LIMIT 200
        """, (faculty, major, year)).fetchall() or []
        header = f"👥 لیست دانشجوها\n\n🎓 {faculty}\n📌 {major}\n🗓 {year}\n\n"
        if not rows:
            await cq.message.reply_text(header + "فعلاً کسی تو این کلاس ثبت نشده 🙂", reply_markup=back_menu_kb())
            return
        out = [header]
        for r in rows:
            out.append(f"• {r['full_name'] or 'بدون‌نام'} | @{r['username'] or '-'} | {r['user_id']}")
        out.append("\n(حداکثر ۲۰۰ نفر نمایش داده شد)")
        await cq.message.reply_text("\n".join(out), reply_markup=back_menu_kb())
        return

    # ---- Approve/Reject buttons ----
    if data.startswith("appr|") and is_admin(uid):
        upload_id = int(data.split("|")[1])
        await approve_upload(context, uid, upload_id)
        return

    if data.startswith("rej|") and is_admin(uid):
        upload_id = int(data.split("|")[1])
        await reject_upload(context, uid, upload_id)
        return

    # ---- User menus ----
    if data == "menu_profile":
        r = q("SELECT faculty, major, entry_year FROM users WHERE user_id=%s", (uid,)).fetchone() or {}
        ap = approved_count(uid)
        await cq.message.reply_text(
            f"👤 پروفایل تو\n\n"
            f"🎓 {r.get('faculty','-')}\n"
            f"📌 {r.get('major','-')}\n"
            f"🗓 {r.get('entry_year','-')}\n\n"
            f"🏅 جزوه‌های تایید شده: {ap}",
            reply_markup=back_menu_kb()
        )
        return

    if data == "menu_search":
        if not user_configured(uid):
            await cq.message.reply_text("اول دانشکده، رشته و ورودی رو انتخاب کن 🙂", reply_markup=start_kb())
            return
        search_state[uid] = True
        await cq.message.reply_text("🔎 اسم درس رو بنویس (مثلاً: فیزیولوژی اعتصاب یا کینزیولوژی 2)", reply_markup=search_kb())
        return

    if data == "menu_upload":
        if not user_configured(uid):
            await cq.message.reply_text("اول دانشکده، رشته و ورودی رو انتخاب کن 🙂", reply_markup=start_kb())
            return
        user_state[uid] = "await_pdf"
        await cq.message.reply_text("📤 یه فایل **PDF** از جزوه رو همینجا بفرست 💙", parse_mode="Markdown", reply_markup=back_menu_kb())
        return

    # ---- Anonymous chat ----
    if data == "menu_chat":
        if not user_configured(uid):
            await cq.message.reply_text("اول دانشکده، رشته و ورودی رو انتخاب کن 🙂", reply_markup=start_kb())
            return

        q("UPDATE user_stats SET chat_used=TRUE WHERE user_id=%s", (uid,))

        if uid in active_chat:
            await cq.message.reply_text("الان توی یه چتی 🙂", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ پایان چت", callback_data="chat_end")],
                [InlineKeyboardButton("🔙 منوی اصلی", callback_data="back_menu")]
            ]))
            return

        await cq.message.reply_text(
            CHAT_INTRO_TEXT,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ بریم!", callback_data="chat_join")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="back_menu")]
            ])
        )
        return

    if data == "chat_join":
        if uid in active_chat:
            return

        if uid in waiting_queue:
            await cq.message.reply_text("تو همین الان تو صفی 😄", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ لغو انتظار", callback_data="chat_cancel")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="back_menu")]
            ]))
            return

        partner = None
        while waiting_queue:
            cand = waiting_queue.pop(0)
            if cand != uid and cand not in active_chat:
                partner = cand
                break

        if partner is None:
            waiting_queue.append(uid)
            await cq.message.reply_text(
                "⏳\nمنتظریم یه دانشجوی دیگه وصل بشه…\n\nهر وقت آماده شد، چت شروع می‌شه 🌱",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ لغو انتظار", callback_data="chat_cancel")],
                    [InlineKeyboardButton("🔙 بازگشت", callback_data="back_menu")]
                ])
            )
            return

        sid = q("INSERT INTO chat_sessions (user_a, user_b) VALUES (%s,%s) RETURNING session_id", (uid, partner)).fetchone()["session_id"]
        active_chat[uid] = partner
        active_chat[partner] = uid
        active_session[uid] = sid
        active_session[partner] = sid

        await context.bot.send_message(
            chat_id=uid,
            text=f"🎉 وصل شدی!\n\n👤 ناشناس{badge(partner)}\nمی‌تونی چت کنی 🙂",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ پایان چت", callback_data="chat_end")]])
        )
        await context.bot.send_message(
            chat_id=partner,
            text=f"🎉 وصل شدی!\n\n👤 ناشناس{badge(uid)}\nمی‌تونی چت کنی 🙂",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ پایان چت", callback_data="chat_end")]])
        )
        return

    if data == "chat_cancel":
        if uid in waiting_queue:
            waiting_queue.remove(uid)
        await cq.message.reply_text(
            "👌\nمنتظر موندن لغو شد\n\nهر وقت خواستی دوباره می‌تونی وارد چت ناشناس بشی 💬",
            reply_markup=back_menu_kb()
        )
        return

    if data == "chat_end":
        await end_chat(context, uid, ended_by=uid)
        return

    # ---- Fetch a material ----
    if data.startswith("get|"):
        mid = int(data.split("|")[1])
        mat = q("SELECT * FROM materials WHERE material_id=%s", (mid,)).fetchone()
        if not mat:
            await cq.message.reply_text("این فایل موجود نیست یا حذف شده.", reply_markup=back_menu_kb())
            return
        await context.bot.copy_message(
            chat_id=uid,
            from_chat_id=mat["archive_channel_id"],
            message_id=mat["archive_message_id"]
        )
        await cq.message.reply_text("اگه خواستی بازم سرچ کن یا جزوه بفرست 👇", reply_markup=search_kb())
        return

    # fallback
    if user_configured(uid):
        await cq.message.reply_text("منوی اصلی 👇", reply_markup=main_menu())
    else:
        await cq.message.reply_text("برای شروع فقط چندتا انتخاب ساده داریم 👇", reply_markup=start_kb())

# =========================
# Messages handler
# =========================
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_basic(update)
    uid = update.effective_user.id
    msg = update.message

    # ---- chat relay (text only) ----
    if uid in active_chat:
        partner = active_chat[uid]
        sid = active_session.get(uid)
        if msg.text:
            q("INSERT INTO chat_messages (session_id, sender_id, msg_text) VALUES (%s,%s,%s)", (sid, uid, msg.text))
            await context.bot.send_message(chat_id=partner, text=msg.text)
        else:
            await context.bot.send_message(chat_id=partner, text="(فعلاً تو چت ناشناس فقط متن پشتیبانی می‌شه 🙂)")
        return

    # ---- search flow ----
    if search_state.get(uid):
        if not msg.text:
            return
        search_state[uid] = False
        query_text = msg.text.strip()

        user = q("SELECT faculty, major FROM users WHERE user_id=%s", (uid,)).fetchone()
        rows = q("""
            SELECT material_id, course_name, professor_name
            FROM materials
            WHERE faculty=%s AND major=%s AND course_name ILIKE %s
            ORDER BY created_at DESC
            LIMIT 20
        """, (user["faculty"], user["major"], f"%{query_text}%")).fetchall() or []

        if not rows:
            await msg.reply_text(
                "چیزی پیدا نشد 😕\n"
                "اگه جزوه این درس رو داری، همینجا بفرست تا به بقیه هم کمک بشه 💙",
                reply_markup=search_kb()
            )
            return

        buttons = []
        for r in rows:
            prof = (r["professor_name"] or "").strip()
            title = f"📄 {r['course_name']} — {prof}" if prof else f"📄 {r['course_name']}"
            buttons.append([InlineKeyboardButton(title, callback_data=f"get|{r['material_id']}")])

        buttons.append([InlineKeyboardButton("📤 ارسال جزوه (فقط PDF)", callback_data="menu_upload")])
        buttons.append([InlineKeyboardButton("🔙 بازگشت", callback_data="back_menu")])
        await msg.reply_text("نتیجه‌ها 👇", reply_markup=InlineKeyboardMarkup(buttons))
        return

    # ---- upload flow ----
    st = user_state.get(uid)

    if st == "await_pdf":
        if not msg.document:
            await msg.reply_text("فقط فایل **PDF** رو بفرست لطفاً 💙", parse_mode="Markdown", reply_markup=back_menu_kb())
            return

        filename = (msg.document.file_name or "").lower()
        if not filename.endswith(".pdf"):
            await msg.reply_text(
                "😊\nبرای اینکه جزوه‌ها مرتب و قابل استفاده باشن، فعلاً فقط فایل‌های **PDF** رو قبول می‌کنیم.\n"
                "لطفاً نسخه PDF جزوه رو بفرست 💙",
                parse_mode="Markdown",
                reply_markup=back_menu_kb()
            )
            return

        u = q("SELECT faculty, major, entry_year FROM users WHERE user_id=%s", (uid,)).fetchone()
        tmp[uid] = {
            "user_chat_id": msg.chat_id,
            "user_message_id": msg.message_id,
            "faculty": u["faculty"],
            "major": u["major"],
            "entry_year": u["entry_year"],
        }
        user_state[uid] = "await_course"
        await msg.reply_text(COURSE_NAME_TEXT, parse_mode="Markdown", reply_markup=back_menu_kb())
        return

    if st == "await_course":
        if not msg.text:
            return
        tmp[uid]["course_name"] = msg.text.strip()
        user_state[uid] = "await_prof"
        await msg.reply_text("اسم استاد رو هم بنویس (اگه نداری یه خط تیره بفرست) 🙂", reply_markup=back_menu_kb())
        return

    if st == "await_prof":
        if not msg.text:
            return
        prof = msg.text.strip()
        if prof in ["-", "—"]:
            prof = None

        data = tmp[uid]
        upload_id = q("""
            INSERT INTO pending_uploads
            (submitter_id, faculty, major, entry_year, course_name, professor_name, user_chat_id, user_message_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING upload_id
        """, (uid, data["faculty"], data["major"], data["entry_year"], data["course_name"], prof, data["user_chat_id"], data["user_message_id"])).fetchone()["upload_id"]

        user_state.pop(uid, None)
        tmp.pop(uid, None)

        await msg.reply_text(
            "📩 جزوه‌ت رسید!\n"
            "بعد از تایید ادمین توی جزوه‌یاب قرار می‌گیره 💙\n"
            "ممنون که کمک می‌کنی 🌱",
            reply_markup=main_menu()
        )

        # notify admins
        for aid in ADMIN_IDS:
            try:
                row = q("SELECT * FROM pending_uploads WHERE upload_id=%s", (upload_id,)).fetchone()
                await send_pending_to_admin(context, aid, row)
            except Exception:
                pass
        return

    # fallback
    if user_configured(uid):
        await msg.reply_text("از منوی زیر انتخاب کن 👇", reply_markup=main_menu())
    else:
        await msg.reply_text("برای شروع فقط چندتا انتخاب ساده داریم 👇", reply_markup=start_kb())

# =========================
# Admin approve/reject
# =========================
async def send_pending_to_admin(context: ContextTypes.DEFAULT_TYPE, admin_chat_id: int, row: dict):
    user = q("SELECT user_id, username, full_name FROM users WHERE user_id=%s", (row["submitter_id"],)).fetchone()
    ap = approved_count(row["submitter_id"])
    prof = row["professor_name"] or "-"

    # copy the PDF to admin
    await context.bot.copy_message(
        chat_id=admin_chat_id,
        from_chat_id=row["user_chat_id"],
        message_id=row["user_message_id"]
    )

    await context.bot.send_message(
        chat_id=admin_chat_id,
        text=(
            "🗂 جزوه در انتظار تایید\n\n"
            f"👤 فرستنده: {user.get('full_name') or 'بدون‌نام'} | @{user.get('username') or '-'} | {user['user_id']}\n"
            f"🎓 {row['faculty']} / {row['major']} / {row['entry_year']}\n"
            f"📚 درس: {row['course_name']}\n"
            f"👨‍🏫 استاد: {prof}\n"
            f"🏅 جزوه‌های تایید شده قبلی: {ap}\n"
        ),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ تایید", callback_data=f"appr|{row['upload_id']}"),
             InlineKeyboardButton("❌ رد", callback_data=f"rej|{row['upload_id']}")]
        ])
    )

async def approve_upload(context: ContextTypes.DEFAULT_TYPE, admin_chat_id: int, upload_id: int):
    row = q("SELECT * FROM pending_uploads WHERE upload_id=%s AND status='pending'", (upload_id,)).fetchone()
    if not row:
        await context.bot.send_message(chat_id=admin_chat_id, text="این مورد قبلاً بررسی شده یا وجود ندارد.")
        return

    copied: Message = await context.bot.copy_message(
        chat_id=ARCHIVE_CHANNEL_ID,
        from_chat_id=row["user_chat_id"],
        message_id=row["user_message_id"]
    )

    q("""
        INSERT INTO materials (faculty, major, entry_year, course_name, professor_name,
                               archive_channel_id, archive_message_id, added_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
    """, (row["faculty"], row["major"], row["entry_year"], row["course_name"], row["professor_name"],
          ARCHIVE_CHANNEL_ID, copied.message_id, row["submitter_id"]))

    q("UPDATE pending_uploads SET status='approved' WHERE upload_id=%s", (upload_id,))
    q("""
        INSERT INTO user_stats (user_id, approved_uploads)
        VALUES (%s, 1)
        ON CONFLICT (user_id) DO UPDATE SET approved_uploads = user_stats.approved_uploads + 1
    """, (row["submitter_id"],))

    await context.bot.send_message(chat_id=admin_chat_id, text="✅ تایید شد و به آرشیو رفت.")
    try:
        await context.bot.send_message(
            chat_id=row["submitter_id"],
            text="🎉 جزوه‌ت تایید شد!\nمرسی که به بچه‌ها کمک می‌کنی 💙\nحالا تو چت ناشناس کنار اسمت مدال 🏅 داری 😄",
            reply_markup=main_menu()
        )
    except Exception:
        pass

async def reject_upload(context: ContextTypes.DEFAULT_TYPE, admin_chat_id: int, upload_id: int):
    row = q("SELECT * FROM pending_uploads WHERE upload_id=%s AND status='pending'", (upload_id,)).fetchone()
    if not row:
        await context.bot.send_message(chat_id=admin_chat_id, text="این مورد قبلاً بررسی شده یا وجود ندارد.")
        return

    q("UPDATE pending_uploads SET status='rejected' WHERE upload_id=%s", (upload_id,))
    await context.bot.send_message(chat_id=admin_chat_id, text="❌ رد شد.")
    try:
        await context.bot.send_message(
            chat_id=row["submitter_id"],
            text="🌱 جزوه‌ت فعلاً تایید نشد.\nاگه دوست داشتی یه نسخه مرتب‌تر/واضح‌تر دوباره بفرست 💙",
            reply_markup=main_menu()
        )
    except Exception:
        pass

# =========================
# End chat
# =========================
async def end_chat(context: ContextTypes.DEFAULT_TYPE, uid: int, ended_by: int):
    if uid in waiting_queue:
        waiting_queue.remove(uid)

    if uid not in active_chat:
        return

    partner = active_chat.get(uid)
    sid = active_session.get(uid)

    for u in [uid, partner]:
        active_chat.pop(u, None)
        active_session.pop(u, None)

    q("UPDATE chat_sessions SET status='ended', ended_at=NOW() WHERE session_id=%s", (sid,))

    try:
        await context.bot.send_message(
            chat_id=ended_by,
            text="👋 چت رو تموم کردی.\nاگه دوست داشتی دوباره می‌تونی چت جدید شروع کنی 😄",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💬 چت ناشناس جدید", callback_data="menu_chat")],
                [InlineKeyboardButton("🔙 منوی اصلی", callback_data="back_menu")]
            ])
        )
    except Exception:
        pass

    try:
        await context.bot.send_message(
            chat_id=partner,
            text="⚠️ طرف مقابل از چت خارج شد.\nاگه دوست داشتی دوباره می‌تونی چت جدید شروع کنی 🙂",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💬 چت ناشناس جدید", callback_data="menu_chat")],
                [InlineKeyboardButton("🔙 منوی اصلی", callback_data="back_menu")]
            ])
        )
    except Exception:
        pass

# =========================
# Error handler
# =========================
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    if isinstance(err, NetworkError):
        return

# =========================
# Run
# =========================
def run_bot():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, on_message))
    app.add_error_handler(on_error)

    # IMPORTANT for Render (avoid signal handler in thread)
    app.run_polling(stop_signals=None)
