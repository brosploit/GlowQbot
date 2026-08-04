import os
import re
import random
import string
import logging
import sqlite3
from datetime import datetime
from collections import deque
from contextlib import closing

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BotCommand,
)
from telegram.constants import ChatAction
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# Configuration

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8217918188:AAHA49a4vT5P5t-t-C8sS52-A7gxDmbFBBE").strip()
DB_PATH = os.environ.get("PROFILE_DB_PATH", "profiles.db")
DEFAULT_ADMIN_PASSWORD = os.environ.get("ADMIN_PANEL_PASSWORD", "changeme123")
LOG_FILE_PATH = os.environ.get("BOT_LOG_FILE", "bot.log")

# ---- Credits / referral / redeem economy -----------------------------------
CONNECT_REQUEST_COST = 5        # credits spent sending a Connect-by-ID request
SET_PREFERENCES_COST = 3        # credits spent saving a "specific" (non-Anyone) preference
REFERRAL_BONUS_REFERRER = 10    # credits the referrer earns when their invite completes setup
REFERRAL_BONUS_NEW_USER = 3     # welcome credits for someone who signed up via a referral link

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.ERROR,
    handlers=[logging.FileHandler(LOG_FILE_PATH, encoding="utf-8")],
)
logger = logging.getLogger(__name__)

conn_logger = logging.getLogger("bot.connections")
conn_logger.setLevel(logging.INFO)
conn_logger.addHandler(logging.FileHandler(LOG_FILE_PATH, encoding="utf-8"))
conn_logger.propagate = False

# Conversation states
USERNAME, NAME, AGE, PHOTO, BIO, GENDER, ORIENTATION, LOCATION = range(8)
EDIT_MENU, EDIT_NAME, EDIT_PHOTO, EDIT_BIO, EDIT_GENDER, EDIT_ORIENTATION = range(100, 106)
DELETE_CONFIRM = 200
PREF_MODE, PREF_GENDERS, PREF_ORIENTATIONS = range(300, 303)
ENTER_ID_WAIT = 400
REPORT_TARGET, REPORT_REASON = range(700, 702)
(ADMIN_AUTH, ADMIN_MENU, ADMIN_BAN_INPUT, ADMIN_UNBAN_INPUT,
 ADMIN_EDIT_TERMS, ADMIN_CHANGE_PW, ADMIN_BROADCAST_INPUT, ADMIN_VIEW_INPUT,
 ADMIN_CREDITS_TARGET, ADMIN_CREDITS_AMOUNT,
 ADMIN_REDEEM_AMOUNT, ADMIN_REDEEM_USES, ADMIN_NOTICE_INPUT,
 ADMIN_MARKET_MENU, ADMIN_MARKET_ADD_CHATID, ADMIN_MARKET_ADD_TITLE,
 ADMIN_MARKET_ADD_DESC, ADMIN_MARKET_ADD_COST, ADMIN_MARKET_REMOVE_INPUT,
 ADMIN_MARKET_COST_ID, ADMIN_MARKET_COST_VALUE) = range(800, 821)
REDEEM_INPUT = 900

GENDER_OPTIONS = ["Male", "Female", "Transgender", "Non-binary", "Other"]
ORIENTATION_OPTIONS = ["Straight", "Gay", "Lesbian", "Bisexual", "Pansexual", "Asexual",
                        "Sissy", "Tomboy", "Other"]
FEMALE_ASSOC_ORIENTATIONS = {"Lesbian", "Tomboy"}
MALE_ASSOC_ORIENTATIONS = {"Gay", "Sissy"}
PREF_GENDER_OPTIONS = ["Male", "Female", "Transgender", "Non-binary", "Other"]
PREF_ORIENTATION_OPTIONS = [o for o in ORIENTATION_OPTIONS if o != "Prefer not to say"]
EDITABLE_FIELDS = ["Name", "Photo", "Bio", "Gender", "Orientation"]
ACCEPT_BTN = "✅ Accept"
DECLINE_BTN = "❌ Decline"


# Buttons & keyboards

BTN_SETUP = "📝 Setup Profile"
BTN_START = "🚀 Start Chat"
BTN_NEXT = "⏭ Next Chat"
BTN_STOP = "🛑 Stop Chat"
BTN_MORE = "☰ More"
BTN_BACK = "🔙 Back"
BTN_CANCEL = "❌ Cancel"
BTN_ENTER_ID = "🔑 Connect by ID"
BTN_PREFERENCES = "⚙️ Preferences"
BTN_EDIT = "✏️ Edit Profile"
BTN_MY_PROFILE = "👤 My Profile"
BTN_DELETE = "🗑 Delete Profile"
BTN_REPORT = "🚩 Report User"
BTN_TERMS = "📜 Terms & Conditions"
BTN_HELP = "ℹ️ Help"
BTN_CREDITS = "💰 My Credits"
BTN_REFERRAL = "🎁 Refer & Earn"
BTN_REDEEM = "🎟 Redeem Code"
BTN_MARKETPLACE = "🛒 Marketplace"
BTN_NOTICES = "🔔 Notices"

# ---- Admin panel buttons (bottom keyboard, matches the main menu's style) ----
ABTN_VIEW = "🔎 View User"
ABTN_EXPORT = "📊 Export Users"
ABTN_BACKUP = "💾 Backup Database"
ABTN_REPORTS = "📋 View Reports"
ABTN_REPORTS_CLEAR = "🧹 Clear Reports"
ABTN_BAN = "🚫 Ban User"
ABTN_UNBAN = "✅ Unban User"
ABTN_CREDITS = "💳 Edit Credits"
ABTN_REDEEM_NEW = "🎟 New Redeem Code"
ABTN_REDEEM_LIST = "📋 List Redeem Codes"
ABTN_BROADCAST = "📣 Broadcast"
ABTN_NOTICE_NEW = "📢 Post Notice"
ABTN_EDIT_TERMS = "📜 Edit Terms & Conditions"
ABTN_CHANGE_PW = "🔑 Change Password"
ABTN_MARKET_MENU = "🛍 Marketplace Admin"
ABTN_MARKET_ADD = "➕ Add Group"
ABTN_MARKET_REMOVE = "🗑 Remove Group"
ABTN_MARKET_LIST = "📋 List Groups"
ABTN_MARKET_COST = "💰 Edit Group Cost"
ABTN_BACK = "🔙 Back"
ABTN_CLOSE = "❌ Close Admin Panel"

PRE_SETUP_KEYBOARD = ReplyKeyboardMarkup(
    [[BTN_SETUP], [BTN_HELP]],
    resize_keyboard=True,
)

# Shown once a profile exists. Setup never reappears here.
MAIN_MENU_KEYBOARD = ReplyKeyboardMarkup(
    [
        [BTN_START, BTN_STOP],
        [BTN_NEXT, BTN_MORE],
    ],
    resize_keyboard=True,
)

# Shown while actively paired with a partner — swaps in Report, drops Start.
IN_CHAT_KEYBOARD = ReplyKeyboardMarkup(
    [[BTN_NEXT, BTN_STOP], [BTN_REPORT]],
    resize_keyboard=True,
)

MORE_MENU_KEYBOARD = ReplyKeyboardMarkup(
    [
        [BTN_MY_PROFILE, BTN_EDIT],
        [BTN_PREFERENCES, BTN_ENTER_ID],
        [BTN_CREDITS, BTN_REFERRAL],
        [BTN_REDEEM, BTN_MARKETPLACE],
        [BTN_NOTICES, BTN_REPORT],
        [BTN_TERMS, BTN_DELETE],
        [BTN_HELP],
        [BTN_BACK],
    ],
    resize_keyboard=True,
)

def build_admin_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(ABTN_VIEW, callback_data="adm:view"),
         InlineKeyboardButton(ABTN_BAN, callback_data="adm:ban"),
         InlineKeyboardButton(ABTN_UNBAN, callback_data="adm:unban")],
        [InlineKeyboardButton(ABTN_CREDITS, callback_data="adm:credits")],
        [InlineKeyboardButton(ABTN_REDEEM_NEW, callback_data="adm:redeem_new"),
         InlineKeyboardButton(ABTN_REDEEM_LIST, callback_data="adm:redeem_list")],
        [InlineKeyboardButton(ABTN_REPORTS, callback_data="adm:reports"),
         InlineKeyboardButton(ABTN_REPORTS_CLEAR, callback_data="adm:reports_clear")],
        [InlineKeyboardButton(ABTN_BROADCAST, callback_data="adm:broadcast"),
         InlineKeyboardButton(ABTN_NOTICE_NEW, callback_data="adm:notice")],
        [InlineKeyboardButton(ABTN_MARKET_MENU, callback_data="adm:market")],
        [InlineKeyboardButton(ABTN_EXPORT, callback_data="adm:export"),
         InlineKeyboardButton(ABTN_BACKUP, callback_data="adm:backup")],
        [InlineKeyboardButton(ABTN_EDIT_TERMS, callback_data="adm:terms"),
         InlineKeyboardButton(ABTN_CHANGE_PW, callback_data="adm:pw")],
        [InlineKeyboardButton("🔄 Refresh", callback_data="adm:refresh"),
         InlineKeyboardButton(ABTN_CLOSE, callback_data="adm:close")],
    ])


def build_admin_market_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(ABTN_MARKET_ADD, callback_data="adm:mk:add"),
         InlineKeyboardButton(ABTN_MARKET_REMOVE, callback_data="adm:mk:remove")],
        [InlineKeyboardButton(ABTN_MARKET_LIST, callback_data="adm:mk:list"),
         InlineKeyboardButton(ABTN_MARKET_COST, callback_data="adm:mk:cost")],
        [InlineKeyboardButton(ABTN_BACK, callback_data="adm:mk:back")],
    ])


def build_admin_back_keyboard(callback_data: str = "adm:back") -> InlineKeyboardMarkup:
    """Inline 'Back' button shown on every admin free-text prompt, so the admin
    can bail out to the panel without typing a throwaway value."""
    return InlineKeyboardMarkup([[InlineKeyboardButton(ABTN_BACK, callback_data=callback_data)]])


ADMIN_AUTH_CANCEL_KEYBOARD = InlineKeyboardMarkup(
    [[InlineKeyboardButton(BTN_CANCEL, callback_data="adm:cancel_auth")]]
)

# Shown during any free-text prompt so the user can always back out.
CANCEL_ONLY_KEYBOARD = ReplyKeyboardMarkup([[BTN_CANCEL]], resize_keyboard=True)

PUBLIC_COMMANDS = [
    BotCommand("start", "Open the main menu"),
    BotCommand("setup", "Create your profile"),
    BotCommand("profile", "View your profile"),
    BotCommand("edit", "Edit your profile"),
    BotCommand("delete", "Delete your profile"),
    BotCommand("find", "Find a random chat partner"),
    BotCommand("next", "Skip to a new partner"),
    BotCommand("stop", "End the current chat"),
    BotCommand("connect", "Connect using a 6-digit ID"),
    BotCommand("preferences", "Choose who you match with"),
    BotCommand("credits", "View your credit balance"),
    BotCommand("referral", "Get your referral link and earn credits"),
    BotCommand("redeem", "Redeem a code for credits"),
    BotCommand("marketplace", "Spend credits to unlock group access"),
    BotCommand("notices", "View the latest notices & updates"),
    BotCommand("report", "Report a user"),
    BotCommand("terms", "View Terms & Conditions"),
    BotCommand("help", "Show help"),
]

def keyboard_for_user(user_id: int) -> ReplyKeyboardMarkup:
    """The persistent bottom keyboard appropriate for this user's current state."""
    if is_in_chat(user_id):
        return IN_CHAT_KEYBOARD
    return MAIN_MENU_KEYBOARD if profile_exists(user_id) else PRE_SETUP_KEYBOARD

def orientation_options_for_gender(gender: str):
    if gender == "Male":
        return [o for o in ORIENTATION_OPTIONS if o not in FEMALE_ASSOC_ORIENTATIONS]
    if gender == "Female":
        return [o for o in ORIENTATION_OPTIONS if o not in MALE_ASSOC_ORIENTATIONS]
    return ORIENTATION_OPTIONS

def rows_of(options, size=2):
    return [options[i:i + size] for i in range(0, len(options), size)]


def with_cancel(rows):
    """Append a Cancel row to a list of keyboard rows."""
    return rows + [[BTN_CANCEL]]

# Database helpers

def init_db() -> None:
    """Create any missing tables. Never drops or alters existing data."""
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                user_id           INTEGER PRIMARY KEY,
                connect_id        TEXT UNIQUE NOT NULL,
                username          TEXT UNIQUE NOT NULL,
                name              TEXT NOT NULL,
                age               INTEGER NOT NULL,
                photo_file_id     TEXT NOT NULL,
                bio               TEXT NOT NULL,
                gender            TEXT NOT NULL,
                orientation       TEXT NOT NULL,
                location          TEXT NOT NULL,
                pref_mode         TEXT NOT NULL DEFAULT 'anyone',
                pref_genders      TEXT NOT NULL DEFAULT '',
                pref_orientations TEXT NOT NULL DEFAULT '',
                credits           INTEGER NOT NULL DEFAULT 0,
                referred_by       INTEGER,
                created_at        TEXT DEFAULT (datetime('now')),
                updated_at        TEXT DEFAULT (datetime('now'))
            )
            """
        )
        existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(profiles)").fetchall()}
        if "credits" not in existing_columns:
            conn.execute("ALTER TABLE profiles ADD COLUMN credits INTEGER NOT NULL DEFAULT 0")
        if "referred_by" not in existing_columns:
            conn.execute("ALTER TABLE profiles ADD COLUMN referred_by INTEGER")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS redeem_codes (
                code         TEXT PRIMARY KEY,
                credit_value INTEGER NOT NULL,
                max_uses     INTEGER NOT NULL,
                uses_left    INTEGER NOT NULL,
                created_at   TEXT DEFAULT (datetime('now')),
                active       INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS redemptions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                code        TEXT NOT NULL,
                user_id     INTEGER NOT NULL,
                redeemed_at TEXT DEFAULT (datetime('now')),
                UNIQUE(code, user_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bans (
                user_id    INTEGER PRIMARY KEY,
                banned_at  TEXT DEFAULT (datetime('now')),
                reason     TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reports (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                reporter_id  INTEGER,
                reported_id  INTEGER,
                reason       TEXT,
                status       TEXT DEFAULT 'open',
                created_at   TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        # ---- thumbs up/down rating system ----
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ratings (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                rater_id    INTEGER NOT NULL,
                rated_id    INTEGER NOT NULL,
                is_positive INTEGER NOT NULL,
                created_at  TEXT DEFAULT (datetime('now'))
            )
            """
        )
        # ---- group marketplace ----
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_groups (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id     INTEGER NOT NULL,
                title       TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                cost        INTEGER NOT NULL,
                active      INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_redemptions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL,
                group_id     INTEGER NOT NULL,
                invite_link  TEXT,
                redeemed_at  TEXT DEFAULT (datetime('now'))
            )
            """
        )
        # ---- notices / updates board ----
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notices (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                text       TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_profiles_referred_by ON profiles(referred_by)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_redemptions_user ON redemptions(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ratings_rated ON ratings(rated_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_market_groups_active ON market_groups(active)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_market_redemptions_user ON market_redemptions(user_id)")
        conn.commit()

def db(sql, params=(), fetch=None):
    """Tiny DB helper: fetch='one'|'all'|'val' or None (write + commit)."""
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql, params)
        if fetch == "one":
            row = cur.fetchone()
            return dict(row) if row else None
        if fetch == "all":
            return [dict(r) for r in cur.fetchall()]
        if fetch == "val":
            row = cur.fetchone()
            return row[0] if row else None
        conn.commit()

def profile_exists(user_id: int) -> bool:
    return db("SELECT 1 FROM profiles WHERE user_id = ?", (user_id,), "val") is not None

def username_exists(username: str) -> bool:
    return db("SELECT 1 FROM profiles WHERE lower(username) = lower(?)", (username,), "val") is not None

def get_profile(user_id: int):
    return db("SELECT * FROM profiles WHERE user_id = ?", (user_id,), "one")

def get_profile_by_username(username: str):
    return db("SELECT * FROM profiles WHERE lower(username) = lower(?)", (username.lstrip("@"),), "one")

def get_profile_by_connect_id(connect_id: str):
    return db("SELECT * FROM profiles WHERE connect_id = ?", (connect_id,), "one")

def get_all_user_ids():
    return [r["user_id"] for r in db("SELECT user_id FROM profiles", fetch="all")]

def _generate_unique_connect_id() -> str:
    while True:
        candidate = "".join(random.choices(string.digits, k=6))
        if not db("SELECT 1 FROM profiles WHERE connect_id = ?", (candidate,), "val"):
            return candidate

def create_profile(user_id, username, name, age, photo_file_id, bio, gender, orientation, location, referred_by=None) -> str:
    connect_id = _generate_unique_connect_id()
    db(
        """INSERT INTO profiles (user_id, connect_id, username, name, age, photo_file_id, bio, gender, orientation, location, referred_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, connect_id, username, name, age, photo_file_id, bio, gender, orientation, location, referred_by),
    )
    return connect_id

# ---- credits ----------------------------------------------------------------
def get_credits(user_id: int) -> int:
    return db("SELECT credits FROM profiles WHERE user_id = ?", (user_id,), "val") or 0

def add_credits(user_id: int, amount: int) -> None:
    """Add (or, with a negative amount, remove) credits. Balance never goes below 0."""
    db(
        "UPDATE profiles SET credits = MAX(0, credits + ?), updated_at = datetime('now') WHERE user_id = ?",
        (amount, user_id),
    )

def set_credits(user_id: int, amount: int) -> None:
    db("UPDATE profiles SET credits = ?, updated_at = datetime('now') WHERE user_id = ?", (max(0, amount), user_id))

def spend_credits(user_id: int, amount: int) -> bool:
    """Atomically deduct `amount` credits if the user can afford it. Returns True on success."""
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute("UPDATE profiles SET credits = credits - ?, updated_at = datetime('now') "
                            "WHERE user_id = ? AND credits >= ?", (amount, user_id, amount))
        conn.commit()
        return cur.rowcount > 0

# ---- referrals ----------------------------------------------------------------
def count_referrals(user_id: int) -> int:
    return db("SELECT COUNT(*) FROM profiles WHERE referred_by = ?", (user_id,), "val") or 0

# ---- redeem codes -------------------------------------------------------------
def _generate_unique_redeem_code() -> str:
    while True:
        candidate = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        if not db("SELECT 1 FROM redeem_codes WHERE code = ?", (candidate,), "val"):
            return candidate

def create_redeem_code(credit_value: int, max_uses: int) -> str:
    code = _generate_unique_redeem_code()
    db(
        "INSERT INTO redeem_codes (code, credit_value, max_uses, uses_left) VALUES (?, ?, ?, ?)",
        (code, credit_value, max_uses, max_uses),
    )
    return code

def get_redeem_code(code: str):
    return db("SELECT * FROM redeem_codes WHERE code = ?", (code.strip().upper(),), "one")

def get_active_redeem_codes(limit: int = 20):
    return db("SELECT * FROM redeem_codes WHERE active = 1 AND uses_left > 0 ORDER BY created_at DESC LIMIT ?",
              (limit,), "all")

def redeem_code_for_user(code: str, user_id: int):
    """Returns (success: bool, message: str, credit_value: int)."""
    code = code.strip().upper()
    entry = get_redeem_code(code)
    if not entry or not entry["active"] or entry["uses_left"] <= 0:
        return False, "❌ That redeem code is invalid, expired, or has already been fully used.", 0
    already = db("SELECT 1 FROM redemptions WHERE code = ? AND user_id = ?", (code, user_id), "val")
    if already:
        return False, "⚠️ You've already redeemed this code.", 0
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute(
            "UPDATE redeem_codes SET uses_left = uses_left - 1 WHERE code = ? AND uses_left > 0", (code,)
        )
        if cur.rowcount == 0:
            conn.commit()
            return False, "❌ That redeem code has just run out of uses.", 0
        conn.execute("INSERT INTO redemptions (code, user_id) VALUES (?, ?)", (code, user_id))
        conn.execute("UPDATE profiles SET credits = credits + ?, updated_at = datetime('now') WHERE user_id = ?",
                     (entry["credit_value"], user_id))
        conn.commit()
    return True, f"✅ Redeemed! +{entry['credit_value']} credits added to your balance.", entry["credit_value"]

_ALLOWED_EDIT_COLUMNS = {"name", "photo_file_id", "bio", "gender", "orientation"}


def update_profile_field(user_id: int, column: str, value) -> None:
    if column not in _ALLOWED_EDIT_COLUMNS:
        raise ValueError(f"Column '{column}' is not editable")
    db(f"UPDATE profiles SET {column} = ?, updated_at = datetime('now') WHERE user_id = ?", (value, user_id))


def set_preferences(user_id: int, mode: str, genders: list, orientations: list) -> None:
    db(
        "UPDATE profiles SET pref_mode = ?, pref_genders = ?, pref_orientations = ?, updated_at = datetime('now') WHERE user_id = ?",
        (mode, ",".join(genders), ",".join(orientations), user_id),
    )

def delete_profile(user_id: int) -> None:
    db("DELETE FROM profiles WHERE user_id = ?", (user_id,))

def export_profiles_to_excel(path: str) -> int:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Users"
    ws.append(["User ID", "Telegram ID", "Username", "Name", "Age", "Connect ID", "Credits",
               "Referred By", "Gender", "Orientation", "Location", "Pref Mode", "Pref Genders",
               "Pref Orientations", "Bio", "Joined", "Banned"])
    rows = db("SELECT * FROM profiles ORDER BY created_at", fetch="all")
    for r in rows:
        ws.append([r["user_id"], r["user_id"], r["username"], r["name"], r["age"], r["connect_id"],
                   r["credits"], r["referred_by"] or "", r["gender"], r["orientation"], r["location"],
                   r["pref_mode"], r["pref_genders"], r["pref_orientations"], r["bio"], r["created_at"],
                   "Yes" if is_banned(r["user_id"]) else "No"])
    wb.save(path)
    return len(rows)

# ---- admin_settings (password, terms text) --------------------------------
def get_setting(key: str, default=None):
    val = db("SELECT value FROM admin_settings WHERE key = ?", (key,), "val")
    return val if val is not None else default

def set_setting(key: str, value: str) -> None:
    db("INSERT INTO admin_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
       (key, value))

def get_admin_password() -> str:
    return get_setting("admin_password", DEFAULT_ADMIN_PASSWORD)

def set_admin_password(new_password: str) -> None:
    set_setting("admin_password", new_password)

def get_terms_text() -> str:
    return get_setting("terms_text", "No terms have been set yet. Please contact the admin @bytecrackbot.")

def set_terms_text(text: str) -> None:
    set_setting("terms_text", text)

# ---- bans -------------------------------------------------------------------
def is_banned(user_id: int) -> bool:
    return db("SELECT 1 FROM bans WHERE user_id = ?", (user_id,), "val") is not None

def ban_user(user_id: int, reason: str = "") -> None:
    db("INSERT OR REPLACE INTO bans (user_id, reason) VALUES (?, ?)", (user_id, reason))

def unban_user(user_id: int) -> None:
    db("DELETE FROM bans WHERE user_id = ?", (user_id,))

def count_banned() -> int:
    return db("SELECT COUNT(*) FROM bans", fetch="val")

def count_profiles() -> int:
    return db("SELECT COUNT(*) FROM profiles", fetch="val")

def resolve_user_id_from_input(text: str):
    text = text.strip()
    if re.fullmatch(r"\d{6}", text):
        profile = get_profile_by_connect_id(text)
        return profile["user_id"] if profile else None
    if text.isdigit():
        return int(text)
    return None

# ---- reports ------------------------------------------------------------------
def save_report(reporter_id: int, reported_id: int, reason: str) -> None:
    db("INSERT INTO reports (reporter_id, reported_id, reason) VALUES (?, ?, ?)", (reporter_id, reported_id, reason))

def get_open_reports(limit: int = 10):
    return db("SELECT * FROM reports WHERE status = 'open' ORDER BY created_at DESC LIMIT ?", (limit,), "all")

def count_open_reports() -> int:
    return db("SELECT COUNT(*) FROM reports WHERE status = 'open'", fetch="val")

def clear_open_reports() -> None:
    db("UPDATE reports SET status = 'reviewed' WHERE status = 'open'")

# ---- ratings (thumbs up / down) -----------------------------------------------
def record_rating(rater_id: int, rated_id: int, is_positive: bool) -> None:
    db("INSERT INTO ratings (rater_id, rated_id, is_positive) VALUES (?, ?, ?)",
       (rater_id, rated_id, 1 if is_positive else 0))

def get_rating_stats(user_id: int):
    """Returns (percent_positive_or_None, total_ratings)."""
    total = db("SELECT COUNT(*) FROM ratings WHERE rated_id = ?", (user_id,), "val") or 0
    if total == 0:
        return None, 0
    positive = db("SELECT COUNT(*) FROM ratings WHERE rated_id = ? AND is_positive = 1", (user_id,), "val") or 0
    return round(positive * 100 / total), total

def rating_line_for(user_id: int) -> str:
    percent, total = get_rating_stats(user_id)
    if total == 0:
        return "⭐ Rating: No ratings yet"
    return f"⭐ Rating: 👍 {percent}% positive ({total} rating{'s' if total != 1 else ''})"

# ---- group marketplace ---------------------------------------------------------
def add_market_group(chat_id: int, title: str, description: str, cost: int) -> int:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute(
            "INSERT INTO market_groups (chat_id, title, description, cost) VALUES (?, ?, ?, ?)",
            (chat_id, title, description, cost),
        )
        conn.commit()
        return cur.lastrowid

def get_market_group(group_id: int):
    return db("SELECT * FROM market_groups WHERE id = ?", (group_id,), "one")

def list_market_groups(active_only: bool = True):
    if active_only:
        return db("SELECT * FROM market_groups WHERE active = 1 ORDER BY created_at DESC", fetch="all")
    return db("SELECT * FROM market_groups ORDER BY created_at DESC", fetch="all")

def set_market_group_active(group_id: int, active: bool) -> None:
    db("UPDATE market_groups SET active = ? WHERE id = ?", (1 if active else 0, group_id))

def set_market_group_cost(group_id: int, cost: int) -> None:
    db("UPDATE market_groups SET cost = ? WHERE id = ?", (cost, group_id))

def delete_market_group(group_id: int) -> None:
    db("DELETE FROM market_groups WHERE id = ?", (group_id,))

def record_market_redemption(user_id: int, group_id: int, invite_link: str) -> None:
    db("INSERT INTO market_redemptions (user_id, group_id, invite_link) VALUES (?, ?, ?)",
       (user_id, group_id, invite_link))

def has_redeemed_group(user_id: int, group_id: int) -> bool:
    return db("SELECT 1 FROM market_redemptions WHERE user_id = ? AND group_id = ?",
               (user_id, group_id), "val") is not None

# ---- notices / updates board ----------------------------------------------------
def add_notice(text: str) -> int:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute("INSERT INTO notices (text) VALUES (?)", (text,))
        conn.commit()
        return cur.lastrowid

def get_latest_notices(limit: int = 10):
    return db("SELECT * FROM notices ORDER BY created_at DESC LIMIT ?", (limit,), "all")

# In-memory chat-pairing & admin-session state

waiting_queue = deque()
active_chats = {}
pending_requests = {}
authenticated_admins = set()

def is_in_chat(user_id: int) -> bool:
    return user_id in active_chats

def is_waiting(user_id: int) -> bool:
    return user_id in waiting_queue

def remove_from_queue(user_id: int) -> None:
    if user_id in waiting_queue:
        waiting_queue.remove(user_id)

def clear_pending_for(user_id: int) -> None:
    pending_requests.pop(user_id, None)
    for target_id in [t for t, req in pending_requests.items() if req == user_id]:
        pending_requests.pop(target_id, None)

async def safe_send(context: ContextTypes.DEFAULT_TYPE, method_name: str, *args, **kwargs):
    """Call any context.bot.send_* method, swallowing/logging Telegram errors so a
    single failed delivery (blocked bot, deactivated account, etc.) never crashes a
    handler or aborts the rest of a flow."""
    try:
        method = getattr(context.bot, method_name)
        return await method(*args, **kwargs)
    except TelegramError as e:
        logger.error(f"Telegram API call {method_name} failed: {e}")
        return None
    except Exception as e:
        logger.exception(f"Unexpected error during {method_name}: {e}")
        return None

def rating_keyboard(target_user_id: int) -> InlineKeyboardMarkup:
    """Inline thumbs up/down for rating `target_user_id` — the person just chatted with."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("👍", callback_data=f"rate:{target_user_id}:up"),
        InlineKeyboardButton("👎", callback_data=f"rate:{target_user_id}:down"),
    ]])

async def end_chat_for(user_id: int, context: ContextTypes.DEFAULT_TYPE, notify: bool = True) -> int | None:
    """Ends user_id's active chat. Returns the (former) partner's user_id, or None."""
    partner_id = active_chats.pop(user_id, None)
    if partner_id is not None:
        active_chats.pop(partner_id, None)
        conn_logger.info(f"Chat ended between {user_id} and {partner_id}")
        if notify:
            await safe_send(context, "send_message", partner_id,
                             "❌ Your partner has left the chat. Tap 🚀 Start Chat to meet someone new.",
                             reply_markup=keyboard_for_user(partner_id))
            await safe_send(context, "send_message", partner_id,
                             "How was your chat? Rate your partner:",
                             reply_markup=rating_keyboard(user_id))
    return partner_id

def format_profile_caption(profile: dict, header: str) -> str:
    if (profile.get("pref_mode") or "anyone") == "specific":
        pref_line = f"🎯 Looking for: {profile.get('pref_genders') or 'Any gender'} / {profile.get('pref_orientations') or 'Any orientation'}"
    else:
        pref_line = "🎯 Looking for: Anyone"
    return (
        f"{header}\n\n"
        f"👤 @{profile['username']} — {profile['name']}, {profile['age']}\n"
        f"⚧ {profile['gender']} — {profile['orientation']}\n"
        f"📍 {profile['location']}\n"
        f"📝 {profile['bio']}\n"
        f"{rating_line_for(profile['user_id'])}\n"
        f"{pref_line}"
    )

async def send_profile_card(to_user_id: int, profile: dict, context: ContextTypes.DEFAULT_TYPE, header: str) -> None:
    await safe_send(context, "send_photo", to_user_id, profile["photo_file_id"],
                     caption=format_profile_caption(profile, header))

def mutual_match(pref_profile: dict, other_profile: dict) -> bool:
    mode = pref_profile.get("pref_mode") or "anyone"
    if mode != "specific":
        return True
    genders = set(filter(None, (pref_profile.get("pref_genders") or "").split(",")))
    orientations = set(filter(None, (pref_profile.get("pref_orientations") or "").split(",")))
    gender_ok = (not genders) or (other_profile["gender"] in genders)
    orientation_ok = (not orientations) or (other_profile["orientation"] in orientations)
    return gender_ok and orientation_ok

async def try_pair(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_profile = get_profile(user_id)
    for candidate_id in list(waiting_queue):
        if candidate_id == user_id or candidate_id in active_chats:
            continue
        candidate_profile = get_profile(candidate_id)
        if not candidate_profile:
            remove_from_queue(candidate_id)
            continue
        if mutual_match(user_profile, candidate_profile) and mutual_match(candidate_profile, user_profile):
            waiting_queue.remove(candidate_id)
            active_chats[user_id] = candidate_id
            active_chats[candidate_id] = user_id

            await send_profile_card(user_id, candidate_profile, context, "✅ Partner found!")
            await safe_send(context, "send_message", user_id,
                             "⏭ Next Chat to skip, 🛑 Stop Chat to end it.", reply_markup=IN_CHAT_KEYBOARD)
            await send_profile_card(candidate_id, user_profile, context, "✅ Partner found!")
            await safe_send(context, "send_message", candidate_id,
                             "⏭ Next Chat to skip, 🛑 Stop Chat to end it.", reply_markup=IN_CHAT_KEYBOARD)

            conn_logger.info(f"Paired {user_id} with {candidate_id} (random match)")
            return True
    return False

async def reject_if_banned(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if is_banned(update.effective_user.id):
        await update.message.reply_text("🚫 You have been banned from using this bot. Please contact the admin @bytecrackbot.")
        return True
    return False

def require_profile_or_prompt(user_id: int) -> bool:
    return profile_exists(user_id)

# Basic commands

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if is_banned(user_id):
        await update.message.reply_text("🚫 You have been banned from using this bot. Please contact the admin @bytecrackbot.")
        return
    if profile_exists(user_id):
        await update.message.reply_text(
            "👋 Welcome back! Use the buttons below to get started.",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
        return

    # Deep-link referral: /start <referrer's connect ID>. Only remembered until this
    # user finishes setup — it never overwrites an existing referral relationship.
    referral_note = ""
    if context.args:
        referrer = get_profile_by_connect_id(context.args[0].strip())
        if referrer and referrer["user_id"] != user_id:
            context.user_data["referral_code"] = referrer["connect_id"]
            referral_note = (
                f"\n\n🎁 You were invited by an existing user — finish setting up your profile "
                f"and you'll both earn bonus credits!"
            )

    await update.message.reply_text(
        "👋 *Welcome to Glow World!*\n\n"
        "Meet new people, chat freely, and stay in control of your privacy.\n\n"
        "Tap *📝 Setup Profile* below to get started — it only takes a minute."
        + referral_note,
        parse_mode="Markdown",
        reply_markup=PRE_SETUP_KEYBOARD,
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    await update.message.reply_text(
        "ℹ️ *Help & Commands*\n\n"
        "📝 /setup — Create your profile\n"
        "✏️ /edit — Edit name, photo, bio, gender, or orientation\n"
        "👤 /profile — View your saved profile\n"
        "🗑 /delete — Delete your profile\n"
        "⚙️ /preferences — Choose who you want to match with\n"
        "🚀 /find — Get matched with a random stranger\n"
        "🔑 /connect <id> — Connect directly using someone's 6-digit ID\n"
        "🎟 /redeem <code> — Redeem a code for credits\n"
        "🛒 /marketplace — Spend credits to unlock group access\n"
        "🔔 /notices — View the latest notices & updates\n"
        "🚩 /report — Report a user for violating the terms\n"
        "📜 /terms — View the Terms & Conditions\n"
        "🛑 /stop — End your current chat\n"
        "⏭ /next — Leave current chat and find someone new\n\n"
        "⭐ After every chat you can rate your partner 👍/👎 — it shows up on their profile.\n"
        "🔒 Connect IDs are private — only the admin @bytecrackbot can look one up for you.",
        parse_mode="Markdown",
        reply_markup=keyboard_for_user(user_id),
    )

async def show_more_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("☰ *More options:*", parse_mode="Markdown", reply_markup=MORE_MENU_KEYBOARD)

async def back_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    await update.message.reply_text("🏠 Main menu:", reply_markup=keyboard_for_user(user_id))

async def show_terms(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    await update.message.reply_text(
        "📜 *Terms & Conditions*\n\n" + get_terms_text(),
        parse_mode="Markdown",
        reply_markup=MORE_MENU_KEYBOARD if profile_exists(user_id) else keyboard_for_user(user_id),
    )

async def view_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    profile = get_profile(user_id)
    if not profile:
        await update.message.reply_text(
            "You haven't set up a profile yet. Tap 📝 Setup Profile to create one.",
            reply_markup=PRE_SETUP_KEYBOARD,
        )
        return
    await update.message.reply_photo(
        profile["photo_file_id"],
        caption=format_profile_caption(profile, "🗂️ Your profile"),
        reply_markup=MORE_MENU_KEYBOARD,
    )

# /setup conversation

async def setup_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if await reject_if_banned(update, context):
        return ConversationHandler.END
    if profile_exists(user_id):
        await update.message.reply_text(
            "⚠️ You already have a profile. Use ✏️ Edit Profile to update it, or 👤 My Profile to view it.",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
        return ConversationHandler.END

    pending_referral = context.user_data.get("referral_code")
    context.user_data.clear()
    if pending_referral:
        context.user_data["referral_code"] = pending_referral
    await update.message.reply_text(
        "🌟 *Let's set up your profile!* (8 quick steps)\n\n"
        "*Step 1* — Choose a unique username (3-20 characters):",
        parse_mode="Markdown",
        reply_markup=CANCEL_ONLY_KEYBOARD,
    )
    return USERNAME

async def setup_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    username = update.message.text.strip() if update.message.text else ""
    if username == BTN_CANCEL:
        return await _cancel_to_presetup(update, context)
    if not re.fullmatch(r"[A-Za-z0-9_]{3,20}", username):
        await update.message.reply_text("Username must be 3-20 characters: letters, numbers, underscores only. Try again:")
        return USERNAME
    if username_exists(username):
        await update.message.reply_text("That username is already taken. Please choose another:")
        return USERNAME
    context.user_data["username"] = username
    await update.message.reply_text("*Step 2* — Enter your name :", parse_mode="Markdown")
    return NAME

async def setup_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip() if update.message.text else ""
    if name == BTN_CANCEL:
        return await _cancel_to_presetup(update, context)
    if not name:
        await update.message.reply_text("Please enter a valid name.")
        return NAME
    context.user_data["name"] = name[:50]
    await update.message.reply_text("*Step 3* — Enter your age :", parse_mode="Markdown")
    return AGE

async def setup_age(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip() if update.message.text else ""
    if text == BTN_CANCEL:
        return await _cancel_to_presetup(update, context)
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("Please enter a valid age number.")
        return AGE
    context.user_data["age"] = int(text)
    await update.message.reply_text("*Step 4* —Send a profile picture..!", parse_mode="Markdown")
    return PHOTO

async def setup_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.text and update.message.text.strip() == BTN_CANCEL:
        return await _cancel_to_presetup(update, context)
    if not update.message.photo:
        await update.message.reply_text("Please send an actual photo (not a file/sticker).")
        return PHOTO
    context.user_data["photo_file_id"] = update.message.photo[-1].file_id
    await update.message.reply_text("*Step 5* — Write a short bio & Introduce yourself.", parse_mode="Markdown")
    return BIO

async def setup_bio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    bio = update.message.text.strip() if update.message.text else ""
    if bio == BTN_CANCEL:
        return await _cancel_to_presetup(update, context)
    if not bio:
        await update.message.reply_text("Please enter a short bio as text.")
        return BIO
    context.user_data["bio"] = bio[:300]
    keyboard = ReplyKeyboardMarkup(with_cancel(rows_of(GENDER_OPTIONS)), resize_keyboard=True)
    await update.message.reply_text("*Step 6* — Select your gender:", parse_mode="Markdown", reply_markup=keyboard)
    return GENDER


async def setup_gender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    gender = update.message.text.strip()
    if gender == BTN_CANCEL:
        return await _cancel_to_presetup(update, context)
    if gender not in GENDER_OPTIONS:
        await update.message.reply_text("Please choose one of the provided options.")
        return GENDER
    context.user_data["gender"] = gender
    options = orientation_options_for_gender(gender)
    keyboard = ReplyKeyboardMarkup(with_cancel(rows_of(options)), resize_keyboard=True)
    await update.message.reply_text("*Step 7* — Select your sexual orientation:", parse_mode="Markdown", reply_markup=keyboard)
    return ORIENTATION

async def setup_orientation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    orientation = update.message.text.strip()
    if orientation == BTN_CANCEL:
        return await _cancel_to_presetup(update, context)
    valid_options = orientation_options_for_gender(context.user_data.get("gender", ""))
    if orientation not in valid_options:
        await update.message.reply_text("Please choose one of the provided options.")
        return ORIENTATION
    context.user_data["orientation"] = orientation
    await update.message.reply_text(
        "*Step 8* — Enter your location in format(city,country):",
        parse_mode="Markdown",
        reply_markup=CANCEL_ONLY_KEYBOARD,
    )
    return LOCATION


async def setup_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    location_str = update.message.text.strip()[:100] if update.message.text else ""
    if location_str == BTN_CANCEL:
        return await _cancel_to_presetup(update, context)
    if not location_str:
        await update.message.reply_text("Please enter your location.")
        return LOCATION

    user_id = update.effective_user.id
    data = context.user_data
    referrer_profile = get_profile_by_connect_id(data["referral_code"]) if data.get("referral_code") else None
    create_profile(
        user_id=user_id, username=data["username"], name=data["name"], age=data["age"],
        photo_file_id=data["photo_file_id"], bio=data["bio"], gender=data["gender"],
        orientation=data["orientation"], location=location_str,
        referred_by=referrer_profile["user_id"] if referrer_profile else None,
    )
    context.user_data.clear()

    bonus_note = ""
    if referrer_profile:
        add_credits(referrer_profile["user_id"], REFERRAL_BONUS_REFERRER)
        add_credits(user_id, REFERRAL_BONUS_NEW_USER)
        conn_logger.info(f"Referral: {user_id} referred by {referrer_profile['user_id']}")
        bonus_note = f"\n\n🎁 Referral bonus: +{REFERRAL_BONUS_NEW_USER} credits added to your balance!"
        await safe_send(context, "send_message", referrer_profile["user_id"],
                         f"🎉 Someone you referred just finished setting up — you earned +{REFERRAL_BONUS_REFERRER} credits!")

    await update.message.reply_text(
        "🎉 *Profile created!* A private Connect ID was generated for you — it's never shown "
        "ask the admin @bytecrackbot if you ever need it revealed.\n\n"
        "By default you'll match with *Anyone*. Open ⚙️ Preferences anytime to narrow that down."
        + bonus_note,
        parse_mode="Markdown",
        reply_markup=MAIN_MENU_KEYBOARD,
    )
    return ConversationHandler.END

async def _cancel_to_presetup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Setup cancelled.", reply_markup=keyboard_for_user(update.effective_user.id))
    return ConversationHandler.END

async def generic_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Cancelled.", reply_markup=keyboard_for_user(update.effective_user.id))
    return ConversationHandler.END

# /edit conversation

def edit_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(with_cancel(rows_of(EDITABLE_FIELDS)), resize_keyboard=True)

async def edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if await reject_if_banned(update, context):
        return ConversationHandler.END
    profile = get_profile(user_id)
    if not profile:
        await update.message.reply_text(
            "You don't have a profile yet. Tap 📝 Setup Profile to create one.",
            reply_markup=PRE_SETUP_KEYBOARD,
        )
        return ConversationHandler.END

    await update.message.reply_photo(profile["photo_file_id"], caption=format_profile_caption(profile, "🗂️ Your current profile"))
    await update.message.reply_text(
        "✏️ What would you like to edit? (Name, Photo, Bio, Gender, and Orientation can be changed.)",
        reply_markup=edit_menu_keyboard(),
    )
    return EDIT_MENU

async def edit_menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = update.message.text.strip()
    if choice in (BTN_CANCEL, BTN_BACK):
        await update.message.reply_text("✅ Done editing.", reply_markup=MORE_MENU_KEYBOARD)
        return ConversationHandler.END
    if choice == "Name":
        await update.message.reply_text("Enter your name:", reply_markup=CANCEL_ONLY_KEYBOARD)
        return EDIT_NAME
    if choice == "Photo":
        await update.message.reply_text("Send your profile photo:", reply_markup=CANCEL_ONLY_KEYBOARD)
        return EDIT_PHOTO
    if choice == "Bio":
        await update.message.reply_text("Setup your new bio:", reply_markup=CANCEL_ONLY_KEYBOARD)
        return EDIT_BIO
    if choice == "Gender":
        keyboard = ReplyKeyboardMarkup(with_cancel(rows_of(GENDER_OPTIONS)), resize_keyboard=True)
        await update.message.reply_text("Select your gender:", reply_markup=keyboard)
        return EDIT_GENDER
    if choice == "Orientation":
        profile = get_profile(update.effective_user.id)
        options = orientation_options_for_gender(profile["gender"])
        keyboard = ReplyKeyboardMarkup(with_cancel(rows_of(options)), resize_keyboard=True)
        await update.message.reply_text("Select your orientation:", reply_markup=keyboard)
        return EDIT_ORIENTATION

    await update.message.reply_text("Please choose one of the menu options.", reply_markup=edit_menu_keyboard())
    return EDIT_MENU

async def edit_apply_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip() if update.message.text else ""
    if text == BTN_CANCEL:
        await update.message.reply_text("Editing menu:", reply_markup=edit_menu_keyboard())
        return EDIT_MENU
    if not text:
        await update.message.reply_text("Please enter a valid name.")
        return EDIT_NAME
    update_profile_field(update.effective_user.id, "name", text[:50])
    await update.message.reply_text("✅ Name updated!", reply_markup=edit_menu_keyboard())
    return EDIT_MENU


async def edit_apply_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.text and update.message.text.strip() == BTN_CANCEL:
        await update.message.reply_text("Editing menu:", reply_markup=edit_menu_keyboard())
        return EDIT_MENU
    if not update.message.photo:
        await update.message.reply_text("Please send an actual photo, or tap ❌ Cancel.")
        return EDIT_PHOTO
    update_profile_field(update.effective_user.id, "photo_file_id", update.message.photo[-1].file_id)
    await update.message.reply_text("✅ Photo updated!", reply_markup=edit_menu_keyboard())
    return EDIT_MENU


async def edit_apply_bio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    bio = update.message.text.strip() if update.message.text else ""
    if bio == BTN_CANCEL:
        await update.message.reply_text("Editing menu:", reply_markup=edit_menu_keyboard())
        return EDIT_MENU
    if not bio:
        await update.message.reply_text("Please enter a valid bio.")
        return EDIT_BIO
    update_profile_field(update.effective_user.id, "bio", bio[:300])
    await update.message.reply_text("✅ Bio updated!", reply_markup=edit_menu_keyboard())
    return EDIT_MENU


async def edit_apply_gender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    gender = update.message.text.strip()
    if gender == BTN_CANCEL:
        await update.message.reply_text("Editing menu:", reply_markup=edit_menu_keyboard())
        return EDIT_MENU
    if gender not in GENDER_OPTIONS:
        await update.message.reply_text("Please choose one of the provided options.")
        return EDIT_GENDER
    update_profile_field(update.effective_user.id, "gender", gender)
    options = orientation_options_for_gender(gender)
    keyboard = ReplyKeyboardMarkup(with_cancel(rows_of(options)), resize_keyboard=True)
    await update.message.reply_text("✅ Gender updated! Please also confirm your orientation:", reply_markup=keyboard)
    return EDIT_ORIENTATION


async def edit_apply_orientation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    orientation = update.message.text.strip()
    profile = get_profile(user_id)
    if orientation == BTN_CANCEL:
        await update.message.reply_text("Editing menu:", reply_markup=edit_menu_keyboard())
        return EDIT_MENU
    valid_options = orientation_options_for_gender(profile["gender"])
    if orientation not in valid_options:
        await update.message.reply_text("Please choose one of the provided options.")
        return EDIT_ORIENTATION
    update_profile_field(user_id, "orientation", orientation)
    await update.message.reply_text("✅ Orientation updated!", reply_markup=edit_menu_keyboard())
    return EDIT_MENU

# /delete conversation

async def delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if not profile_exists(user_id):
        await update.message.reply_text("You don't have a profile to delete.", reply_markup=PRE_SETUP_KEYBOARD)
        return ConversationHandler.END
    keyboard = ReplyKeyboardMarkup([["Yes, delete it", "No, cancel"], [BTN_CANCEL]], resize_keyboard=True)
    await update.message.reply_text(
        "⚠️ Are you sure you want to *permanently delete* your profile? This cannot be undone.",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    return DELETE_CONFIRM


async def delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    answer = update.message.text.strip()
    if answer == BTN_CANCEL:
        await update.message.reply_text("Cancelled. Your profile was not deleted.", reply_markup=MORE_MENU_KEYBOARD)
        return ConversationHandler.END
    if answer.lower() == "yes, delete it":
        if is_in_chat(user_id):
            await end_chat_for(user_id, context)
        remove_from_queue(user_id)
        clear_pending_for(user_id)
        delete_profile(user_id)
        await update.message.reply_text(
            "🗑️ Your profile has been deleted. Tap 📝 Setup Profile anytime to create a new one.",
            reply_markup=PRE_SETUP_KEYBOARD,
        )
    else:
        await update.message.reply_text("Cancelled. Your profile was not deleted.", reply_markup=MORE_MENU_KEYBOARD)
    return ConversationHandler.END

# /preferences conversation

async def preferences_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if not profile_exists(user_id):
        await update.message.reply_text("Please set up your profile first.", reply_markup=PRE_SETUP_KEYBOARD)
        return ConversationHandler.END
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎲 Anyone (free)", callback_data="pm:anyone")],
        [InlineKeyboardButton(f"🎯 Specific gender & orientation ({SET_PREFERENCES_COST} credits)", callback_data="pm:specific")],
        [InlineKeyboardButton("❌ Cancel", callback_data="pm:cancel")],
    ])
    await update.message.reply_text(
        f"Who do you want to be matched with?\n💰 Your balance: {get_credits(user_id)} credits",
        reply_markup=keyboard,
    )
    return PREF_MODE

def build_gender_pref_keyboard(selected: set) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(("✅ " if g in selected else "▫️ ") + g, callback_data=f"pg:{g}")] for g in PREF_GENDER_OPTIONS]
    rows.append([InlineKeyboardButton("➡️ Next", callback_data="pg:next")])
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="pg:cancel")])
    return InlineKeyboardMarkup(rows)

def build_orientation_pref_keyboard(selected: set) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(("✅ " if o in selected else "▫️ ") + o, callback_data=f"po:{o}")] for o in PREF_ORIENTATION_OPTIONS]
    rows.append([InlineKeyboardButton("💾 Save preferences", callback_data="po:save")])
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="po:cancel")])
    return InlineKeyboardMarkup(rows)

async def pref_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if query.data == "pm:cancel":
        await query.edit_message_text("Cancelled — your preferences were not changed.")
        return ConversationHandler.END
    if query.data == "pm:anyone":
        set_preferences(user_id, "anyone", [], [])
        await query.edit_message_text("✅ Preference saved: matching with Anyone.")
        return ConversationHandler.END
    context.user_data["pref_genders"] = set()
    await query.edit_message_text("Select the gender(s) you want to match with, then tap Next:", reply_markup=build_gender_pref_keyboard(set()))
    return PREF_GENDERS

async def pref_gender_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    data = query.data.split(":", 1)[1]
    if data == "cancel":
        await query.answer()
        await query.edit_message_text("Cancelled — your preferences were not changed.")
        return ConversationHandler.END
    selected = context.user_data.setdefault("pref_genders", set())
    if data == "next":
        if not selected:
            await query.answer("Select at least one, or go back and choose Anyone instead.", show_alert=True)
            return PREF_GENDERS
        await query.answer()
        context.user_data["pref_orientations"] = set()
        await query.edit_message_text("Select the orientation(s) you want to match with, then tap Save:", reply_markup=build_orientation_pref_keyboard(set()))
        return PREF_ORIENTATIONS
    await query.answer()
    selected.symmetric_difference_update({data})
    await query.edit_message_reply_markup(reply_markup=build_gender_pref_keyboard(selected))
    return PREF_GENDERS

async def pref_orientation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    data = query.data.split(":", 1)[1]
    if data == "cancel":
        await query.answer()
        await query.edit_message_text("Cancelled — your preferences were not changed.")
        return ConversationHandler.END
    selected = context.user_data.setdefault("pref_orientations", set())
    if data == "save":
        if not selected:
            await query.answer("Select at least one, or go back and choose Anyone instead.", show_alert=True)
            return PREF_ORIENTATIONS
        user_id = query.from_user.id
        if not spend_credits(user_id, SET_PREFERENCES_COST):
            await query.answer(f"You need {SET_PREFERENCES_COST} credits for this — you have {get_credits(user_id)}.", show_alert=True)
            await query.edit_message_text(
                f"❌ Not enough credits ({SET_PREFERENCES_COST} required). Earn more with 🎁 Refer & Earn or 🎟 Redeem Code."
            )
            return ConversationHandler.END
        await query.answer()
        genders = context.user_data.get("pref_genders", set())
        set_preferences(user_id, "specific", list(genders), list(selected))
        await query.edit_message_text(
            f"✅ Preferences saved! (-{SET_PREFERENCES_COST} credits)\n"
            f"Genders: {', '.join(genders)}\nOrientations: {', '.join(selected)}"
        )
        return ConversationHandler.END
    await query.answer()
    selected.symmetric_difference_update({data})
    await query.edit_message_reply_markup(reply_markup=build_orientation_pref_keyboard(selected))
    return PREF_ORIENTATIONS

# Credits, referrals, and redeem codes

async def credits_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not profile_exists(user_id):
        await update.message.reply_text("Please set up your profile first.", reply_markup=PRE_SETUP_KEYBOARD)
        return
    await update.message.reply_text(
        f"💰 *Your credit balance:* {get_credits(user_id)}\n\n"
        f"Spend credits to:\n"
        f"🔑 Connect by ID — {CONNECT_REQUEST_COST} credits\n"
        f"🎯 Set specific match preferences — {SET_PREFERENCES_COST} credits\n\n"
        f"Earn more with 🎁 Refer & Earn or 🎟 Redeem Code.",
        parse_mode="Markdown",
        reply_markup=MORE_MENU_KEYBOARD if profile_exists(user_id) else keyboard_for_user(user_id),
    )

async def referral_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    profile = get_profile(user_id)
    if not profile:
        await update.message.reply_text("Please set up your profile first.", reply_markup=PRE_SETUP_KEYBOARD)
        return
    bot_username = context.bot.username
    link = f"https://t.me/{bot_username}?start={profile['connect_id']}"
    await update.message.reply_text(
        "🎁 *Refer & Earn*\n\n"
        f"Share your link — when a friend joins and finishes setting up their profile:\n"
        f"• You get +{REFERRAL_BONUS_REFERRER} credits\n"
        f"• They get +{REFERRAL_BONUS_NEW_USER} credits\n\n"
        f"🔗 {link}\n\n"
        f"👥 Friends referred so far: {count_referrals(user_id)}\n"
        f"💰 Current balance: {get_credits(user_id)} credits",
        parse_mode="Markdown",
        reply_markup=MORE_MENU_KEYBOARD,
    )

async def redeem_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if await reject_if_banned(update, context):
        return ConversationHandler.END
    if not profile_exists(user_id):
        await update.message.reply_text("Please set up your profile first.", reply_markup=PRE_SETUP_KEYBOARD)
        return ConversationHandler.END
    if context.args:
        await _apply_redeem_code(update, context, context.args[0])
        return ConversationHandler.END
    await update.message.reply_text("🎟 Enter your redeem code:", reply_markup=CANCEL_ONLY_KEYBOARD)
    return REDEEM_INPUT

async def redeem_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    code = update.message.text.strip() if update.message.text else ""
    if code == BTN_CANCEL:
        await update.message.reply_text("Cancelled.", reply_markup=keyboard_for_user(update.effective_user.id))
        return ConversationHandler.END
    await _apply_redeem_code(update, context, code)
    return ConversationHandler.END

async def _apply_redeem_code(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str) -> None:
    user_id = update.effective_user.id
    success, message, amount = redeem_code_for_user(code, user_id)
    if success:
        conn_logger.info(f"Redeem: {user_id} redeemed {code.strip().upper()} for {amount} credits")
        message += f"\n💰 New balance: {get_credits(user_id)} credits"
    await update.message.reply_text(message, reply_markup=MORE_MENU_KEYBOARD)

# Group marketplace (user-facing) — spend credits to unlock a group's invite link

def build_marketplace_keyboard(groups) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(f"{g['title']} — {g['cost']} credits", callback_data=f"mk:buy:{g['id']}")]
            for g in groups]
    return InlineKeyboardMarkup(rows)

async def marketplace_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not profile_exists(user_id):
        await update.message.reply_text("Please set up your profile first.", reply_markup=PRE_SETUP_KEYBOARD)
        return
    groups = list_market_groups(active_only=True)
    if not groups:
        await update.message.reply_text("🛒 The marketplace is empty right now — check back later!",
                                         reply_markup=MORE_MENU_KEYBOARD)
        return
    lines = "\n".join(f"• *{g['title']}* — {g['cost']} credits\n  {g['description']}" for g in groups)
    await update.message.reply_text(
        f"🛒 *Marketplace*\n\nUnlock group access using your credits (balance: {get_credits(user_id)}):\n\n{lines}",
        parse_mode="Markdown",
        reply_markup=build_marketplace_keyboard(groups),
    )

async def marketplace_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = query.from_user.id
    try:
        group_id = int(query.data.split(":", 2)[2])
    except (ValueError, IndexError):
        await query.answer("Something went wrong.", show_alert=True)
        return
    group = get_market_group(group_id)
    if not group or not group["active"]:
        await query.answer("That group is no longer available.", show_alert=True)
        return
    if not spend_credits(user_id, group["cost"]):
        await query.answer(f"Not enough credits — you need {group['cost']}, you have {get_credits(user_id)}.",
                            show_alert=True)
        return
    try:
        invite = await context.bot.create_chat_invite_link(chat_id=group["chat_id"], member_limit=1,
                                                             name=f"user_{user_id}")
    except TelegramError as e:
        add_credits(user_id, group["cost"])  # refund — the link was never delivered
        logger.error(f"Failed to create invite link for group {group_id}: {e}")
        await query.answer("⚠️ Couldn't generate an invite link. Your credits were refunded. "
                            "Contact admin @bytecrackbot for assistance.", show_alert=True)
        return
    record_market_redemption(user_id, group_id, invite.invite_link)
    await query.answer("✅ Unlocked!")
    await query.message.reply_text(
        f"✅ *{group['title']}* unlocked! (-{group['cost']} credits)\n\n"
        f"🔗 One-time invite link (works once, for you only):\n{invite.invite_link}",
        parse_mode="Markdown",
    )

# Notices / updates board (user-facing)

async def notices_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    notices = get_latest_notices()
    if not notices:
        await update.message.reply_text("🔔 No notices have been posted yet.", reply_markup=MORE_MENU_KEYBOARD)
        return
    lines = [f"📅 {n['created_at']}\n{n['text']}" for n in notices]
    await update.message.reply_text(
        "🔔 *Latest notices & updates:*\n\n" + "\n\n".join(lines),
        parse_mode="Markdown",
        reply_markup=MORE_MENU_KEYBOARD,
    )

# Random matching commands

async def find(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if await reject_if_banned(update, context):
        return
    if not require_profile_or_prompt(user_id):
        await update.message.reply_text(
            "⚠️ You need to set up your profile first.", reply_markup=PRE_SETUP_KEYBOARD,
        )
        return
    if is_in_chat(user_id):
        await update.message.reply_text("You're already chatting with someone. Use ⏭ Next Chat to switch or 🛑 Stop Chat to end it.")
        return
    if is_waiting(user_id):
        await update.message.reply_text("⏳ Still looking for a partner... hang tight!")
        return
    await context.bot.send_chat_action(user_id, ChatAction.TYPING)
    paired = await try_pair(user_id, context)
    if not paired:
        waiting_queue.append(user_id)
        await update.message.reply_text("🔍 Looking for a partner... please wait.")

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if is_waiting(user_id):
        remove_from_queue(user_id)
        await update.message.reply_text("🛑 Stopped searching. Tap 🚀 Start Chat to try again.",
                                         reply_markup=keyboard_for_user(user_id))
        return
    if is_in_chat(user_id):
        partner_id = await end_chat_for(user_id, context)
        await update.message.reply_text("🛑 Chat ended. Tap 🚀 Start Chat to meet someone new.",
                                         reply_markup=keyboard_for_user(user_id))
        if partner_id is not None:
            await update.message.reply_text("How was your chat? Rate your partner:",
                                             reply_markup=rating_keyboard(partner_id))
        return
    await update.message.reply_text("You're not currently chatting with anyone. Tap 🚀 Start Chat to begin.")

async def next_partner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if await reject_if_banned(update, context):
        return
    if not require_profile_or_prompt(user_id):
        await update.message.reply_text("⚠️ You need to set up your profile first.", reply_markup=PRE_SETUP_KEYBOARD)
        return
    old_partner_id = None
    if is_in_chat(user_id):
        old_partner_id = await end_chat_for(user_id, context)
    else:
        remove_from_queue(user_id)
    await update.message.reply_text("🔄 Looking for a new partner...", reply_markup=keyboard_for_user(user_id))
    if old_partner_id is not None:
        await update.message.reply_text("How was your chat? Rate your partner:",
                                         reply_markup=rating_keyboard(old_partner_id))
    paired = await try_pair(user_id, context)
    if not paired:
        waiting_queue.append(user_id)

# Direct connect-by-ID

async def perform_connect_request(update: Update, context: ContextTypes.DEFAULT_TYPE, target_connect_id: str) -> None:
    requester_id = update.effective_user.id
    target_profile = get_profile_by_connect_id(target_connect_id)
    if not target_profile:
        await update.message.reply_text("❌ Invalid ID. Double-check it with the bot admin.", reply_markup=MAIN_MENU_KEYBOARD)
        return
    target_id = target_profile["user_id"]
    if target_id == requester_id:
        await update.message.reply_text("You can't connect with yourself 🙂", reply_markup=MAIN_MENU_KEYBOARD)
        return
    if is_in_chat(requester_id) or is_waiting(requester_id):
        await update.message.reply_text("Please 🛑 Stop Chat your current chat or search before requesting a new connection.")
        return
    if is_in_chat(target_id):
        await update.message.reply_text("That user is currently busy chatting with someone else. Try again later.", reply_markup=MAIN_MENU_KEYBOARD)
        return
    if target_id in pending_requests:
        await update.message.reply_text("That user already has a pending request. Try again later.", reply_markup=MAIN_MENU_KEYBOARD)
        return
    if not spend_credits(requester_id, CONNECT_REQUEST_COST):
        await update.message.reply_text(
            f"❌ Not enough credits. Connecting by ID costs {CONNECT_REQUEST_COST} credits — "
            f"you have {get_credits(requester_id)}.\nEarn more with 🎁 Refer & Earn or 🎟 Redeem Code.",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
        return

    requester_profile = get_profile(requester_id)
    pending_requests[target_id] = requester_id
    keyboard = ReplyKeyboardMarkup([[ACCEPT_BTN, DECLINE_BTN]], one_time_keyboard=True, resize_keyboard=True)
    sent = await safe_send(
        context, "send_photo", target_id, requester_profile["photo_file_id"],
        caption=format_profile_caption(requester_profile, "💌 Someone wants to connect with you!"),
        reply_markup=keyboard,
    )
    if sent is None:
        pending_requests.pop(target_id, None)
        add_credits(requester_id, CONNECT_REQUEST_COST)  # refund — the request never went out
        await update.message.reply_text("⚠️ Couldn't reach that user. They may have blocked the bot.", reply_markup=MAIN_MENU_KEYBOARD)
        return
    await update.message.reply_text(
        f"✅ Request sent! (-{CONNECT_REQUEST_COST} credits) Waiting for them to respond.",
        reply_markup=MAIN_MENU_KEYBOARD,
    )

async def connect_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    requester_id = update.effective_user.id
    if await reject_if_banned(update, context):
        return
    if not require_profile_or_prompt(requester_id):
        await update.message.reply_text("⚠️ Set up your profile first.", reply_markup=PRE_SETUP_KEYBOARD)
        return
    if not context.args:
        await update.message.reply_text("Usage: /connect <id>  (or tap 🔑 Connect by ID)")
        return
    await perform_connect_request(update, context, context.args[0].strip())

async def enter_id_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if await reject_if_banned(update, context):
        return ConversationHandler.END
    if not require_profile_or_prompt(user_id):
        await update.message.reply_text("Please set up your profile first.", reply_markup=PRE_SETUP_KEYBOARD)
        return ConversationHandler.END
    await update.message.reply_text(
        "🔑 Please enter a Connect ID (the 6-digit code the admin gave you):",
        reply_markup=CANCEL_ONLY_KEYBOARD,
    )
    return ENTER_ID_WAIT

async def enter_id_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    code = update.message.text.strip() if update.message.text else ""
    if code == BTN_CANCEL:
        await update.message.reply_text("Cancelled.", reply_markup=keyboard_for_user(update.effective_user.id))
        return ConversationHandler.END
    if not re.fullmatch(r"\d{6}", code):
        await update.message.reply_text("That doesn't look like a valid 6-digit ID. Try again, or tap ❌ Cancel.")
        return ENTER_ID_WAIT
    await perform_connect_request(update, context, code)
    return ConversationHandler.END

async def handle_connect_response(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    target_id = update.effective_user.id
    text = update.message.text.strip() if update.message.text else ""
    if target_id not in pending_requests or text not in (ACCEPT_BTN, DECLINE_BTN):
        return False
    requester_id = pending_requests.pop(target_id)

    if text == DECLINE_BTN:
        await update.message.reply_text("Declined.", reply_markup=MAIN_MENU_KEYBOARD)
        await safe_send(context, "send_message", requester_id, "😔 Your connection request was declined.")
        return True

    if is_in_chat(requester_id) or is_in_chat(target_id):
        await update.message.reply_text("⚠️ One of you is already in another chat now. Request cancelled.", reply_markup=MAIN_MENU_KEYBOARD)
        await safe_send(context, "send_message", requester_id, "⚠️ Your connect request couldn't be completed — try again.")
        return True

    remove_from_queue(requester_id)
    remove_from_queue(target_id)
    active_chats[requester_id] = target_id
    active_chats[target_id] = requester_id
    conn_logger.info(f"Connect-by-ID accepted: {requester_id} <-> {target_id}")

    requester_profile = get_profile(requester_id)
    target_profile = get_profile(target_id)
    await send_profile_card(requester_id, target_profile, context, "✅ Connected!")
    await safe_send(context, "send_message", requester_id, "⏭ Next Chat to skip, 🛑 Stop Chat to end it.", reply_markup=IN_CHAT_KEYBOARD)
    await send_profile_card(target_id, requester_profile, context, "✅ Connected!")
    await update.message.reply_text("⏭ Next Chat to skip, 🛑 Stop Chat to end it.", reply_markup=IN_CHAT_KEYBOARD)
    return True

# Rating (thumbs up/down) callback — standalone, not tied to any conversation

async def rate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    rater_id = query.from_user.id
    try:
        _, target_str, verdict = query.data.split(":", 2)
        target_id = int(target_str)
    except (ValueError, IndexError):
        await query.answer("Something went wrong with that rating button.", show_alert=True)
        return
    if target_id == rater_id:
        await query.answer("You can't rate yourself.", show_alert=True)
        return
    record_rating(rater_id, target_id, verdict == "up")
    await query.answer("Thanks for your feedback!")
    try:
        await query.edit_message_text("✅ Rating submitted — thanks for your feedback!")
    except TelegramError:
        pass  # message may already be edited/gone; the rating was still recorded

# /report conversation

async def report_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if await reject_if_banned(update, context):
        return ConversationHandler.END
    if not profile_exists(user_id):
        await update.message.reply_text("Please set up your profile first.", reply_markup=PRE_SETUP_KEYBOARD)
        return ConversationHandler.END

    partner_id = active_chats.get(user_id)
    if partner_id:
        context.user_data["report_target"] = partner_id
        await update.message.reply_text("Please describe why you're reporting this user:", reply_markup=CANCEL_ONLY_KEYBOARD)
        return REPORT_REASON

    await update.message.reply_text(
        "You're not currently chatting with anyone. Enter the 6-digit Connect ID of the user you "
        "want to report (ask the admin if you don't know it):",
        reply_markup=CANCEL_ONLY_KEYBOARD,
    )
    return REPORT_TARGET

async def report_target_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    code = update.message.text.strip() if update.message.text else ""
    if code == BTN_CANCEL:
        await update.message.reply_text("Cancelled.", reply_markup=keyboard_for_user(update.effective_user.id))
        return ConversationHandler.END
    if not re.fullmatch(r"\d{6}", code):
        await update.message.reply_text("Please enter a valid 6-digit ID, or tap ❌ Cancel.")
        return REPORT_TARGET
    profile = get_profile_by_connect_id(code)
    if not profile:
        await update.message.reply_text("No user found with that ID. Try again, or tap ❌ Cancel.")
        return REPORT_TARGET
    context.user_data["report_target"] = profile["user_id"]
    await update.message.reply_text("Please describe why you're reporting this user:", reply_markup=CANCEL_ONLY_KEYBOARD)
    return REPORT_REASON

async def report_reason_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    reason = update.message.text.strip()[:500] if update.message.text else ""
    if reason == BTN_CANCEL:
        context.user_data.pop("report_target", None)
        await update.message.reply_text("Cancelled.", reply_markup=keyboard_for_user(update.effective_user.id))
        return ConversationHandler.END
    if not reason:
        await update.message.reply_text("Please describe the issue in text.")
        return REPORT_REASON
    reporter_id = update.effective_user.id
    reported_id = context.user_data.pop("report_target", None)
    if reported_id is None:
        await update.message.reply_text("Something went wrong, please try again from ☰ More → 🚩 Report User.")
        return ConversationHandler.END

    save_report(reporter_id, reported_id, reason)
    # Notify only admins who are currently authenticated in this runtime — no static ID list.
    for admin_id in authenticated_admins:
        await safe_send(context, "send_message", admin_id,
                         f"🚩 New report against user {reported_id} from {reporter_id}:\n{reason}")
    await update.message.reply_text("✅ Report submitted. Thank you for helping keep the community safe.", reply_markup=keyboard_for_user(reporter_id))
    return ConversationHandler.END

# Admin-only quick lookups (require an authenticated admin session — no ID allow-list)

async def admin_getid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in authenticated_admins:
        await update.message.reply_text("⛔ Admin session required. Open /admin and enter the password first.")
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /getid <telegram_user_id>")
        return
    profile = get_profile(int(context.args[0]))
    if not profile:
        await update.message.reply_text("No profile found for that Telegram user ID.")
        return
    await update.message.reply_text(f"Connect ID for user {profile['user_id']} (@{profile['username']}): {profile['connect_id']}")

async def admin_whois(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in authenticated_admins:
        await update.message.reply_text("⛔ Admin session required. Open /admin and enter the password first.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /whois <connect_id>")
        return
    profile = get_profile_by_connect_id(context.args[0].strip())
    if not profile:
        await update.message.reply_text("No profile found for that Connect ID.")
        return
    await update.message.reply_text(f"Connect ID {profile['connect_id']} belongs to Telegram user {profile['user_id']} (@{profile['username']})")

async def admin_groupid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run inside any group chat (with an authenticated admin present) to get the
    chat ID needed for 🛍 Marketplace Admin → ➕ Add Group. Requires the bot to
    already be a member of that group."""
    if update.effective_user.id not in authenticated_admins:
        await update.message.reply_text("⛔ Admin session required. Open /admin in a private chat with the bot first.")
        return
    await update.message.reply_text(f"This chat's ID is: `{update.effective_chat.id}`", parse_mode="Markdown")

def build_admin_stats_text(extra: str = "") -> str:
    body = (
        "🔐 *Admin Panel*\n\n"
        "📈 *Live statistics*\n"
        f"👥 Total users: {count_profiles()}\n"
        f"⏳ Users waiting: {len(waiting_queue)}\n"
        f"💬 Active chats: {len(active_chats) // 2}\n"
        f"🚩 Open reports: {count_open_reports()}\n"
        f"🚫 Banned users: {count_banned()}\n"
        f"🛍 Marketplace groups: {len(list_market_groups(active_only=False))}\n"
    )
    if extra:
        body += f"\n{extra}\n"
    return body + "\nChoose an action below:"

async def admin_render(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str,
                        reply_markup=None, parse_mode: str = "Markdown") -> None:
    """Edit the tapped inline-button message in place whenever possible, so the
    admin panel stays a single evolving message instead of piling up a new one
    for every tap. Falls back to sending a fresh message when there's nothing
    to edit (e.g. after the admin typed free-text input, or the previous
    message was a photo/document that can't take a text edit)."""
    query = update.callback_query
    if query is not None:
        try:
            await query.edit_message_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
            return
        except TelegramError:
            pass
    chat_id = update.effective_chat.id
    await context.bot.send_message(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup)


async def send_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, extra: str = "") -> None:
    await admin_render(update, context, build_admin_stats_text(extra), reply_markup=build_admin_menu_keyboard())

def _clear_admin_flow_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Drop any half-entered admin flow (target user, pending amounts, etc.)
    whenever the admin backs out early, so a later flow can't accidentally
    pick up stale data."""
    for key in ("admin_credits_target", "admin_redeem_amount", "market_add_chatid",
                "market_add_title", "market_add_desc", "market_cost_group_id"):
        context.user_data.pop(key, None)

async def admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_user.id in authenticated_admins:
        await send_admin_menu(update, context)
        return ADMIN_MENU
    await update.message.reply_text(
        "🔐 Enter the admin panel password:",
        reply_markup=ADMIN_AUTH_CANCEL_KEYBOARD,
    )
    return ADMIN_AUTH

async def admin_auth_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Cancelled.")
    return ConversationHandler.END

async def admin_auth_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip() if update.message.text else ""
    if text == get_admin_password():
        authenticated_admins.add(update.effective_user.id)
        await update.message.reply_text("✅ Access granted.")
        await send_admin_menu(update, context)
        return ADMIN_MENU
    await update.message.reply_text("❌ Incorrect password.")
    return ConversationHandler.END

async def admin_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Generic 🔙 Back handler shared by every admin free-text prompt — always
    returns straight to the admin main menu."""
    query = update.callback_query
    await query.answer()
    _clear_admin_flow_state(context)
    await send_admin_menu(update, context)
    return ADMIN_MENU

async def admin_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = update.effective_chat.id

    if data == "adm:refresh":
        await send_admin_menu(update, context)
        return ADMIN_MENU

    if data == "adm:view":
        await admin_render(update, context, "Enter the username to look up (with or without @):",
                            reply_markup=build_admin_back_keyboard())
        return ADMIN_VIEW_INPUT

    if data == "adm:ban":
        await admin_render(update, context, "Enter the Telegram user ID or 6-digit Connect ID to ban:",
                            reply_markup=build_admin_back_keyboard())
        return ADMIN_BAN_INPUT

    if data == "adm:unban":
        await admin_render(update, context, "Enter the Telegram user ID or 6-digit Connect ID to unban:",
                            reply_markup=build_admin_back_keyboard())
        return ADMIN_UNBAN_INPUT

    if data == "adm:credits":
        await admin_render(update, context,
                            "Enter the Telegram user ID or 6-digit Connect ID whose credits you want to edit:",
                            reply_markup=build_admin_back_keyboard())
        return ADMIN_CREDITS_TARGET

    if data == "adm:redeem_new":
        await admin_render(update, context, "How many credits should this code be worth?",
                            reply_markup=build_admin_back_keyboard())
        return ADMIN_REDEEM_AMOUNT

    if data == "adm:redeem_list":
        codes = get_active_redeem_codes()
        if not codes:
            extra = "No active redeem codes. 🎟"
        else:
            lines = [f"`{c['code']}` — {c['credit_value']} credits — {c['uses_left']}/{c['max_uses']} uses left"
                     for c in codes]
            extra = "🎟 *Active redeem codes:*\n" + "\n".join(lines)
        await send_admin_menu(update, context, extra=extra)
        return ADMIN_MENU

    if data == "adm:reports":
        reports = get_open_reports()
        if not reports:
            extra = "No open reports. 🎉"
        else:
            lines = []
            for r in reports:
                reporter = get_profile(r["reporter_id"])
                reported = get_profile(r["reported_id"])
                lines.append(
                    f"#{r['id']} | {r['created_at']}\n"
                    f"From: @{reporter['username'] if reporter else r['reporter_id']}\n"
                    f"Against: @{reported['username'] if reported else r['reported_id']} (ID: {r['reported_id']})\n"
                    f"Reason: {r['reason']}"
                )
            extra = "\n\n".join(lines)
        await send_admin_menu(update, context, extra=extra)
        return ADMIN_MENU

    if data == "adm:reports_clear":
        clear_open_reports()
        await send_admin_menu(update, context, extra="✅ All open reports marked as reviewed.")
        return ADMIN_MENU

    if data == "adm:broadcast":
        await admin_render(update, context, "Send the message you want to broadcast to every user:",
                            reply_markup=build_admin_back_keyboard())
        return ADMIN_BROADCAST_INPUT

    if data == "adm:notice":
        await admin_render(
            update, context,
            "Send the notice text you want to post. It's saved to the 🔔 Notices board and every "
            "user is notified immediately.",
            reply_markup=build_admin_back_keyboard(),
        )
        return ADMIN_NOTICE_INPUT

    if data == "adm:market":
        await admin_render(
            update, context, "🛍 *Marketplace admin* — manage which groups users can unlock with credits.",
            reply_markup=build_admin_market_keyboard(),
        )
        return ADMIN_MARKET_MENU

    if data == "adm:export":
        path = "users_export.xlsx"
        count = export_profiles_to_excel(path)
        with open(path, "rb") as f:
            await context.bot.send_document(chat_id, f, filename="users_export.xlsx",
                                             caption=f"📊 Exported {count} user(s).")
        await send_admin_menu(update, context)
        return ADMIN_MENU

    if data == "adm:backup":
        try:
            with open(DB_PATH, "rb") as f:
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                await context.bot.send_document(chat_id, f, filename=f"profiles_backup_{stamp}.db",
                                                 caption="💾 Database backup.")
        except FileNotFoundError:
            await context.bot.send_message(chat_id, "⚠️ Database file not found yet.")
        await send_admin_menu(update, context)
        return ADMIN_MENU

    if data == "adm:terms":
        await admin_render(update, context, "Send the new Terms & Conditions text:",
                            reply_markup=build_admin_back_keyboard())
        return ADMIN_EDIT_TERMS

    if data == "adm:pw":
        await admin_render(update, context, "Enter the new admin panel password:",
                            reply_markup=build_admin_back_keyboard())
        return ADMIN_CHANGE_PW

    if data == "adm:close":
        await query.edit_message_text("Admin panel closed.")
        return ConversationHandler.END

    # Unknown/stale callback data — just re-show the menu.
    await send_admin_menu(update, context)
    return ADMIN_MENU

async def admin_ban_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip() if update.message.text else ""
    target_id = resolve_user_id_from_input(text)
    if target_id is None:
        await update.message.reply_text("Couldn't resolve that ID. Try again, or tap Back.",
                                         reply_markup=build_admin_back_keyboard())
        return ADMIN_BAN_INPUT
    ban_user(target_id)
    clear_pending_for(target_id)
    if is_in_chat(target_id):
        await end_chat_for(target_id, context)
    remove_from_queue(target_id)
    await safe_send(context, "send_message", target_id, "🚫 You have been banned from this bot by the admin.")
    await send_admin_menu(update, context, extra=f"✅ User {target_id} has been banned.")
    return ADMIN_MENU

async def admin_unban_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip() if update.message.text else ""
    target_id = resolve_user_id_from_input(text)
    if target_id is None:
        await update.message.reply_text("Couldn't resolve that ID. Try again, or tap Back.",
                                         reply_markup=build_admin_back_keyboard())
        return ADMIN_UNBAN_INPUT
    unban_user(target_id)
    await safe_send(context, "send_message", target_id, "✅ You have been unbanned and can use the bot again.")
    await send_admin_menu(update, context, extra=f"✅ User {target_id} has been unbanned.")
    return ADMIN_MENU

async def admin_credits_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip() if update.message.text else ""
    target_id = resolve_user_id_from_input(text)
    if target_id is None or not profile_exists(target_id):
        await update.message.reply_text("Couldn't find that user. Try again, or tap Back.",
                                         reply_markup=build_admin_back_keyboard())
        return ADMIN_CREDITS_TARGET
    context.user_data["admin_credits_target"] = target_id
    await update.message.reply_text(
        f"Current balance for user {target_id}: {get_credits(target_id)} credits.\n\n"
        f"Enter a new value to *set* it (e.g. `100`), or a signed value to *add/subtract* "
        f"(e.g. `+20` or `-15`):",
        parse_mode="Markdown",
        reply_markup=build_admin_back_keyboard(),
    )
    return ADMIN_CREDITS_AMOUNT

async def admin_credits_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip() if update.message.text else ""
    target_id = context.user_data.get("admin_credits_target")
    if target_id is None:
        await update.message.reply_text("Something went wrong — start over from the admin menu.")
        await send_admin_menu(update, context)
        return ADMIN_MENU
    if not re.fullmatch(r"[+-]?\d+", text):
        await update.message.reply_text("Please enter a whole number like `100`, `+20`, or `-15`.",
                                         parse_mode="Markdown", reply_markup=build_admin_back_keyboard())
        return ADMIN_CREDITS_AMOUNT
    if text.startswith("+") or text.startswith("-"):
        add_credits(target_id, int(text))
    else:
        set_credits(target_id, int(text))
    context.user_data.pop("admin_credits_target", None)
    new_balance = get_credits(target_id)
    await safe_send(context, "send_message", target_id, f"💰 An admin updated your credit balance. New balance: {new_balance} credits.")
    await send_admin_menu(update, context, extra=f"✅ User {target_id}'s balance is now {new_balance} credits.")
    return ADMIN_MENU

async def admin_redeem_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip() if update.message.text else ""
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("Please enter a positive whole number of credits.",
                                         reply_markup=build_admin_back_keyboard())
        return ADMIN_REDEEM_AMOUNT
    context.user_data["admin_redeem_amount"] = int(text)
    await update.message.reply_text("How many times can this code be used in total?",
                                     reply_markup=build_admin_back_keyboard())
    return ADMIN_REDEEM_USES

async def admin_redeem_uses(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip() if update.message.text else ""
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("Please enter a positive whole number of uses.",
                                         reply_markup=build_admin_back_keyboard())
        return ADMIN_REDEEM_USES
    amount = context.user_data.pop("admin_redeem_amount", None)
    if amount is None:
        await update.message.reply_text("Something went wrong — start over from the admin menu.")
        await send_admin_menu(update, context)
        return ADMIN_MENU
    code = create_redeem_code(amount, int(text))
    await send_admin_menu(
        update, context,
        extra=f"✅ New redeem code created:\n`{code}`\nWorth {amount} credits, {text} use(s) total.",
    )
    return ADMIN_MENU

async def admin_view_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip() if update.message.text else ""
    profile = get_profile_by_username(text)
    if not profile:
        await update.message.reply_text("No profile found for that username. Try again, or tap Back.",
                                         reply_markup=build_admin_back_keyboard())
        return ADMIN_VIEW_INPUT

    reports_against = db("SELECT COUNT(*) FROM reports WHERE reported_id = ?", (profile["user_id"],), "val")
    caption = (
        f"🗂️ *Full profile — @{profile['username']}*\n\n"
        f"🆔 Telegram user ID: `{profile['user_id']}`\n"
        f"🔑 Connect ID: `{profile['connect_id']}`\n"
        f"👤 Name: {profile['name']}, {profile['age']}\n"
        f"⚧ Gender: {profile['gender']} — Orientation: {profile['orientation']}\n"
        f"📍 Location: {profile['location']}\n"
        f"📝 Bio: {profile['bio']}\n"
        f"🎯 Preferences: mode={profile['pref_mode']}, "
        f"genders={profile['pref_genders'] or 'any'}, orientations={profile['pref_orientations'] or 'any'}\n"
        f"💰 Credits: {profile['credits']}\n"
        f"🎁 Referred by: {profile['referred_by'] or '—'} | Referrals made: {count_referrals(profile['user_id'])}\n"
        f"{rating_line_for(profile['user_id'])}\n"
        f"🚫 Banned: {'Yes' if is_banned(profile['user_id']) else 'No'}\n"
        f"🚩 Reports against: {reports_against}\n"
        f"📅 Joined: {profile['created_at']} | Updated: {profile['updated_at']}"
    )
    await update.message.reply_photo(profile["photo_file_id"], caption=caption, parse_mode="Markdown",
                                      reply_markup=build_admin_back_keyboard())
    return ADMIN_VIEW_INPUT

async def admin_broadcast_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip() if update.message.text else ""
    if not text:
        await update.message.reply_text("Please send some text to broadcast, or tap Back.",
                                         reply_markup=build_admin_back_keyboard())
        return ADMIN_BROADCAST_INPUT
    sent, failed = 0, 0
    for uid in get_all_user_ids():
        result = await safe_send(context, "send_message", uid, f"📣 {text}")
        if result is None:
            failed += 1
        else:
            sent += 1
    await send_admin_menu(update, context,
                           extra=f"✅ Broadcast complete — delivered to {sent} user(s), failed for {failed}.")
    return ADMIN_MENU

async def admin_notice_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip() if update.message.text else ""
    if not text:
        await update.message.reply_text("Please send some text for the notice, or tap Back.",
                                         reply_markup=build_admin_back_keyboard())
        return ADMIN_NOTICE_INPUT
    notice_text = text[:2000]
    add_notice(notice_text)
    sent, failed = 0, 0
    for uid in get_all_user_ids():
        result = await safe_send(context, "send_message", uid, f"📢 *New notice posted!*\n\n{notice_text}", parse_mode="Markdown")
        if result is None:
            failed += 1
        else:
            sent += 1
    await send_admin_menu(update, context,
                           extra=f"✅ Notice posted to the board and sent to {sent} user(s) (failed for {failed}).")
    return ADMIN_MENU

async def admin_editterms_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip() if update.message.text else ""
    set_terms_text(text[:2000])
    await send_admin_menu(update, context, extra="✅ Terms & Conditions updated.")
    return ADMIN_MENU

async def admin_changepw_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    new_pw = update.message.text.strip() if update.message.text else ""
    if len(new_pw) < 4:
        await update.message.reply_text("Password too short — please choose at least 4 characters.",
                                         reply_markup=build_admin_back_keyboard())
        return ADMIN_CHANGE_PW
    set_admin_password(new_pw)
    await send_admin_menu(update, context, extra="✅ Admin panel password updated.")
    return ADMIN_MENU

# ---- Marketplace admin submenu -------------------------------------------------
async def admin_market_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "adm:mk:back":
        await send_admin_menu(update, context)
        return ADMIN_MENU

    if data == "adm:mk:add":
        await admin_render(
            update, context,
            "Send the group's chat ID (a negative number like `-1001234567890`).\n\n"
            "Tip: add this bot as an *admin* (with \"invite users via link\" permission) in the "
            "target group first, then send /groupid inside that group to get its chat ID.",
            reply_markup=build_admin_back_keyboard(),
        )
        return ADMIN_MARKET_ADD_CHATID

    if data == "adm:mk:remove":
        groups = list_market_groups(active_only=False)
        if not groups:
            await admin_render(update, context, "No marketplace groups yet.",
                                reply_markup=build_admin_market_keyboard())
            return ADMIN_MARKET_MENU
        lines = [f"#{g['id']} — {g['title']} ({g['cost']} credits) {'✅' if g['active'] else '⛔ inactive'}"
                 for g in groups]
        await admin_render(update, context, "Enter the # ID of the group to remove:\n\n" + "\n".join(lines),
                            reply_markup=build_admin_back_keyboard())
        return ADMIN_MARKET_REMOVE_INPUT

    if data == "adm:mk:list":
        groups = list_market_groups(active_only=False)
        if not groups:
            await admin_render(update, context, "No marketplace groups yet.",
                                reply_markup=build_admin_market_keyboard())
        else:
            lines = [
                f"#{g['id']} — *{g['title']}* — {g['cost']} credits — chat_id `{g['chat_id']}` — "
                f"{'✅ active' if g['active'] else '⛔ inactive'}\n{g['description']}"
                for g in groups
            ]
            await admin_render(update, context, "🛍 *Marketplace groups:*\n\n" + "\n\n".join(lines),
                                reply_markup=build_admin_market_keyboard())
        return ADMIN_MARKET_MENU

    if data == "adm:mk:cost":
        groups = list_market_groups(active_only=False)
        if not groups:
            await admin_render(update, context, "No marketplace groups yet.",
                                reply_markup=build_admin_market_keyboard())
            return ADMIN_MARKET_MENU
        lines = [f"#{g['id']} — {g['title']} — currently {g['cost']} credits" for g in groups]
        await admin_render(update, context,
                            "Enter the # ID of the group whose cost you want to change:\n\n" + "\n".join(lines),
                            reply_markup=build_admin_back_keyboard())
        return ADMIN_MARKET_COST_ID

    await admin_render(update, context, "Please use one of the buttons below.",
                        reply_markup=build_admin_market_keyboard())
    return ADMIN_MARKET_MENU

async def admin_market_add_chatid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip() if update.message.text else ""
    if not re.fullmatch(r"-?\d+", text):
        await update.message.reply_text("Please send a valid numeric chat ID, or tap Back.",
                                         reply_markup=build_admin_back_keyboard())
        return ADMIN_MARKET_ADD_CHATID
    context.user_data["market_add_chatid"] = int(text)
    await update.message.reply_text("Now send the group's display title (what users will see):",
                                     reply_markup=build_admin_back_keyboard())
    return ADMIN_MARKET_ADD_TITLE

async def admin_market_add_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip() if update.message.text else ""
    if not text:
        await update.message.reply_text("Please send a non-empty title, or tap Back.",
                                         reply_markup=build_admin_back_keyboard())
        return ADMIN_MARKET_ADD_TITLE
    context.user_data["market_add_title"] = text[:100]
    await update.message.reply_text("Now send a short description for this group:",
                                     reply_markup=build_admin_back_keyboard())
    return ADMIN_MARKET_ADD_DESC

async def admin_market_add_desc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip() if update.message.text else ""
    context.user_data["market_add_desc"] = text[:300]
    await update.message.reply_text("How many credits should unlocking this group cost?",
                                     reply_markup=build_admin_back_keyboard())
    return ADMIN_MARKET_ADD_COST

async def admin_market_add_cost(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip() if update.message.text else ""
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("Please enter a positive whole number of credits, or tap Back.",
                                         reply_markup=build_admin_back_keyboard())
        return ADMIN_MARKET_ADD_COST
    chat_id_val = context.user_data.pop("market_add_chatid", None)
    title = context.user_data.pop("market_add_title", None)
    desc = context.user_data.pop("market_add_desc", "")
    if chat_id_val is None or title is None:
        await update.message.reply_text("Something went wrong — start over from the admin menu.")
        await send_admin_menu(update, context)
        return ADMIN_MENU
    
    try:
        await context.bot.get_chat(chat_id_val)
    except TelegramError as e:
        await update.message.reply_text(
            f"⚠️ Couldn't reach chat `{chat_id_val}` ({e}). Make sure this bot has been added "
            f"to that group as an admin. The group was NOT saved — try again from 🛍 Marketplace Admin.",
            parse_mode="Markdown",
        )
        await send_admin_menu(update, context)
        return ADMIN_MENU
    group_id = add_market_group(chat_id_val, title, desc, int(text))
    await send_admin_menu(update, context, extra=f"✅ Group added to the marketplace as #{group_id} — {title} for {text} credits.")
    return ADMIN_MENU

async def admin_market_remove_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip() if update.message.text else ""
    if not text.isdigit():
        await update.message.reply_text("Please enter the numeric # ID, or tap Back.",
                                         reply_markup=build_admin_back_keyboard())
        return ADMIN_MARKET_REMOVE_INPUT
    group = get_market_group(int(text))
    if not group:
        await update.message.reply_text("No group with that ID. Try again, or tap Back.",
                                         reply_markup=build_admin_back_keyboard())
        return ADMIN_MARKET_REMOVE_INPUT
    delete_market_group(group["id"])
    await send_admin_menu(update, context, extra=f"✅ Removed group #{group['id']} — {group['title']} from the marketplace.")
    return ADMIN_MENU

async def admin_market_cost_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip() if update.message.text else ""
    if not text.isdigit():
        await update.message.reply_text("Please enter the numeric # ID, or tap Back.",
                                         reply_markup=build_admin_back_keyboard())
        return ADMIN_MARKET_COST_ID
    group = get_market_group(int(text))
    if not group:
        await update.message.reply_text("No group with that ID. Try again, or tap Back.",
                                         reply_markup=build_admin_back_keyboard())
        return ADMIN_MARKET_COST_ID
    context.user_data["market_cost_group_id"] = group["id"]
    await update.message.reply_text(
        f"Current cost for {group['title']}: {group['cost']} credits.\nEnter the new cost:",
        reply_markup=build_admin_back_keyboard(),
    )
    return ADMIN_MARKET_COST_VALUE

async def admin_market_cost_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip() if update.message.text else ""
    group_id = context.user_data.get("market_cost_group_id")
    if group_id is None:
        await update.message.reply_text("Something went wrong — start over from the admin menu.")
        await send_admin_menu(update, context)
        return ADMIN_MENU
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("Please enter a positive whole number of credits.",
                                         reply_markup=build_admin_back_keyboard())
        return ADMIN_MARKET_COST_VALUE
    context.user_data.pop("market_cost_group_id", None)
    set_market_group_cost(group_id, int(text))
    await send_admin_menu(update, context, extra=f"✅ Cost updated to {text} credits.")
    return ADMIN_MENU

async def admin_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Admin panel closed.", reply_markup=keyboard_for_user(update.effective_user.id))
    return ConversationHandler.END

# Message relay handler

async def relay_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id

    if is_banned(user_id):
        await update.message.reply_text("🚫 You have been banned from using this bot.")
        return

    if await handle_connect_response(update, context):
        return

    if not profile_exists(user_id):
        await update.message.reply_text(
            "Please set up your profile first, then use 🚀 Start Chat or 🔑 Connect by ID to get matched.",
            reply_markup=PRE_SETUP_KEYBOARD,
        )
        return

    partner_id = active_chats.get(user_id)
    if partner_id is None:
        await update.message.reply_text(
            "You're not in a chat right now. Tap 🚀 Start Chat to get matched with someone.",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
        return

    msg = update.message
    try:
        if msg.text:
            await context.bot.send_message(partner_id, msg.text)
        elif msg.photo:
            await context.bot.send_photo(partner_id, msg.photo[-1].file_id, caption=msg.caption)
        elif msg.video:
            await context.bot.send_video(partner_id, msg.video.file_id, caption=msg.caption)
        elif msg.voice:
            await context.bot.send_voice(partner_id, msg.voice.file_id)
        elif msg.video_note:
            await context.bot.send_video_note(partner_id, msg.video_note.file_id)
        elif msg.sticker:
            await context.bot.send_sticker(partner_id, msg.sticker.file_id)
        elif msg.document:
            await context.bot.send_document(partner_id, msg.document.file_id, caption=msg.caption)
        elif msg.audio:
            await context.bot.send_audio(partner_id, msg.audio.file_id, caption=msg.caption)
        else:
            await update.message.reply_text("⚠️ This message type isn't supported for relay yet.")
            return
    except TelegramError as e:
        logger.error(f"Failed to relay message from {user_id} to {partner_id}: {e}")
        await update.message.reply_text("⚠️ Couldn't deliver your message. Your partner may have blocked the bot.")
    except Exception as e:
        logger.exception(f"Unexpected error relaying message from {user_id} to {partner_id}: {e}")
        await update.message.reply_text("⚠️ Something went wrong delivering your message. Please try again.")

# Global error handler — logs everything, never lets a bad update crash the bot

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled exception while processing an update", exc_info=context.error)
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                "⚠️ Something went wrong on our end. Please try again in a moment."
            )
    except Exception:
        pass

async def post_init(application: Application) -> None:
    # Only the public command set is registered here, so /admin, /getid, and /whois
    # never appear in Telegram's "/" command suggestions — they still work if typed.
    await application.bot.set_my_commands(PUBLIC_COMMANDS)

# Main

def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("Please set your bot token via the TELEGRAM_BOT_TOKEN environment variable.")

    init_db()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_error_handler(on_error)

    cancel_fallback = CommandHandler("cancel", generic_cancel)

    setup_conv = ConversationHandler(
        entry_points=[CommandHandler("setup", setup_start), MessageHandler(filters.Text({BTN_SETUP}), setup_start)],
        states={
            USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_username)],
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_name)],
            AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_age)],
            PHOTO: [MessageHandler(filters.PHOTO | (filters.TEXT & ~filters.COMMAND), setup_photo)],
            BIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_bio)],
            GENDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_gender)],
            ORIENTATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_orientation)],
            LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_location)],
        },
        fallbacks=[cancel_fallback],
    )

    edit_conv = ConversationHandler(
        entry_points=[CommandHandler("edit", edit_start), MessageHandler(filters.Text({BTN_EDIT}), edit_start)],
        states={
            EDIT_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_menu_choice)],
            EDIT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_apply_name)],
            EDIT_PHOTO: [MessageHandler(filters.PHOTO | (filters.TEXT & ~filters.COMMAND), edit_apply_photo)],
            EDIT_BIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_apply_bio)],
            EDIT_GENDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_apply_gender)],
            EDIT_ORIENTATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_apply_orientation)],
        },
        fallbacks=[cancel_fallback],
    )

    delete_conv = ConversationHandler(
        entry_points=[CommandHandler("delete", delete_start), MessageHandler(filters.Text({BTN_DELETE}), delete_start)],
        states={DELETE_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_confirm)]},
        fallbacks=[cancel_fallback],
    )

    preferences_conv = ConversationHandler(
        entry_points=[CommandHandler("preferences", preferences_start), MessageHandler(filters.Text({BTN_PREFERENCES}), preferences_start)],
        states={
            PREF_MODE: [CallbackQueryHandler(pref_mode_callback, pattern=r"^pm:")],
            PREF_GENDERS: [CallbackQueryHandler(pref_gender_callback, pattern=r"^pg:")],
            PREF_ORIENTATIONS: [CallbackQueryHandler(pref_orientation_callback, pattern=r"^po:")],
        },
        fallbacks=[cancel_fallback],
    )

    enter_id_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Text({BTN_ENTER_ID}), enter_id_start)],
        states={ENTER_ID_WAIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_id_receive)]},
        fallbacks=[cancel_fallback],
    )

    report_conv = ConversationHandler(
        entry_points=[CommandHandler("report", report_start), MessageHandler(filters.Text({BTN_REPORT}), report_start)],
        states={
            REPORT_TARGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_target_receive)],
            REPORT_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_reason_receive)],
        },
        fallbacks=[cancel_fallback],
    )

    redeem_conv = ConversationHandler(
        entry_points=[CommandHandler("redeem", redeem_start), MessageHandler(filters.Text({BTN_REDEEM}), redeem_start)],
        states={REDEEM_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, redeem_receive)]},
        fallbacks=[cancel_fallback],
    )

    admin_back = CallbackQueryHandler(admin_back_callback, pattern=r"^adm:back$")
    admin_conv = ConversationHandler(
        entry_points=[CommandHandler("admin", admin_start)],
        states={
            ADMIN_AUTH: [
                CallbackQueryHandler(admin_auth_cancel, pattern=r"^adm:cancel_auth$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_auth_check),
            ],
            ADMIN_MENU: [CallbackQueryHandler(admin_menu_callback, pattern=r"^adm:")],
            ADMIN_VIEW_INPUT: [admin_back, MessageHandler(filters.TEXT & ~filters.COMMAND, admin_view_input)],
            ADMIN_BAN_INPUT: [admin_back, MessageHandler(filters.TEXT & ~filters.COMMAND, admin_ban_input)],
            ADMIN_UNBAN_INPUT: [admin_back, MessageHandler(filters.TEXT & ~filters.COMMAND, admin_unban_input)],
            ADMIN_BROADCAST_INPUT: [admin_back, MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_input)],
            ADMIN_NOTICE_INPUT: [admin_back, MessageHandler(filters.TEXT & ~filters.COMMAND, admin_notice_input)],
            ADMIN_EDIT_TERMS: [admin_back, MessageHandler(filters.TEXT & ~filters.COMMAND, admin_editterms_input)],
            ADMIN_CHANGE_PW: [admin_back, MessageHandler(filters.TEXT & ~filters.COMMAND, admin_changepw_input)],
            ADMIN_CREDITS_TARGET: [admin_back, MessageHandler(filters.TEXT & ~filters.COMMAND, admin_credits_target)],
            ADMIN_CREDITS_AMOUNT: [admin_back, MessageHandler(filters.TEXT & ~filters.COMMAND, admin_credits_amount)],
            ADMIN_REDEEM_AMOUNT: [admin_back, MessageHandler(filters.TEXT & ~filters.COMMAND, admin_redeem_amount)],
            ADMIN_REDEEM_USES: [admin_back, MessageHandler(filters.TEXT & ~filters.COMMAND, admin_redeem_uses)],
            ADMIN_MARKET_MENU: [CallbackQueryHandler(admin_market_menu_callback, pattern=r"^adm:mk:")],
            ADMIN_MARKET_ADD_CHATID: [admin_back, MessageHandler(filters.TEXT & ~filters.COMMAND, admin_market_add_chatid)],
            ADMIN_MARKET_ADD_TITLE: [admin_back, MessageHandler(filters.TEXT & ~filters.COMMAND, admin_market_add_title)],
            ADMIN_MARKET_ADD_DESC: [admin_back, MessageHandler(filters.TEXT & ~filters.COMMAND, admin_market_add_desc)],
            ADMIN_MARKET_ADD_COST: [admin_back, MessageHandler(filters.TEXT & ~filters.COMMAND, admin_market_add_cost)],
            ADMIN_MARKET_REMOVE_INPUT: [admin_back, MessageHandler(filters.TEXT & ~filters.COMMAND, admin_market_remove_input)],
            ADMIN_MARKET_COST_ID: [admin_back, MessageHandler(filters.TEXT & ~filters.COMMAND, admin_market_cost_id)],
            ADMIN_MARKET_COST_VALUE: [admin_back, MessageHandler(filters.TEXT & ~filters.COMMAND, admin_market_cost_value)],
        },
        fallbacks=[CommandHandler("cancel", admin_cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.Text({BTN_HELP}), help_command))
    app.add_handler(MessageHandler(filters.Text({BTN_MORE}), show_more_menu))
    app.add_handler(MessageHandler(filters.Text({BTN_BACK}), back_to_main_menu))
    app.add_handler(CommandHandler("terms", show_terms))
    app.add_handler(MessageHandler(filters.Text({BTN_TERMS}), show_terms))
    app.add_handler(CommandHandler("profile", view_profile))
    app.add_handler(MessageHandler(filters.Text({BTN_MY_PROFILE}), view_profile))

    app.add_handler(setup_conv)
    app.add_handler(edit_conv)
    app.add_handler(delete_conv)
    app.add_handler(preferences_conv)
    app.add_handler(enter_id_conv)
    app.add_handler(report_conv)
    app.add_handler(redeem_conv)
    app.add_handler(admin_conv)

    app.add_handler(CommandHandler("credits", credits_command))
    app.add_handler(MessageHandler(filters.Text({BTN_CREDITS}), credits_command))
    app.add_handler(CommandHandler("referral", referral_command))
    app.add_handler(MessageHandler(filters.Text({BTN_REFERRAL}), referral_command))

    app.add_handler(CommandHandler("find", find))
    app.add_handler(MessageHandler(filters.Text({BTN_START}), find))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(MessageHandler(filters.Text({BTN_STOP}), stop))
    app.add_handler(CommandHandler("next", next_partner))
    app.add_handler(MessageHandler(filters.Text({BTN_NEXT}), next_partner))
    app.add_handler(CommandHandler("connect", connect_command))
    app.add_handler(CommandHandler("getid", admin_getid))
    app.add_handler(CommandHandler("whois", admin_whois))
    app.add_handler(CommandHandler("groupid", admin_groupid))

    app.add_handler(CommandHandler("marketplace", marketplace_command))
    app.add_handler(MessageHandler(filters.Text({BTN_MARKETPLACE}), marketplace_command))
    app.add_handler(CommandHandler("notices", notices_command))
    app.add_handler(MessageHandler(filters.Text({BTN_NOTICES}), notices_command))

    # Standalone inline-callback handlers (rating buttons and marketplace "buy"
    # buttons) — these work from anywhere, independent of any conversation.
    app.add_handler(CallbackQueryHandler(rate_callback, pattern=r"^rate:"))
    app.add_handler(CallbackQueryHandler(marketplace_buy_callback, pattern=r"^mk:buy:"))

    app.add_handler(MessageHandler(~filters.COMMAND, relay_message))

    conn_logger.info("Bot is starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
