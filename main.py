"""
================================================================================
🤖 TELEGRAM ACADEMIC BOT - VASUKI (v11.0 - COMPLETE)
================================================================================
Author: Custom AI
Architecture: Monolithic (Supabase Integrated)
System:
  - Python 3.10+
  - Python-Telegram-Bot v21+
  - Google Gemini AI
  - Flask (Keep-Alive)
  - Supabase (PostgreSQL Persistence)

Features:
  1. 🛡️ CLOUD PERSISTENCE (Supabase JSONB Storage)
  2. ⚡ NON-BLOCKING SAVES (Threaded Database Writes)
  3. 🔄 SMART STATE MANAGEMENT
  4. 🛡️ AUTO-RESTORE SYSTEM
  5. 📸 AI AUTO-SCHEDULING (Gemini Vision)
  6. 🎨 HTML RICH MESSAGES
  7. 📊 ATTENDANCE TRACKING & EXPORT
  8. 👥 MULTIPLE ADMIN SUPPORT
  9. 📚 SHOW ALL SUBJECTS (Restored)
================================================================================
"""

import logging
import asyncio
import os
import json
import io
import time
import traceback
import html
import hmac
import base64
import re
import random
from collections import deque
from threading import Thread, Lock, Event
from datetime import datetime, timedelta, time as dtime
import pytz
from flask import Flask
from dotenv import load_dotenv
# import google.generativeai as genai # Disabled to save memory
from PIL import Image
import urllib.request

# ------------------------------------------------------------------------------
# 📦 EXTERNAL IMPORTS
# ------------------------------------------------------------------------------
from telegram import (
    Update, 
    ReplyKeyboardMarkup, 
    KeyboardButton, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup,
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeAllGroupChats,
    ChatMember,
    ChatMemberUpdated
)
from telegram.constants import ParseMode, ChatAction
from telegram.ext import (
    Application, 
    CommandHandler, 
    ContextTypes, 
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ChatMemberHandler,
    Defaults,
    filters,
    JobQueue
)
from telegram.request import HTTPXRequest 
from telegram.error import Conflict, NetworkError, RetryAfter, TimedOut

# SUPABASE CLIENT
from supabase import create_client, Client

# ==============================================================================
# 🔐 1. SYSTEM CONFIGURATION & ENVIRONMENT
# ==============================================================================
load_dotenv()

# Critical Environment Variables
TOKEN = os.environ.get("BOT_TOKEN")

# Robust Multi-Admin Parsing
# Robust Multi-Admin Parsing
raw_admins = os.environ.get("ADMIN_USERNAMES", "")
ADMIN_USERNAMES = [
    u.strip().replace("@", "").lower() 
    for u in raw_admins.split(",") 
    if u.strip()
]
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")

# Owner DM target for security alerts and non-admin activity mirroring.
# Optional: when unset, the bot learns super-admin chat IDs the first time they
# DM it (see remember_owner_id) — a username can't be used as a chat_id.
_raw_owner = os.environ.get("OWNER_CHAT_ID", "").strip()
try:
    OWNER_CHAT_ID = int(_raw_owner) if _raw_owner else None
except ValueError:
    OWNER_CHAT_ID = None
    logging.getLogger(__name__).warning("⚠️ OWNER_CHAT_ID is not a number — ignoring.")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
ENV_GROUP_ID = os.environ.get("GROUP_CHAT_ID")

# SUPABASE CREDENTIALS
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# Timezone Configuration (India Standard Time)
IST = pytz.timezone('Asia/Kolkata')
START_TIME = datetime.now(IST)

# ------------------------------------------------------------------------------
# 📝 LOGGING CONFIGURATION
# ------------------------------------------------------------------------------
class ISTFormatter(logging.Formatter):
    def converter(self, timestamp):
        dt = datetime.fromtimestamp(timestamp, IST)
        return dt.timetuple()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
for handler in logging.getLogger().handlers:
    handler.setFormatter(ISTFormatter(fmt='%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S IST'))

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------------
# 🧠 AI & DB ENGINE INITIALIZATION
# ------------------------------------------------------------------------------
# Gemini is LAZY LOADED to save memory (~100MB)
model = None  # Will be initialized on first AI request

def get_gemini_model():
    """Gemini disabled to save memory"""
    return None
    # global model
    # if model is None and GEMINI_API_KEY:
    #     try:
    #         import google.generativeai as genai
    #         genai.configure(api_key=GEMINI_API_KEY)
    #         model = genai.GenerativeModel('gemini-2.5-flash')
    #         logger.info("✅ Gemini AI loaded (lazy)")
    #     except Exception as e:
    #         logger.error(f"❌ Gemini AI Failed: {e}")
    # return model

# ------------------------------------------------------------------------------
# 🔑 SUPABASE KEY TIER CHECK
# ------------------------------------------------------------------------------
# Which key the bot holds decides who else can reach the database:
#
#   service_role / sb_secret_  → bypasses RLS. Correct for a server-side bot, and
#                                it keeps working after RLS is switched on.
#   anon / sb_publishable_     → a PUBLIC-BY-DESIGN credential. With RLS disabled
#                                on the table, anyone holding it can read and
#                                rewrite bot_storage — including DB["admins"].
#
# Detected at boot so the situation is visible in the logs and in /stats instead
# of being an assumption. Never logs or displays the key itself.
def _supabase_key_role(key):
    """Best-effort role behind a Supabase key. Returns None if unrecognised."""
    if not key:
        return None
    k = str(key).strip()
    if k.startswith("sb_secret_"):
        return "service_role"
    if k.startswith("sb_publishable_"):
        return "anon"
    parts = k.split(".")
    if len(parts) == 3:                      # legacy JWT-style key
        try:
            payload = parts[1] + "=" * (-len(parts[1]) % 4)
            return json.loads(base64.urlsafe_b64decode(payload)).get("role")
        except Exception:
            return None
    return None


SUPABASE_KEY_ROLE = _supabase_key_role(SUPABASE_KEY)
SUPABASE_KEY_IS_PUBLIC = SUPABASE_KEY_ROLE == "anon"

# Supabase Connection
supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info(f"✅ Supabase Connected (key role: {SUPABASE_KEY_ROLE or 'unknown'})")
    except Exception as e:
        logger.error(f"❌ Supabase Connection Failed: {e}")

    if SUPABASE_KEY_IS_PUBLIC:
        logger.critical(
            "🚨 SUPABASE_KEY is an ANON/PUBLISHABLE key. That key is meant to be "
            "public, so it cannot be what protects bot_storage. Switch to the "
            "service_role (sb_secret_) key AND enable RLS on the table — "
            "otherwise anyone who obtains it can rewrite the admin list."
        )
    elif SUPABASE_KEY_ROLE == "service_role":
        logger.info("🔐 service_role key in use — enabling RLS on bot_storage "
                    "will not break the bot.")
    else:
        logger.warning(f"🔎 Could not identify the Supabase key tier "
                       f"({SUPABASE_KEY_ROLE or 'unrecognised format'}). Confirm "
                       f"it is the service_role key and that RLS is enabled.")
else:
    logger.critical("⚠️ SUPABASE_URL or SUPABASE_KEY missing! Persistence will fail on Render.")

# ==============================================================================
# 💾 2. DATABASE & PERSISTENCE LAYER (SUPABASE VERSION)
# ==============================================================================

# Default Database Structure
DEFAULT_DB = {
    "config": {
        "group_id": int(ENV_GROUP_ID) if ENV_GROUP_ID else None,
        "group_name": "Linked via Env Var" if ENV_GROUP_ID else "❌ No Group Linked",
        # Forum topic every scheduled class alert lands in unless the class
        # carries its own override. Set with /classtopic from inside the topic.
        "class_topic_id": None,
        "class_topic_name": None
    },
    "subjects": {
        "CSDA": [], 
        "AICS": []
    }, 
    "active_jobs": [],
    "attendance": {},
    "feedback": [],
    "system_stats": {
        "start_time": time.time(),
        "classes_scheduled": 0,
        "ai_requests": 0
    },
    "schedules": [],
    "admins": [],
    "topics": {},
    # Security trail for privileged actions (see audit()).
    "audit_log": [],
    # Numeric chat IDs of super admins, learned from their DMs, so the bot can
    # push security alerts and non-admin activity reports without an env var.
    "owner_ids": []
}

DB = DEFAULT_DB.copy()


def _ensure_db_shape():
    """
    Guarantee every top-level DB key exists AND has the correct type.

    `dict.get(key, default)` only returns the default when the key is MISSING.
    A key explicitly set to null — which happens with partial/hand-edited
    imports and with JSON round-trips through Supabase — returns None, so
    `DB["subjects"].get(batch)` then raises
    "'NoneType' object has no attribute 'get'". Rather than trusting the stored
    shape anywhere it is read, normalise it once here.
    """
    global DB
    if not isinstance(DB, dict):
        DB = json.loads(json.dumps(DEFAULT_DB))
        return

    for k in ("config", "subjects", "attendance", "system_stats", "topics"):
        if not isinstance(DB.get(k), dict):
            DB[k] = {}
    for k in ("active_jobs", "feedback", "schedules", "admins",
              "audit_log", "owner_ids"):
        if not isinstance(DB.get(k), list):
            DB[k] = []

    # Admin usernames are the authorisation key, so normalise them once here.
    # Storing '@Foo' and 'foo' as different entries used to mean a revoked admin
    # kept access and could not be removed by typing their handle back.
    DB["admins"] = sorted({
        str(a).strip().lstrip("@").lower()
        for a in DB["admins"] if str(a).strip()
    })
    DB["owner_ids"] = sorted({int(i) for i in DB["owner_ids"]
                              if str(i).lstrip("-").isdigit()})

    DB["config"].setdefault("group_id", int(ENV_GROUP_ID) if ENV_GROUP_ID else None)
    DB["config"].setdefault("group_name", "❌ No Group Linked")
    DB["config"].setdefault("class_topic_id", None)
    DB["config"].setdefault("class_topic_name", None)
    for b in ("CSDA", "AICS"):
        if not isinstance(DB["subjects"].get(b), list):
            DB["subjects"][b] = []
    DB["system_stats"].setdefault("start_time", time.time())
    DB["system_stats"].setdefault("classes_scheduled", 0)
    DB["system_stats"].setdefault("ai_requests", 0)


def load_db():
    global DB
    if not supabase:
        logger.warning("⚠️ Using In-Memory DB (No Supabase)")
        _ensure_db_shape()
        return

    try:
        response = supabase.table("bot_storage").select("data").eq("id", 1).execute()
        if response.data and len(response.data) > 0:
            cloud_data = response.data[0]['data']
            if not cloud_data:
                save_db()
            else:
                DB = cloud_data
                _ensure_db_shape()
                logger.info("📂 Database Loaded from Supabase.")
        else:
            logger.info("🆕 No Cloud Data found. Initializing...")
            save_db()
    except Exception as e:
        logger.error(f"❌ Failed to load DB from Cloud: {e}")
        _ensure_db_shape()

# ------------------------------------------------------------------------------
# ☁️ COALESCING CLOUD WRITER
# ------------------------------------------------------------------------------
# save_db() is called from 32 places, including mark_attendance — so 60 students
# tapping "present" used to spawn 60 OS threads, each of which serialised the
# ENTIRE database and, on failure, slept for up to 18 minutes (60+60+60+300+600).
# Measured on a 1.4 MB database that cost +116 MB RSS at 60 concurrent threads
# and +287 MB at 120, which is how a 512 MB Render instance gets OOM-killed.
#
# Replaced with ONE long-lived daemon worker plus a dirty flag:
#   • N calls during a burst collapse into a single write
#   • only one serialisation exists at any moment, so RSS stays flat
#   • retries are bounded (~3.5 min, not 18) and abandoned if newer data arrives,
#     because the next write supersedes the failed one anyway
#   • daemon thread, so a failing save can't hold up process shutdown

SAVE_DEBOUNCE_SECONDS = 2.0          # let a burst of edits settle into one write
SAVE_RETRY_DELAYS = (5, 15, 45, 120)  # bounded backoff

_save_dirty = Event()
_save_worker_lock = Lock()
_save_worker_thread = None
_save_stats = {"requested": 0, "written": 0, "coalesced": 0,
               "failed": 0, "last_error": None, "last_ok": None}


def _db_snapshot():
    """
    Point-in-time copy of DB for uploading.

    Serialising DB directly would let the asyncio thread mutate it mid-encode
    ("dictionary changed size during iteration"). One round-trip is far cheaper
    than the many concurrent encodes this replaces.
    """
    return json.loads(json.dumps(DB))


def _save_db_blocking():
    """Single synchronous upload attempt. Returns True, or raises."""
    if not supabase:
        return False
    supabase.table("bot_storage").upsert({"id": 1, "data": _db_snapshot()}).execute()
    _save_stats["written"] += 1
    _save_stats["last_ok"] = time.time()
    return True


def _save_worker_loop():
    """One thread for the whole process lifetime."""
    while True:
        _save_dirty.wait()
        # Collapse a burst: anything that arrives in this window rides along.
        time.sleep(SAVE_DEBOUNCE_SECONDS)
        _save_dirty.clear()

        wrote = False
        for attempt, delay in enumerate((0,) + SAVE_RETRY_DELAYS):
            if delay:
                # A newer change is queued — that write covers this one too.
                if _save_dirty.is_set():
                    logger.info("☁️ Newer changes queued, skipping retry.")
                    break
                time.sleep(delay)
                if _save_dirty.is_set():
                    break
            try:
                if _save_db_blocking():
                    logger.info("✅ Database saved to Cloud.")
                    wrote = True
                break
            except Exception as e:
                _save_stats["last_error"] = str(e)[:200]
                logger.error(f"❌ Cloud save failed (attempt {attempt + 1}): {e}")

        if not wrote and not _save_dirty.is_set():
            _save_stats["failed"] += 1
            logger.error("❌ CLOUD SAVE FAILED — will retry on next change.")


def save_db():
    """
    Request a cloud save. Cheap, non-blocking, and safe to call in a tight loop —
    concurrent calls coalesce into a single upload.
    """
    _save_stats["requested"] += 1
    if not supabase:
        return
    global _save_worker_thread
    with _save_worker_lock:
        if _save_worker_thread is None or not _save_worker_thread.is_alive():
            _save_worker_thread = Thread(target=_save_worker_loop,
                                         name="db-writer", daemon=True)
            _save_worker_thread.start()
    if _save_dirty.is_set():
        _save_stats["coalesced"] += 1
    _save_dirty.set()


def flush_db_sync(timeout=25):
    """
    Force an immediate blocking save. For shutdown and manual /forcesave, where
    we must not return before the data is durable.
    """
    if not supabase:
        return False
    last = None
    for delay in (0, 2, 5):
        if delay:
            time.sleep(delay)
        try:
            if _save_db_blocking():
                _save_dirty.clear()
                logger.info("✅ Database flushed to Cloud.")
                return True
        except Exception as e:
            last = e
            logger.error(f"❌ Flush attempt failed: {e}")
    _save_stats["failed"] += 1
    _save_stats["last_error"] = str(last)[:200] if last else "unknown"
    return False

async def force_cloud_save(update, context):
    """Manually trigger cloud save with UI feedback"""
    if not await require_private_admin(update, context): return
    
    msg = await update.message.reply_text(
        "☁️ <b>SAVING TO CLOUD...</b>\n"
        "⏳ <i>Please wait...</i>",
        parse_mode=ParseMode.HTML
    )
    
    try:
        # Blocking flush off the event loop, so the reply reflects the real result.
        # The old code awaited the fire-and-forget retry thread and then reported
        # success unconditionally — even when every attempt had failed.
        loop = asyncio.get_event_loop()
        saved = await loop.run_in_executor(None, flush_db_sync)

        if saved:
            await msg.edit_text(
                "✅ <b>CLOUD SAVE SUCCESSFUL!</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "💾 <i>Data has been synced to Supabase.</i>",
                parse_mode=ParseMode.HTML
            )
        else:
            await msg.edit_text(
                f"❌ <b>SAVE FAILED</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"<code>{html.escape(str(_save_stats.get('last_error'))[:180])}</code>\n\n"
                f"<i>Data is safe in memory and will retry on the next change.</i>",
                parse_mode=ParseMode.HTML
            )
    except Exception as e:
        await msg.edit_text(
            f"❌ <b>SAVE FAILED!</b>\n\n"
            f"<i>Error:</i> <code>{html.escape(str(e)[:180])}</code>",
            parse_mode=ParseMode.HTML
        )

def refresh_db():
    """Reload database from Supabase - for live sync without restart"""
    global DB
    if not supabase:
        logger.warning("⚠️ Cannot refresh: No Supabase connection")
        return False
    
    try:
        response = supabase.table("bot_storage").select("data").eq("id", 1).execute()
        if response.data and len(response.data) > 0:
            cloud_data = response.data[0]['data']
            if cloud_data:
                # Preserve only runtime data, update everything else
                old_active_jobs = DB.get("active_jobs", [])
                DB.update(cloud_data)
                # Keep local active_jobs if cloud has none (runtime jobs)
                if not cloud_data.get("active_jobs"):
                    DB["active_jobs"] = old_active_jobs
                _ensure_db_shape()
                logger.info("🔄 Database refreshed from Supabase")
                return True
    except Exception as e:
        logger.error(f"❌ Refresh failed: {e}")
    return False

async def refresh_db_command(update, context):
    """Manual database refresh command"""
    if not await require_private_admin(update, context): return
    
    msg = await update.message.reply_text(
        "🔄 <b>REFRESHING DATABASE...</b>",
        parse_mode=ParseMode.HTML
    )
    
    loop = asyncio.get_event_loop()
    success = await loop.run_in_executor(None, refresh_db)
    
    if success:
        await msg.edit_text(
            "✅ <b>DATABASE REFRESHED!</b>\n\n"
            "<i>Supabase changes are now live.</i>",
            parse_mode=ParseMode.HTML
        )
    else:
        await msg.edit_text(
            "❌ <b>REFRESH FAILED!</b>\n\n"
            "<i>Check logs for details.</i>",
            parse_mode=ParseMode.HTML
        )

load_db()

# ------------------------------------------------------------------------------
# 🔄 JOB PERSISTENCE HELPERS
# ------------------------------------------------------------------------------
def job_tag(text, fallback="CLASS"):
    """
    Squeeze arbitrary text into the [A-Za-z0-9_] alphabet used by job IDs.

    Job names end up inside the attendance button's callback_data, and the
    handler validates that data against a strict character class. A batch like
    "CSDA & AICS" (or anything Gemini invents during an image scan) used to
    produce an ID with '&' or '/' in it, which the validator then rejected —
    the tap came back as "that class no longer exists". Normalising at creation
    keeps IDs and validation in agreement.
    """
    cleaned = re.sub(r"[^A-Za-z0-9]+", "", str(text or ""))
    return cleaned[:24] or fallback


def add_job_to_db(job_name, run_timestamp, chat_id, data):
    job_entry = {
        "name": job_name,
        "timestamp": run_timestamp,
        "chat_id": chat_id,
        "data": data
    }
    DB["active_jobs"] = [j for j in DB["active_jobs"] if j["name"] != job_name]
    DB["active_jobs"].append(job_entry)
    save_db()

def remove_job_from_db(job_name):
    original_count = len(DB["active_jobs"])
    DB["active_jobs"] = [j for j in DB["active_jobs"] if j["name"] != job_name]
    if len(DB["active_jobs"]) < original_count:
        save_db()

def update_all_jobs_chat_id(new_chat_id):
    """Update all pending jobs to use the new chat_id - fixes 'kicked from group' errors"""
    updated = 0
    for job in DB.get("active_jobs", []):
        if job.get("chat_id") != new_chat_id:
            job["chat_id"] = new_chat_id
            updated += 1
    if updated > 0:
        save_db()
        logger.info(f"🔄 Updated {updated} jobs with new chat_id: {new_chat_id}")
    return updated

def cleanup_old_data(context=None):
    """
    Clean up old data to prevent memory bloat.
    Can be run as a scheduled job (context) or standalone.
    """
    cleaned = 0
    now_ts = time.time()
    thirty_days = 30 * 24 * 60 * 60
    
    # 1. Clean Attendance
    # Previously this read the timestamp from parts[3] of the job ID. For the
    # common 3-part ID that index does not exist (so nothing was ever pruned),
    # and for 4-part 'cmsg_' IDs it was the counter — meaning `now - 0 > 30 days`
    # was true and brand-new records were deleted immediately. Use the stored
    # timestamp, and only fall back to parsing the ID.
    keys_to_remove = []
    if isinstance(DB.get("attendance"), dict):
        for job_id, rec in DB["attendance"].items():
            try:
                ts = None
                if isinstance(rec, dict):
                    ts = rec.get("class_ts") or rec.get("sent_ts")
                if ts is None:
                    ts = _legacy_ts_from_job_id(job_id)
                # Unknown age: keep it. Never delete data we can't date.
                if ts is None:
                    continue
                if now_ts - float(ts) > thirty_days:
                    keys_to_remove.append(job_id)
            except Exception:
                continue

    for k in keys_to_remove:
        del DB["attendance"][k]
        cleaned += 1

    # 2. Clean Feedback (Keep last 50)
    if "feedback" in DB and len(DB["feedback"]) > 50:
        old_len = len(DB["feedback"])
        DB["feedback"] = DB["feedback"][-50:]
        removed = old_len - 50
        logger.info(f"🧹 Pruned {removed} old feedback entries")
        cleaned += removed



    # 4. Clean Stale Active Jobs (older than 24h)
    if "active_jobs" in DB:
        valid_jobs = []
        stale_jobs = 0
        for job in DB["active_jobs"]:
            # If job is more than 24 hours in the past, it's dead
            if job["timestamp"] < now_ts - 86400:
                stale_jobs += 1
            else:
                valid_jobs.append(job)
        
        if stale_jobs > 0:
            DB["active_jobs"] = valid_jobs
            logger.info(f"🧹 Removed {stale_jobs} stale active jobs")
            cleaned += stale_jobs

    if cleaned > 0:
        save_db()
        logger.info(f"🧹 Total cleanup: {cleaned} records removed.")

# ==============================================================================
# 🚦 3. CONVERSATION STATES
# ==============================================================================
(
    SELECT_BATCH, NEW_SUBJECT_INPUT, SELECT_SUB_OR_ADD, SELECT_DAYS, 
    INPUT_START_DATE, INPUT_END_DATE, INPUT_TIME, INPUT_END_TIME, INPUT_LINK,
    SELECT_OFFSET, MSG_TYPE_CHOICE, INPUT_MANUAL_MSG, GEMINI_PROMPT_INPUT,
    EDIT_SELECT_JOB, EDIT_CHOOSE_FIELD, EDIT_NEW_VALUE, ADD_ADMIN_INPUT,
    REMOVE_ADMIN_INPUT, CUSTOM_OFFSET_INPUT, NIGHT_SCHEDULE_TIME,
    CUSTOM_MSG_BATCH, CUSTOM_MSG_TIME, CUSTOM_MSG_START, CUSTOM_MSG_END,
    CUSTOM_MSG_TEXT, CUSTOM_MSG_LINK, CUSTOM_MSG_DAYS,
    SELECT_TOPIC, ADD_TOPIC_NAME, ADD_TOPIC_ID, REMOVE_TOPIC_INPUT,
    EDIT_SUB_SELECT_BATCH, EDIT_SUB_SELECT_SUBJECT, EDIT_SUB_ACTION, EDIT_SUB_NEW_NAME,
    RESET_CONFIRM, EDIT_TOPIC_SELECT, EDIT_TOPIC_NEW_NAME, DELETE_TOPIC_CONFIRM,
    EDIT_SELECT_SCOPE, EDIT_BULK_DAYS,
    COMBINED_SELECT_SUB, EDIT_MSG_TYPE,
    EDIT_SELECT_BATCH, EDIT_SELECT_SUBJECT
) = range(45)

# Regex to match any menu button for canceling wizards
MENU_REGEX = "^(📸 AI Auto-Schedule|🧠 Custom AI|🟦 Schedule CSDA|🟧 Schedule AICS|📅 Schedule Classes|📝 Custom Message|➕ Add Subject|📂 More Options|✏️ Edit Class|🗑️ Delete Class|📅 View Schedule|📊 Attendance|📚 All Subjects|📤 Export Data|📥 Import Data|👥 Manage Admins|💬 Manage Topics|🛠️ Admin Tools|🔙 Back to Main|🌙 Night Schedule|☁️ Force Save|🔄 Reset System|🗑️ Remove Topic|➕ Add Topic Manual|📋 List Topics|👤 Add Admin|🗑️ Remove Admin|📋 View Admins)$"

# ==============================================================================
# 🛠️ UTILITY FUNCTIONS
# ==============================================================================

# Telegram-allowed HTML tags (official list)
ALLOWED_HTML_TAGS = [
    'b', 'strong',           # Bold
    'i', 'em',               # Italic
    'u', 'ins',              # Underline
    's', 'strike', 'del',    # Strikethrough
    'span', 'tg-spoiler',    # Spoiler
    'a',                     # Links
    'code', 'pre',           # Code
    'blockquote',            # Blockquote
    'tg-emoji'               # Custom emoji
]

def validate_html(text):
    """
    Validate that HTML only uses Telegram-allowed tags.
    Returns: (is_valid: bool, error_message: str or None)
    """
    import re
    # Find all tags (opening and closing)
    tags = re.findall(r'</?(\w+(?:-\w+)?)[^>]*>', text)
    invalid_tags = [t for t in tags if t.lower() not in ALLOWED_HTML_TAGS]
    
    if invalid_tags:
        unique_invalid = list(set(invalid_tags))
        return False, f"❌ Invalid HTML tags: {', '.join(unique_invalid)}\n\n✅ Allowed: {', '.join(ALLOWED_HTML_TAGS[:8])}..."
    return True, None

def sanitize_html(text):
    """Remove or convert forbidden HTML tags to safe alternatives"""
    if not text:
        return text
    # Replace common forbidden tags
    replacements = [
        ('<br>', '\n'), ('<br/>', '\n'), ('<br />', '\n'),
        ('<p>', ''), ('</p>', '\n'),
        ('<div>', ''), ('</div>', '\n'),
        ('<h1>', '<b>'), ('</h1>', '</b>\n'),
        ('<h2>', '<b>'), ('</h2>', '</b>\n'),
        ('<h3>', '<b>'), ('</h3>', '</b>\n'),
        ('<li>', '• '), ('</li>', '\n'),
        ('<ul>', ''), ('</ul>', ''),
        ('<ol>', ''), ('</ol>', ''),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    return text

def safe_job_data(job):
    """Safely get job data ensuring it returns a dict"""
    if job and hasattr(job, 'data') and isinstance(job.data, dict):
        return job.data
    return {}


# ------------------------------------------------------------------------------
# #️⃣ CLASS TOPIC RESOLUTION
# ------------------------------------------------------------------------------
# Three distinct intentions have to survive a JSON round-trip through Supabase,
# so 'message_thread_id' in job data is deliberately tri-state:
#
#   None        -> "inherit": use whatever /classtopic points at right now.
#                  Every job scheduled before /classtopic existed is also None,
#                  so old jobs pick the default up for free.
#   GENERAL_TOPIC -> "explicitly General": the admin chose no topic on purpose,
#                  and the default must NOT override that.
#   <int>       -> a specific topic, chosen per-class in the wizard.
#
# Nothing outside _resolve_thread_id may pass the raw value to Telegram — the
# sentinel is a string and the API expects an int or nothing.
GENERAL_TOPIC = "GENERAL"


def get_class_topic():
    """(id, name) of the configured default class topic, or (None, None)."""
    cfg = DB.get("config", {}) if isinstance(DB.get("config"), dict) else {}
    tid = cfg.get("class_topic_id")
    if tid in (None, "", 0):
        return None, None
    try:
        tid = int(tid)
    except (TypeError, ValueError):
        return None, None
    name = cfg.get("class_topic_name") or DB.get("topics", {}).get(str(tid)) or f"Topic {tid}"
    return tid, name


def _clear_class_topic_if(tid):
    """
    Drop the class-topic default when that topic is deleted.

    Leaving a dangling id behind would point every class alert at a thread that
    no longer exists — recoverable (send_alert_job falls back to General) but it
    would prefix every notification with "Topic unavailable".
    Returns a note to append to the caller's reply, or "".
    """
    current = DB.get("config", {}).get("class_topic_id")
    if current is not None and str(current) == str(tid):
        DB["config"]["class_topic_id"] = None
        DB["config"]["class_topic_name"] = None
        return "\n\n⚠️ <i>That was the class-alert topic — alerts now go to General.</i>"
    return ""


def _topic_label(stored):
    """Human-readable destination for a stored topic intention."""
    tid = _resolve_thread_id({'message_thread_id': stored})
    if not tid:
        return "General"
    return (DB.get("topics", {}).get(str(tid))
            or get_class_topic()[1]
            or f"Topic {tid}")


def _resolve_thread_id(data):
    """Turn a job's stored topic intention into a value Telegram accepts."""
    tid = data.get('message_thread_id') if isinstance(data, dict) else None

    if isinstance(tid, str) and tid.strip().upper() == GENERAL_TOPIC:
        return None
    if tid is None:
        return get_class_topic()[0]
    try:
        tid = int(tid)
    except (TypeError, ValueError):
        return get_class_topic()[0]
    # 0 is not a valid thread id; older builds used it to mean General.
    return tid or None

def safe_text(text, default=""):
    """
    Make any string safe to send over HTTP/Telegram.

    Fixes the 'surrogates not allowed' UnicodeEncodeError. Data that round-trips
    through JSON (Supabase) or comes from Telegram display names can contain
    UTF-16 surrogate pairs (e.g. '\\ud83d\\udcac') which Python treats as two
    lone code points that CANNOT be encoded to UTF-8.

    Strategy:
      1. If it already encodes cleanly, return as-is (fast path).
      2. A surrogate PAIR is valid UTF-16 — round-tripping through UTF-16 with
         'surrogatepass' RESTORES the real character (\\ud83d\\udcac -> 💬).
      3. Truly unpaired surrogates can't be recovered, so drop them.
    """
    if text is None:
        return default
    try:
        text = str(text)
    except Exception:
        return default
    if text == "":
        return default
    try:
        text.encode('utf-8')
        return text
    except UnicodeEncodeError:
        pass
    try:
        return text.encode('utf-16', 'surrogatepass').decode('utf-16')
    except Exception:
        try:
            return text.encode('utf-8', 'ignore').decode('utf-8') or default
        except Exception:
            return default


def safe_decode(text):
    """Backwards-compatible wrapper. Kept because existing callers rely on the
    'No content' placeholder for empty values."""
    return safe_text(text, default="No content")




async def send_long_message(bot, chat_id, text, parse_mode=None, reply_markup=None, **kwargs):
    """
    Split and send long messages in chunks of 4000 characters.
    Handles Telegram's 4096 character limit safely.
    """
    MAX_LEN = 4000  # Leave buffer for safety
    
    if len(text) <= MAX_LEN:
        return await bot.send_message(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup, **kwargs)
    
    # Split into chunks
    chunks = []
    current = ""
    for line in text.split('\n'):
        if len(current) + len(line) + 1 > MAX_LEN:
            if current:
                chunks.append(current)
            current = line
        else:
            current = current + '\n' + line if current else line
    if current:
        chunks.append(current)
    
    # Send each chunk
    last_msg = None
    for i, chunk in enumerate(chunks):
        # Only add reply_markup to last chunk
        markup = reply_markup if i == len(chunks) - 1 else None
        try:
            last_msg = await bot.send_message(
                chat_id, chunk, 
                parse_mode=parse_mode, 
                reply_markup=markup, 
                **kwargs
            )
            await asyncio.sleep(0.1)  # Rate limiting - 10 msg/sec max
        except Exception as e:
            if "too long" in str(e).lower():
                # Emergency split
                for j in range(0, len(chunk), MAX_LEN):
                    await bot.send_message(chat_id, chunk[j:j+MAX_LEN], **kwargs)
                    await asyncio.sleep(0.1)
            else:
                raise e
    return last_msg

async def send_message_safe(bot, chat_id, text, parse_mode=ParseMode.HTML, **kwargs):
    """
    Bulletproof message sender with multiple fallbacks.
    1. Try with HTML
    2. If fails, strip HTML and send plain
    3. If too long, chunk it
    """
    try:
        # First try normal send
        if len(text) > 4000:
            return await send_long_message(bot, chat_id, text, parse_mode=parse_mode, **kwargs)
        return await bot.send_message(chat_id, text, parse_mode=parse_mode, **kwargs)
    except Exception as e:
        err = str(e).lower()
        if "parse" in err or "entity" in err or "tag" in err:
            # HTML parsing error - strip all HTML and retry
            import re
            clean_text = re.sub(r'<[^>]+>', '', text)
            logger.warning(f"HTML parse error, sending plain text: {e}")
            return await bot.send_message(chat_id, clean_text, **kwargs)
        elif "too long" in err:
            return await send_long_message(bot, chat_id, text, parse_mode=parse_mode, **kwargs)
        else:
            raise e

# ==============================================================================
# 🌐 4. KEEP-ALIVE SERVER (FLASK)
# ==============================================================================
app = Flask('')

@app.route('/')
def home():
    """
    Public liveness page. Deliberately says nothing useful.

    This binds to 0.0.0.0 with no authentication, so anyone who finds the URL can
    read it. It used to publish the linked group ID, the storage backend and the
    pending job count — free reconnaissance. Uptime monitors only need a 200.
    """
    return """
    <html>
    <body style="font-family: monospace; background: #0d1117; color: #c9d1d9; padding: 20px;">
        <h1>🤖 VASUKI: <span style="color: #2ea043;">ONLINE</span></h1>
        <p>Use the bot in Telegram.</p>
    </body>
    </html>
    """

@app.route('/health')
def health():
    """Lightweight health check endpoint for uptime monitors"""
    return 'OK', 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

# ==============================================================================
# 🧠 5. ARTIFICIAL INTELLIGENCE LOGIC
# ==============================================================================
async def analyze_timetable_image(image_bytes):
    ai_model = get_gemini_model()
    if not ai_model: return None
    prompt = """
    Analyze this timetable image. Extract class details into strict JSON:
    [{"day": "Mon", "time": "10:00", "subject": "Maths", "batch": "CSDA"}]
    Constraints: 
    1. Days: Mon, Tue, Wed, Thu, Fri, Sat, Sun.
    2. Time: 24h format HH:MM.
    3. Return ONLY raw JSON string.
    """
    try:
        DB["system_stats"]["ai_requests"] += 1
        img = Image.open(io.BytesIO(image_bytes))
        img.thumbnail((1024, 1024)) 
        response = await asyncio.to_thread(ai_model.generate_content, [prompt, img])
        text = response.text
        text = re.sub(r"```json", "", text, flags=re.IGNORECASE)
        text = re.sub(r"```", "", text)
        return json.loads(text.strip())
    except Exception as e:
        logger.error(f"AI Vision Error: {e}")
        return None

async def generate_hype_message(batch, subject, time_str, link):
    ai_model = get_gemini_model()
    if not ai_model: return None
    try:
        DB["system_stats"]["ai_requests"] += 1
        date_str = datetime.now(IST).strftime('%A, %d %B')
        time_formatted = _format_time_12h(time_str)
        prompt = (
            f"Create a high quality HTML notification for an upcoming college class.\n"
            f"Info:\n"
            f"- Batch: {batch}\n"
            f"- Subject: {subject}\n"
            f"- Timing: {time_formatted}\n"
            f"- Date: {date_str}\n"
            f"- Link: {link}\n\n"
            f"Rules:\n"
            f"1. Use allowed HTML tags only: <b>, <i>, <code>, <a href='...'>, <blockquote>.\n"
            f"2. Do NOT use <br>, <p>, or <div>. Use real newlines (\\n) for spacing.\n"
            f"3. Explicitly state the class timing ({time_formatted}) including both start and end time.\n"
            f"4. If a join link is provided, include <a href='{link}'>JOIN CLASS</a>.\n"
            f"5. Make it clear, stylish, and motivating."
        )
        response = await asyncio.to_thread(ai_model.generate_content, prompt)
        text = response.text
        # Sanitize common forbidden tags
        text = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<p>", "").replace("</p>", "\n")
        return text
    except Exception: return None

async def custom_gemini_task(prompt):
    ai_model = get_gemini_model()
    if not ai_model: return "❌ AI Disabled."
    try:
        DB["system_stats"]["ai_requests"] += 1
        response = await asyncio.to_thread(ai_model.generate_content, prompt)
        return response.text
    except Exception as e: return f"Error: {e}"

# ==============================================================================
# 🎨 6. UI COMPONENTS
# ==============================================================================
def get_main_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📸 AI Auto-Schedule"), KeyboardButton("🧠 Custom AI")],
        [KeyboardButton("🟦 Schedule CSDA"), KeyboardButton("🟧 Schedule AICS")],
        [KeyboardButton("📅 Schedule Classes")],
        [KeyboardButton("📝 Custom Message"), KeyboardButton("➕ Add Subject")],
        [KeyboardButton("📂 More Options ⤵️")]
    ], resize_keyboard=True, is_persistent=True)

def get_more_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("✏️ Edit Class"), KeyboardButton("🗑️ Delete Class")],
        [KeyboardButton("📅 View Schedule"), KeyboardButton("📊 Attendance")],
        [KeyboardButton("📚 All Subjects"), KeyboardButton("📤 Export Data")], 
        [KeyboardButton("📥 Import Data"), KeyboardButton("👥 Manage Admins")],
        [KeyboardButton("🛠️ Admin Tools"), KeyboardButton("🔙 Back to Main")]
    ], resize_keyboard=True, is_persistent=True)

def get_admin_mgmt_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("👤 Add Admin"), KeyboardButton("🗑️ Remove Admin")],
        [KeyboardButton("📋 View Admins"), KeyboardButton("🔙 Back to Main")]
    ], resize_keyboard=True, is_persistent=True)

def days_keyboard(selected_days):
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    buttons = []
    row = []
    for d in days:
        icon = "✅" if d in selected_days else "⬜"
        row.append(InlineKeyboardButton(f"{icon} {d}", callback_data=f"toggle_{d}"))
        if len(row) == 3: 
            buttons.append(row)
            row = []
    if row: buttons.append(row)
    buttons.append([InlineKeyboardButton("🚀 DONE", callback_data="days_done")])
    return InlineKeyboardMarkup(buttons)

# ==============================================================================
# 🛡️ 7. ACCESS CONTROL
# ==============================================================================
def _norm_username(username):
    """
    Canonical form of a Telegram handle: no '@', no whitespace, lowercase.

    Every comparison in this file goes through here. ADMIN_USERNAMES is
    lowercased at parse time, so comparing a raw update.effective_user.username
    against it silently failed for anyone with a capital letter in their handle —
    which quietly disabled the super-admin gate.
    """
    if not username:
        return ""
    return str(username).strip().lstrip("@").lower()


def is_admin(username):
    """Check if username is an admin (from env or database)"""
    username = _norm_username(username)
    if not username:
        return False

    # Environment admins (already normalised at import time)
    if username in ADMIN_USERNAMES:
        return True

    # Database admins (normalised by _ensure_db_shape)
    if username in {_norm_username(a) for a in DB.get("admins", [])}:
        return True

    return False

def is_super_admin(username):
    """Check if username is a primary admin. Env list only — DB admins can
    never reach this tier, so a leaked /login password cannot escalate."""
    username = _norm_username(username)
    if not username:
        return False
    return username in ADMIN_USERNAMES

def is_private_chat(update):
    """Check if the message is from a private chat"""
    return update.effective_chat.type == 'private'

# ==============================================================================
# 🧾 AUDIT TRAIL
# ==============================================================================
# Every privileged or security-relevant action lands here, so a takeover can be
# reconstructed after the fact. Render logs are ephemeral; this survives with the
# database. Capped, because the whole DB is serialised on every save.
AUDIT_LOG_MAX = 300


def audit(action, user=None, details=None, ok=True):
    """Append a privileged action to the persistent audit trail."""
    try:
        if not isinstance(DB.get("audit_log"), list):
            DB["audit_log"] = []
        entry = {
            "ts": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
            "action": str(action)[:60],
            "ok": bool(ok),
            "user_id": getattr(user, "id", None),
            "username": _norm_username(getattr(user, "username", None)) or None,
            "name": safe_text(getattr(user, "first_name", None), "") or None,
            "details": safe_text(details, "")[:300] or None,
        }
        DB["audit_log"].append(entry)
        if len(DB["audit_log"]) > AUDIT_LOG_MAX:
            del DB["audit_log"][:-AUDIT_LOG_MAX]
        logger.info("AUDIT %s ok=%s by @%s (%s): %s", entry["action"], entry["ok"],
                    entry["username"], entry["user_id"], entry["details"])
        save_db()
        return entry
    except Exception as e:
        logger.error(f"audit() failed for {action}: {e}")
        return None

# ==============================================================================
# 📡 OWNER NOTIFICATIONS & NON-ADMIN ACTIVITY MIRROR
# ==============================================================================
# A username cannot be used as a chat_id for a private user, so the bot needs a
# numeric ID to DM the owner. It uses OWNER_CHAT_ID when provided, and otherwise
# learns it the first time a super admin opens a DM.

def remember_owner_id(user, update=None):
    """Record a super admin's numeric chat ID so the bot can DM them."""
    try:
        if not user or not getattr(user, "id", None):
            return
        if not is_super_admin(getattr(user, "username", None)):
            return
        if update is not None and not is_private_chat(update):
            return  # user.id is only a valid DM target once they've opened one
        ids = DB.setdefault("owner_ids", [])
        if user.id not in ids:
            ids.append(user.id)
            save_db()
            logger.info(f"📡 Owner DM target learned: {user.id}")
    except Exception as e:
        logger.error(f"remember_owner_id failed: {e}")


def _owner_targets():
    """Every chat ID that should receive security alerts / activity reports."""
    targets = []
    if OWNER_CHAT_ID:
        targets.append(OWNER_CHAT_ID)
    for i in DB.get("owner_ids", []):
        if i not in targets:
            targets.append(i)
    return targets


async def notify_owner(context, text):
    """
    Best-effort DM to the owner(s). Never raises into the caller.

    Accepts a handler context, an Application, or a raw Bot, so startup checks in
    post_init can use it too.
    """
    bot = getattr(context, "bot", context)
    targets = _owner_targets()
    if not targets:
        logger.warning("📡 No owner DM target set (OWNER_CHAT_ID unset and no "
                       "super admin has DMed the bot yet).")
        return
    for chat_id in targets:
        try:
            await send_message_safe(bot, chat_id, safe_text(text),
                                    parse_mode=ParseMode.HTML,
                                    disable_web_page_preview=True)
        except Exception as e:
            logger.warning(f"Could not notify owner {chat_id}: {e}")


# Flood guard: the owner's DM is subject to Telegram rate limits like any other
# chat, so a spam burst from a group must not get the bot throttled.
MIRROR_MAX_PER_WINDOW = 20
MIRROR_WINDOW = 60
_mirror_times = deque()
_mirror_suppressed = 0


def _mirror_allowed():
    """Sliding-window rate check for owner activity reports."""
    global _mirror_suppressed
    now = time.time()
    while _mirror_times and now - _mirror_times[0] > MIRROR_WINDOW:
        _mirror_times.popleft()
    if len(_mirror_times) >= MIRROR_MAX_PER_WINDOW:
        _mirror_suppressed += 1
        return False
    _mirror_times.append(now)
    return True


def _plain(text, limit=600):
    """Strip HTML and escape, so mirrored content can't break the report."""
    if not text:
        return ""
    clean = re.sub(r"<[^>]+>", "", safe_text(text, "")).strip()
    if len(clean) > limit:
        clean = clean[:limit] + " …"
    return html.escape(clean)


def _describe_incoming(update):
    """Human-readable summary of whatever the user just sent."""
    if update.callback_query:
        return f"[button tap] {update.callback_query.data}"
    msg = update.effective_message
    if not msg:
        return "[no message]"
    if msg.text:
        return msg.text
    for label in ("photo", "document", "voice", "audio", "video", "sticker",
                  "animation", "contact", "location", "poll", "video_note"):
        if getattr(msg, label, None):
            extra = safe_text(msg.caption, "")
            return f"[{label}]" + (f" {extra}" if extra else "")
    return "[non-text message]"


async def mirror_non_admin(context, update, bot_reply=None, event=None):
    """
    Forward a non-admin interaction to the owner's DM: who they are, what they
    sent, and what the bot said back. Admins are never mirrored.
    """
    global _mirror_suppressed
    try:
        user = update.effective_user
        if is_admin(getattr(user, "username", None)):
            return
        if not _owner_targets() or not _mirror_allowed():
            return

        chat = update.effective_chat
        if chat and chat.type in ("group", "supergroup"):
            where = f"👥 {html.escape(safe_text(chat.title, 'Group'))}"
        else:
            where = "📩 Direct message"

        uname = _norm_username(getattr(user, "username", None))
        handle = f"@{html.escape(uname)}" if uname else "<i>no username set</i>"

        lines = [
            "👁 <b>NON-ADMIN ACTIVITY</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"👤 <b>Username:</b> {handle}",
            f"🆔 <b>User ID:</b> <code>{getattr(user, 'id', '?')}</code>",
            f"📛 <b>Name:</b> {html.escape(safe_text(getattr(user, 'first_name', None), '—'))}",
            f"📍 <b>Where:</b> {where}",
            f"🕒 <b>Time:</b> {datetime.now(IST).strftime('%d %b %Y, %H:%M:%S IST')}",
        ]
        if event:
            lines.append(f"⚡ <b>Event:</b> {html.escape(str(event))}")
        lines.append("\n💬 <b>They sent:</b>\n"
                     f"<blockquote>{_plain(_describe_incoming(update))}</blockquote>")
        lines.append("\n🤖 <b>Bot replied:</b>\n"
                     f"<blockquote>{_plain(bot_reply) or '<i>nothing</i>'}</blockquote>")

        if _mirror_suppressed:
            lines.append(f"\n⚠️ <i>{_mirror_suppressed} earlier event(s) were "
                         f"dropped by the flood guard.</i>")
            _mirror_suppressed = 0

        await notify_owner(context, "\n".join(lines))
    except Exception as e:
        logger.error(f"mirror_non_admin failed: {e}")

# ==============================================================================
# 🔒 AUTHORISATION GUARDS
# ==============================================================================

async def require_super_admin(update, context, action=None):
    """
    Private chat + env-listed super admin. Use for anything that can escalate
    privilege, exfiltrate the database, or take the bot down.
    """
    if not await require_private_admin(update, context):
        return False
    user = update.effective_user
    if not is_super_admin(getattr(user, "username", None)):
        audit(action or "super_admin_denied", user, "not a super admin", ok=False)
        await deny_access(update, context, scope="super")
        return False
    return True


async def require_admin_callback(update, context, super_only=False):
    """
    Authorise an inline-button tap.

    Callback data is NOT a trusted channel. Telegram hands every client the raw
    callback_data of any message it can see, and the API lets a client submit an
    arbitrary data value against that message. A group member who can see the
    '✅ Mark me present' button on a class alert can just as easily submit
    'kill_<job_name>' — the job name is right there in the attendance button. So
    every callback that mutates state must re-authorise the sender instead of
    assuming the tap came from a menu we drew for an admin.
    """
    query = update.callback_query
    user = update.effective_user
    username = getattr(user, "username", None)
    data = getattr(query, "data", "?")

    if not is_admin(username):
        audit("callback_denied", user, f"data={data}", ok=False)
        await deny_access(update, context, event=f"forged/unauthorised button: {data}")
        return False

    if super_only and not is_super_admin(username):
        audit("callback_denied_super", user, f"data={data}", ok=False)
        await deny_access(update, context, scope="super")
        return False

    # Admin keyboards are only ever sent to a DM, so a tap arriving from a group
    # means the data was submitted by hand.
    if update.effective_chat and update.effective_chat.type != "private":
        audit("callback_wrong_chat", user, f"data={data}", ok=False)
        try:
            await query.answer("⚠️ Admin actions only work in DM.", show_alert=True)
        except Exception:
            pass
        return False

    return True

# ------------------------------------------------------------------------------
# 🔒 ACCESS-DENIED REPLIES
# One calm, useful line instead of a joke: what is restricted, and what the
# member can actually do about it. Lines rotate per user so a second attempt
# does not read like a stuck error. The user's name is stitched in.
# ------------------------------------------------------------------------------

# Commands every member may use anywhere (group or DM). Everything else is
# admin-only.
PUBLIC_COMMANDS = {"feedback", "login", "start"}

# DM replies. Deliberately short: the same text is reused as a Telegram popup
# alert, which truncates at ~190 characters, so the point has to land in the
# first line. Anything actionable lives in DENY_FOOTERS.
DENY_PRIVATE = [
    "\U0001F512 <b>Admin-only command</b>\n\n{user}, this control is limited to the class admins.",
    "\U0001F512 <b>Restricted control</b>\n\n{user}, only the class admins can run this one.",
    "\U0001F512 <b>Admins only</b>\n\n{user}, scheduling and class settings are managed by the admins.",
    "\U0001F6E1 <b>Permission needed</b>\n\n{user}, this action belongs to the admin panel.",
    "\U0001F512 <b>Not open to members</b>\n\n{user}, this part of the bot is reserved for admins.",
    "\U0001F6E1 <b>Access denied</b>\n\n{user}, your account does not have admin rights here.",
    "\U0001F512 <b>Locked</b>\n\n{user}, class management commands are admin-only.",
    "\U0001F510 <b>Admin rights required</b>\n\n{user}, you will need admin access for this one.",
]

# Group replies — a single line, sent silently and auto-deleted, so the group
# stays clean and nobody gets pinged.
DENY_GROUP = [
    "\U0001F512 {user} \u2014 that command is admin-only.",
    "\U0001F512 {user} \u2014 admins only, nothing to do here.",
    "\U0001F512 {user} \u2014 this control is not open to members.",
    "\U0001F6E1 {user} \u2014 you need admin rights for that one.",
    "\U0001F512 {user} \u2014 admin-only. You can still tap <b>Mark me present</b> on class alerts.",
    "\U0001F512 {user} \u2014 restricted command. Message me privately if you need help.",
]

# A normal admin reaching for an owner-only action.
DENY_SUPER = [
    "\U0001F6E1 <b>Owner-only action</b>\n\n{user}, this one is limited to the bot owner.",
    "\U0001F510 <b>Higher permission needed</b>\n\n{user}, your admin role does not cover this action.",
    "\U0001F6E1 <b>Restricted to the owner</b>\n\n{user}, only the owner can change this.",
    "\U0001F512 <b>Owner approval required</b>\n\n{user}, ask the owner to run this one.",
]

# Appended to DM replies for members: the part they can act on.
DENY_FOOTERS = [
    "\U0001F4AC <b>Need access?</b> Message @AvadaKedavaaraa\n\U0001F511 <b>Already have the password?</b> <code>/login [password]</code>",
    "\u2705 <b>As a member you can</b> tap <b>Mark me present</b> on any class alert, and send notes with <code>/feedback</code>\n\U0001F511 <b>Have the admin password?</b> <code>/login [password]</code>",
    "\U0001F4AC <b>Think this is a mistake?</b> Ping @AvadaKedavaaraa\n\U0001F511 <b>Already approved?</b> <code>/login [password]</code>",
]

# Separate footer for the owner-only case: /login is no help to someone who is
# already an admin, so point them at the owner instead.
DENY_FOOTERS_SUPER = [
    "\U0001F4AC <b>Need this done?</b> Ask @AvadaKedavaaraa to run it.",
    "\U0001F4AC <b>Owner:</b> @AvadaKedavaaraa",
]

# Per-user history so the same line never repeats back-to-back.
_LINE_HISTORY = {}
# Group spam guard: user+chat -> last reply timestamp
_DENY_COOLDOWN = {}
GROUP_DENY_COOLDOWN = 25       # seconds before the same user gets another reply
GROUP_DENY_TTL = 8             # seconds before the group reply self-destructs

def _pick_line(pool, key):
    """Random pick that avoids anything used recently for this key."""
    hist = _LINE_HISTORY.get(key)
    if hist is None:
        hist = deque(maxlen=max(1, len(pool) - 1))
        # Keep the dict from growing forever on a busy group.
        if len(_LINE_HISTORY) > 500:
            _LINE_HISTORY.clear()
        _LINE_HISTORY[key] = hist

    available = [line for line in pool if line not in hist]
    if not available:
        hist.clear()
        available = list(pool)

    choice = random.choice(available)
    hist.append(choice)
    return choice

def user_tag(user):
    """Clickable mention + @username, safe for HTML parse mode."""
    if not user:
        return "<b>Anonymous</b>"
    name = html.escape(user.first_name or user.username or "Anonymous")
    tag = f'<a href="tg://user?id={user.id}">{name}</a>'
    if user.username:
        tag += f" (@{html.escape(user.username)})"
    return f"<b>{tag}</b>"

def build_deny_message(user, scope="private"):
    """
    Personalised, non-repeating "you can't use this" reply for one user.

    DM replies carry a footer with the next step; group replies stay bare
    because they self-delete a few seconds later and the footer would only be
    clutter.
    """
    pools = {
        "private": DENY_PRIVATE,
        "group": DENY_GROUP,
        "super": DENY_SUPER,
    }
    pool = pools.get(scope, DENY_PRIVATE)
    uid = getattr(user, "id", 0)
    text = _pick_line(pool, f"{uid}:{scope}").format(user=user_tag(user))

    if scope == "super":
        # They are already an admin, so /login instructions would be noise.
        text += "\n\n" + _pick_line(DENY_FOOTERS_SUPER, f"{uid}:footer_super")
    elif scope != "group":
        text += "\n\n" + _pick_line(DENY_FOOTERS, f"{uid}:footer")
    return text

async def _delete_after(context, chat_id, message_id, delay):
    """Fire-and-forget cleanup so groups don't fill up with bot replies."""
    try:
        await asyncio.sleep(delay)
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass

async def deny_access(update, context, scope=None, event=None):
    """
    Single entry point for every "you are not allowed" reply.

    • Private chat  → full reply + how-to-get-access footer, stays in chat.
    • Group chat    → one short line that mentions the user, then both that
                      line and the offending command are deleted. Repeat
                      attempts inside the cooldown are cleaned up silently.
    • Callback taps → reply shown as a popup alert.

    Every denial is also mirrored to the owner's DM, so you see who tried what
    and exactly what the bot said back.
    """
    try:
        user = update.effective_user
        message = update.effective_message

        # Button/callback taps: popup alert, nothing left in chat.
        if update.callback_query:
            plain = re.sub(r"<[^>]+>", "", build_deny_message(user, scope or "private"))
            try:
                await update.callback_query.answer(plain[:190], show_alert=True)
            except Exception:
                pass
            await mirror_non_admin(context, update, bot_reply=plain[:190],
                                   event=event or "denied button tap")
            return False

        if message is None:
            return False

        in_group = update.effective_chat.type in ("group", "supergroup")
        scope = scope or ("group" if in_group else "private")

        if not in_group:
            reply = build_deny_message(user, scope)
            await message.reply_text(
                reply,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
            await mirror_non_admin(context, update, bot_reply=reply,
                                   event=event or "access denied (DM)")
            return False

        # ---- Group: keep it clean and spam-proof ----
        chat_id = update.effective_chat.id
        cool_key = (chat_id, user.id if user else 0)
        now = time.time()
        recently_replied = now - _DENY_COOLDOWN.get(cool_key, 0) < GROUP_DENY_COOLDOWN
        _DENY_COOLDOWN[cool_key] = now
        if len(_DENY_COOLDOWN) > 500:
            for k in [k for k, v in _DENY_COOLDOWN.items() if now - v > 300]:
                _DENY_COOLDOWN.pop(k, None)

        # Remove the command they tried, whether or not we reply.
        try:
            await message.delete()
        except Exception:
            pass

        if recently_replied:
            await mirror_non_admin(
                context, update, bot_reply=None,
                event=event or "access denied in group (reply suppressed by cooldown)")
            return False

        reply = build_deny_message(user, scope)
        try:
            sent = await context.bot.send_message(
                chat_id=chat_id,
                text=reply,
                parse_mode=ParseMode.HTML,
                message_thread_id=getattr(message, "message_thread_id", None),
                disable_web_page_preview=True,
                disable_notification=True
            )
            asyncio.create_task(_delete_after(context, chat_id, sent.message_id, GROUP_DENY_TTL))
        except Exception as e:
            logger.warning(f"Could not send group denial: {e}")
        await mirror_non_admin(context, update, bot_reply=reply,
                               event=event or "access denied (group)")
        return False
    except Exception as e:
        logger.error(f"Error in deny_access: {e}")
        return False

# Rotating reminders for admins who tap admin buttons inside the group.
PRIVATE_ONLY_NUDGES = [
    "\U0001F4E9 {user} \u2014 admin controls only work in our private chat.",
    "\U0001F4E9 {user} \u2014 let's do this in DM so the group stays clean.",
    "\U0001F4E9 {user} \u2014 this panel is DM-only, message me directly.",
    "\U0001F4E9 {user} \u2014 open our private chat and run it there.",
]

async def require_private_admin(update, context):
    """
    Check if user is admin AND in private chat.
    Returns True if allowed, False if not (and sends appropriate message).
    """
    try:
        user = update.effective_user
        
        # Check if admin
        if not is_admin(user.username if user else None):
            await deny_access(update, context)
            return False

        # Learn the owner's DM target so security alerts have somewhere to go.
        remember_owner_id(user, update)

        # Check if private chat
        if not is_private_chat(update):
            message = update.effective_message
            if message:
                nudge = _pick_line(PRIVATE_ONLY_NUDGES, f"{user.id}:private_only")
                sent = await message.reply_text(
                    nudge.format(user=user_tag(user)),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                    disable_notification=True   # silent: nobody gets pinged
                )
                asyncio.create_task(_delete_after(context, sent.chat_id, sent.message_id, 10))
            return False
        
        return True
    except Exception as e:
        logger.error(f"Error in require_private_admin: {e}")
        return False

# ==============================================================================
# 👥 ADMIN MANAGEMENT
# ==============================================================================
async def start_add_admin(update, context):
    """Start the add admin conversation"""
    try:
        if not await require_private_admin(update, context): return ConversationHandler.END
        
        # Only super admins can add other admins
        if not is_super_admin(update.effective_user.username):
            await deny_access(update, context, scope="super")
            return ConversationHandler.END
        
        current_admins = DB.get("admins", [])
        admin_list = "\n".join([f"• @{a}" for a in current_admins]) if current_admins else "<i>No additional admins</i>"
        
        await update.message.reply_text(
            "👥 <b>ADD NEW ADMIN</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<b>Current Admins:</b>\n{admin_list}\n\n"
            "<i>Enter the username to add (without @):</i>\n"
            "<i>Or send /cancel to abort.</i>",
            parse_mode=ParseMode.HTML
        )
        return ADD_ADMIN_INPUT
    except Exception as e:
        logger.error(f"Error in start_add_admin: {e}")
        await update.message.reply_text("❌ An error occurred.")
        return ConversationHandler.END

async def save_new_admin(update, context):
    """Save the new admin username"""
    try:
        # Normalise before storing: the admin list IS the authorisation key, so
        # 'Foo' and 'foo' must never be two different entries.
        username = _norm_username(update.message.text)

        if not re.fullmatch(r"[a-z0-9_]{4,32}", username):
            await update.message.reply_text(
                "❌ <b>INVALID USERNAME!</b>\n\n"
                "<i>Telegram handles are 4-32 characters, letters, digits and "
                "underscores only. Send it without the @.</i>",
                parse_mode=ParseMode.HTML
            )
            return ADD_ADMIN_INPUT

        if "admins" not in DB:
            DB["admins"] = []

        if is_admin(username):
            await update.message.reply_text(
                f"⚠️ <b>ALREADY AN ADMIN!</b>\n\n"
                f"<i>@{html.escape(username)} already has access.</i>",
                parse_mode=ParseMode.HTML
            )
            return ConversationHandler.END

        DB["admins"].append(username)
        DB["admins"] = sorted(set(DB["admins"]))
        audit("admin_added", update.effective_user, f"granted @{username}")
        save_db()
        
        await update.message.reply_text(
            f"✅ <b>ADMIN ADDED!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 <b>@{username}</b> is now an admin.\n\n"
            f"<i>They can now use all admin features!</i> 🎉",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Error in save_new_admin: {e}")
        await update.message.reply_text("❌ An error occurred.")
        return ConversationHandler.END

async def start_remove_admin(update, context):
    """Start the remove admin conversation"""
    try:
        if not await require_private_admin(update, context): return ConversationHandler.END
        
        if not is_super_admin(update.effective_user.username):
            await deny_access(update, context, scope="super")
            return ConversationHandler.END
        
        current_admins = DB.get("admins", [])
        if not current_admins:
            await update.message.reply_text(
                "📭 <b>NO ADMINS TO REMOVE!</b>\n\n"
                "<i>There are no additional admins.</i>",
                parse_mode=ParseMode.HTML
            )
            return ConversationHandler.END
        
        admin_list = "\n".join([f"• @{a}" for a in current_admins])
        
        await update.message.reply_text(
            "🗑️ <b>REMOVE ADMIN</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<b>Current Admins:</b>\n{admin_list}\n\n"
            "<i>Enter the username to remove (without @):</i>\n"
            "<i>Or send /cancel to abort.</i>",
            parse_mode=ParseMode.HTML
        )
        return REMOVE_ADMIN_INPUT
    except Exception as e:
        logger.error(f"Error in start_remove_admin: {e}")
        await update.message.reply_text("❌ An error occurred.")
        return ConversationHandler.END

async def remove_admin_save(update, context):
    """Remove the admin username"""
    try:
        # Case-insensitive: an admin stored as 'Foo' used to be unremovable by
        # typing 'foo', silently leaving a revoked admin with full access.
        username = _norm_username(update.message.text)
        matches = [a for a in DB.get("admins", []) if _norm_username(a) == username]

        if not matches:
            if is_super_admin(username):
                await update.message.reply_text(
                    f"🔒 <b>CANNOT REMOVE</b>\n\n"
                    f"<i>@{html.escape(username)} is set in ADMIN_USERNAMES. "
                    f"Remove them from the environment variable instead.</i>",
                    parse_mode=ParseMode.HTML
                )
                return ConversationHandler.END
            await update.message.reply_text(
                f"❌ <b>NOT FOUND!</b>\n\n"
                f"<i>@{html.escape(username)} is not in the admin list.</i>",
                parse_mode=ParseMode.HTML
            )
            return ConversationHandler.END

        for m in matches:
            DB["admins"].remove(m)
        audit("admin_removed", update.effective_user, f"revoked @{username}")
        save_db()
        
        await update.message.reply_text(
            f"✅ <b>ADMIN REMOVED!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 <b>@{username}</b> is no longer an admin.",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Error in remove_admin_save: {e}")
        await update.message.reply_text("❌ An error occurred.")
        return ConversationHandler.END

async def view_admins(update, context):
    """View all current admins"""
    try:
        if not await require_private_admin(update, context): return
        
        env_admins = ADMIN_USERNAMES if ADMIN_USERNAMES and ADMIN_USERNAMES != [''] else []
        db_admins = DB.get("admins", [])
        
        msg = "👥 <b>ADMIN LIST</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        if env_admins:
            msg += "🔐 <b>Primary Admins (ENV):</b>\n"
            for a in env_admins:
                msg += f"   • @{a}\n"
            msg += "\n"
        
        if db_admins:
            msg += "👤 <b>Additional Admins:</b>\n"
            for a in db_admins:
                msg += f"   • @{a}\n"
        else:
            msg += "<i>No additional admins added.</i>\n"
        
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Error in view_admins: {e}")
        await update.message.reply_text("❌ An error occurred.")

async def track_chats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Track when bot is added to groups - only update if no group linked"""
    result = update.my_chat_member.new_chat_member
    chat = update.effective_chat
    
    if result.status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR]:
        # Only update group if no group is currently linked
        current_group = DB.get("config", {}).get("group_id")
        
        if current_group is None:
            # No group linked - set this one
            DB["config"]["group_id"] = chat.id
            DB["config"]["group_name"] = chat.title
            save_db()
            logger.info(f"🆕 LINKED GROUP: {chat.title} ({chat.id})")
            
            await context.bot.send_message(
                chat_id=chat.id,
                text=f"✅ <b>VASUKI CONNECTED</b>\n"
                     f"<b>Group:</b> {chat.title}\n"
                     f"<b>Timezone:</b> IST (GMT+5:30)\n"
                     f"<i>Use /start in DM to manage scheduling.</i>",
                parse_mode=ParseMode.HTML
            )
        elif current_group == chat.id:
            # Same group - update name if changed
            if DB["config"]["group_name"] != chat.title:
                DB["config"]["group_name"] = chat.title
                save_db()
                logger.info(f"📝 Updated group name: {chat.title}")
        else:
            # Different group - log but don't overwrite
            logger.info(f"ℹ️ Bot added to {chat.title} but already linked to {DB['config']['group_name']}")

async def updategroup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Update the linked group ID - ENV ADMINS ONLY, responds in private"""
    chat = update.effective_chat
    user = update.effective_user
    
    # Only works in groups
    if chat.type == "private":
        await update.message.reply_text(
            "⚠️ <b>Use this command in a GROUP, not private chat!</b>",
            parse_mode=ParseMode.HTML
        )
        return
    
    # SUPER ADMINS ONLY. This repoints every pending job at the current chat, so
    # a password-tier admin who added the bot to their own group could otherwise
    # divert all class announcements and join links to themselves.
    if not is_admin(user.username):
        await deny_access(update, context, event="/updategroup")
        return
    if not is_super_admin(user.username):
        audit("updategroup_denied", user,
              f"chat={safe_text(chat.title, '?')} ({chat.id})", ok=False)
        await deny_access(update, context, scope="super")
        return

    
    # Delete the command message from group immediately
    try:
        await update.message.delete()
    except:
        pass  # May fail if bot lacks delete permission
    
    old_id = DB.get("config", {}).get("group_id")
    new_id = chat.id
    
    # Update config
    DB["config"]["group_id"] = new_id
    DB["config"]["group_name"] = chat.title
    
    # Update all pending jobs
    updated_jobs = update_all_jobs_chat_id(new_id)
    
    save_db()
    
    # Send response to admin's PRIVATE chat
    try:
        await context.bot.send_message(
            chat_id=user.id,
            text=f"✅ <b>GROUP UPDATED!</b>\n"
                 f"━━━━━━━━━━━━━━━━━━━━\n\n"
                 f"📍 <b>Group:</b> {chat.title}\n"
                 f"🆔 <b>New ID:</b> <code>{new_id}</code>\n"
                 f"🔄 <b>Jobs Updated:</b> {updated_jobs}\n\n"
                 f"<i>All scheduled messages will now be sent here.</i>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        # Fallback: send a silent message in group if private fails
        await context.bot.send_message(
            chat_id=chat.id,
            text=f"✅ Group updated! Check /verifytopics for details.",
            disable_notification=True
        )
    
    audit("updategroup", user,
          f"{old_id} → {new_id} ({safe_text(chat.title, '?')}), jobs={updated_jobs}")
    await notify_owner(
        context,
        "📍 <b>GROUP LINK CHANGED</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 {user_tag(user)}\n"
        f"🆔 <code>{old_id}</code> → <code>{new_id}</code>\n"
        f"📛 {html.escape(safe_text(chat.title, '?'))}\n"
        f"🔄 Jobs repointed: <b>{updated_jobs}</b>"
    )
    logger.info(f"🔄 Group updated: {old_id} → {new_id} ({chat.title})")

# ==============================================================================
# 🏠 8. CORE HANDLERS
# ==============================================================================

async def verify_topic_connectivity(bot, group_id, topic_id):
    """
    Verify topic is functional by sending and immediately deleting a test message.
    Returns: (success: bool, error_message: str or None)
    """
    if not group_id or not topic_id:
        return False, "No group or topic configured"
    
    try:
        # Send silent test message
        msg = await bot.send_message(
            chat_id=group_id,
            text="🔄 Topic verification...",
            message_thread_id=int(topic_id),
            disable_notification=True
        )
        # Immediately delete
        await msg.delete()
        return True, None
    except Exception as e:
        error = str(e).lower()
        if "thread" in error or "topic" in error:
            return False, "Topic not found or closed"
        elif "chat not found" in error:
            return False, "Group not accessible"
        else:
            return False, str(e)[:50]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_type = update.effective_chat.type

    # Strict Access Control - non-admins get a clear "admins only" reply, and the
    # owner gets a DM showing who opened the bot and what it said back.
    if not is_admin(user.username):
        await deny_access(update, context, event="opened the bot (/start)")
        return

    # Learn the owner's DM target so activity reports have somewhere to go.
    remember_owner_id(user, update)

    # GROUP/SUPERGROUP: Link and auto-delete message
    if chat_type in ['group', 'supergroup']:
        DB["config"]["group_id"] = update.effective_chat.id
        DB["config"]["group_name"] = update.effective_chat.title
        save_db()
        try:
            msg = await update.message.reply_text(
                f"✅ <b>VASUKI CONNECTED</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"<i>Linked to:</i> <b>{update.effective_chat.title}</b>\n\n"
                f"<i>Use /start in your DM for full admin access.</i>\n\n"
                f"<i>This message will auto-delete shortly.</i>",
                parse_mode=ParseMode.HTML,
                disable_notification=True  # Silent
            )
            # Schedule auto-delete after 1 seconds
            await asyncio.sleep(1)
            try:
                await msg.delete()
            except:
                pass
        except Exception as e:
            logger.error(f"Failed to reply in group start: {e}")
        return

    # PRIVATE CHAT: Show dashboard with topic verification
    grp_name = DB.get("config", {}).get("group_name", "❌ No Group Linked")
    grp_id = DB.get("config", {}).get("group_id")
    topics = DB.get("topics", {})
    
    # Verify group and topic connectivity
    group_status = "🟢" if grp_id else "🔴"
    topic_status = "🔴 None"
    topic_count = len(topics)
    
    if grp_id and topics:
        # Test first topic
        first_topic_id = list(topics.keys())[0]
        first_topic_name = topics[first_topic_id]
        success, error = await verify_topic_connectivity(context.bot, grp_id, first_topic_id)
        
        if success:
            topic_status = f"🟢 {topic_count} connected"
        else:
            topic_status = f"🟡 {topic_count} (verify needed)"
    elif topics:
        topic_status = f"🟡 {topic_count} (no group)"
    
    # Build keyboard with verify option
    kb = []
    if grp_id and topics:
        kb.append([InlineKeyboardButton("🔄 Verify Topics", callback_data="verify_topics")])
    
    try:
        await update.message.reply_text(
            f"<b>VASUKI — ADMIN DASHBOARD</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<i>Logged in as</i> <b>{user.first_name}</b>\n\n"
            f"<b>System Status</b>\n"
            f"┣ 📍 <b>Group:</b> {group_status} {grp_name}\n"
            f"┣ 💬 <b>Topics:</b> {topic_status}\n"
            f"┣ ⏰ <b>Time:</b> {datetime.now(IST).strftime('%H:%M IST')}\n"
            f"┣ 📅 <b>Scheduled Classes:</b> {len(DB.get('active_jobs', []))}\n"
            f"┗ 💾 <b>Storage:</b> {'☁️ Supabase' if supabase else '💻 Local'}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>Select an option from the menu below.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(kb) if kb else get_main_keyboard()
        )
        # Also show main keyboard
        if kb:
            await update.message.reply_text("📱 <b>Main Menu</b>", parse_mode=ParseMode.HTML, reply_markup=get_main_keyboard())
    except Exception as e:
        logger.error(f"Failed to send dashboard: {e}")

# Pagination constant
TOPICS_PER_PAGE = 10

async def verify_topics_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command handler for /verifytopics"""
    if not await require_private_admin(update, context): return
    
    await show_verify_topics_page(update, context, page=0)

async def verify_topics_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle pagination callbacks for verify topics"""
    # Same class of exposure as delete_nav: global handler, spoofable data.
    # This one leaks the linked group ID, every topic ID and the job count.
    if not await require_admin_callback(update, context): return
    query = update.callback_query
    await query.answer()
    
    data = query.data
    page = 0
    
    if data.startswith("verify_page_"):
        page = int(data.split("_")[-1])
    
    # "verify_topics" data implies page 0, which is default
    await show_verify_topics_page(query, context, page=page, is_callback=True)

async def show_verify_topics_page(update_or_query, context, page=0, is_callback=False):
    """Helper to show a page of verified topics"""
    grp_id = DB.get("config", {}).get("group_id")
    grp_name = DB.get("config", {}).get("group_name", "Unknown")
    topics = DB.get("topics", {})
    pending_jobs = len(DB.get("active_jobs", []))
    
    # Reply target
    target = update_or_query.message if is_callback else update_or_query.message
    if is_callback:
        edit_func = update_or_query.edit_message_text
    else:
        edit_func = target.reply_text

    if not grp_id:
        text = "❌ <b>No group linked!</b>\n\nUse /start in a group first."
        if is_callback: await edit_func(text, parse_mode=ParseMode.HTML)
        else: await edit_func(text, parse_mode=ParseMode.HTML)
        return
    
    if not topics:
        text = "❌ <b>No topics registered!</b>\n\nGo to a topic and use /topic TopicName"
        if is_callback: await edit_func(text, parse_mode=ParseMode.HTML)
        else: await edit_func(text, parse_mode=ParseMode.HTML)
        return

    # Convert to list and sort
    topic_items = list(topics.items())
    total_topics = len(topic_items)
    total_pages = (total_topics + TOPICS_PER_PAGE - 1) // TOPICS_PER_PAGE
    
    # Slice for current page
    start_idx = page * TOPICS_PER_PAGE
    end_idx = start_idx + TOPICS_PER_PAGE
    current_batch = topic_items[start_idx:end_idx]
    
    # Verify this batch
    if not is_callback: # Only show "Verifying..." on initial command
        initial_msg = await target.reply_text("🔄 <b>Verifying topics...</b>", parse_mode=ParseMode.HTML)
        edit_func = initial_msg.edit_text # Switch to editing the status msg

    results = []
    success_count = 0
    # Note: Success count here is only for THIS page. 
    # To get global success count we'd need to verify all, which is slow.
    # We'll just show status for current page items.
    
    for tid, name in current_batch:
        success, error = await verify_topic_connectivity(context.bot, grp_id, tid)
        icon = "✅" if success else "❌"
        status = f"(ID: {tid})" if success else f"- {error}"
        results.append(f"{icon} <b>{name}</b> {status}")
        if success: success_count += 1
        
    # Build Navigation Keyboard
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"verify_page_{page-1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"verify_page_{page+1}"))
    
    keyboard = [nav_row] if nav_row else []
    
    # Refresh button
    keyboard.append([InlineKeyboardButton("🔄 Refresh List", callback_data=f"verify_page_{page}")])
    
    result_msg = (
        f"🔍 <b>TOPIC VERIFICATION</b> (Page {page+1}/{total_pages})\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📍 <b>Group:</b> {grp_name}\n"
        f"🆔 <b>ID:</b> <code>{grp_id}</code>\n"
        f"📅 <b>Pending Jobs:</b> {pending_jobs}\n\n"
        + "\n".join(results) +
        f"\n\n💡 <i>Use /updategroup in your group to fix ID issues</i>"
    )
    
    await edit_func(result_msg, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_navigation(update, context):
    try:
        user = update.effective_user
        if not is_admin(user.username):
            await deny_access(update, context)
            return

        msg = update.message.text
        if "More Options" in msg:
            await update.message.reply_text(
                "📂 <b>ADVANCED TOOLS</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "<i>Pick a tool from below:</i> 🛠️",
                reply_markup=get_more_keyboard(),
                parse_mode=ParseMode.HTML
            )
        elif "Manage Admins" in msg:
            if not await require_private_admin(update, context): return
            await update.message.reply_text(
                "👥 <b>ADMIN MANAGEMENT</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "<i>Manage who can control this bot:</i> 👇",
                reply_markup=get_admin_mgmt_keyboard(),
                parse_mode=ParseMode.HTML
            )
        elif "Back" in msg:
            await update.message.reply_text(
                "🏠 <b>MAIN MENU</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "<i>What would you like to do?</i> ✨",
                reply_markup=get_main_keyboard(),
                parse_mode=ParseMode.HTML
            )
    except Exception as e:
        logger.error(f"Error in handle_navigation: {e}")
        await update.message.reply_text("❌ An error occurred.")

# NEW FEATURE: VIEW ALL SUBJECTS
async def view_all_subjects(update, context):
    if not await require_private_admin(update, context): return
    _ensure_db_shape()
    subjects = DB.get("subjects", {})
    # Check if any batch has at least one subject
    if not subjects or not any(bool(v) for v in subjects.values() if isinstance(v, list)):
        await update.message.reply_text(
            "📭 <b>NO SUBJECTS FOUND!</b>\n\n"
            "<i>Add subjects using</i> ➕ <b>Add Subject</b>",
            parse_mode=ParseMode.HTML
        )
        return

    msg = "📚 <b>REGISTERED SUBJECTS</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    for batch, sub_list in subjects.items():
        if isinstance(sub_list, list) and sub_list:
            msg += f"🏷️ <b>{html.escape(str(batch))}</b>\n"
            for s in sub_list:
                msg += f"   ├ 📖 {html.escape(str(s))}\n"
            msg += "\n"

    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

# ==============================================================================
# 🧙‍♂️ 9. SCHEDULING WIZARD
# ==============================================================================
async def cancel_wizard(update, context):
    await update.message.reply_text(
        "❌ <b>CANCELLED</b>\n\n"
        "<i>Operation cancelled. Use the menu to continue.</i>",
        parse_mode=ParseMode.HTML
    )
    return ConversationHandler.END

async def init_schedule_wizard(update, context):
    if not await require_private_admin(update, context): return ConversationHandler.END
    if not DB["config"]["group_id"]:
        await update.message.reply_text(
            "⛔ <b>NO GROUP LINKED!</b>\n\n"
            "<i>Add me to a group first, then use /start there.</i>",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END
    
    text = update.message.text
    batch = "CSDA" if "CSDA" in text else "AICS"
    context.user_data['sch_batch'] = batch
    context.user_data['sch_days'] = [] 
    
    subs = DB["subjects"].get(batch, [])
    if not subs:
        await update.message.reply_text(
            f"⚠️ <b>NO SUBJECTS IN {batch}!</b>\n\n"
            f"<i>Use</i> ➕ <b>Add Subject</b> <i>first.</i>",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END
    
    rows = [[InlineKeyboardButton(f"📖 {s}", callback_data=f"pick_{s}")] for s in subs]
    await update.message.reply_text(
        f"📚 <b>SELECT SUBJECT</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎯 <i>Batch:</i> <b>{batch}</b>\n\n"
        f"<i>Select a subject to schedule:</i>",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML
    )
    return SELECT_SUB_OR_ADD

async def wizard_pick_sub(update, context):
    context.user_data['sch_sub'] = update.callback_query.data.split("_")[1]
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "📅 <b>SELECT DAYS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Tap to toggle days, then press</i> <b>DONE</b> <i>to confirm.</i>",
        reply_markup=days_keyboard([]),
        parse_mode=ParseMode.HTML
    )
    return SELECT_DAYS

async def wizard_toggle_days(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "days_done":
        if not context.user_data.get('sch_days'): return SELECT_DAYS
        await query.edit_message_text(
            "📅 <b>START DATE</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Enter in format:</i> <code>DD-MM-YYYY</code>\n"
            "<i>Or type:</i> <code>Today</code>",
            parse_mode=ParseMode.HTML
        )
        return INPUT_START_DATE
    
    day = query.data.split("_")[1]
    days = context.user_data.get('sch_days', [])
    if day in days: days.remove(day)
    else: days.append(day)
    context.user_data['sch_days'] = days
    await query.edit_message_reply_markup(days_keyboard(days))
    return SELECT_DAYS

async def wizard_start_date(update, context):
    text = update.message.text.strip().lower()
    try:
        if text == 'today': start_dt = datetime.now(IST)
        else: start_dt = datetime.strptime(text, "%d-%m-%Y").replace(tzinfo=IST)
        context.user_data['start_dt'] = start_dt
        await update.message.reply_text(
            "🏁 <b>END DATE</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Enter in format:</i> <code>DD-MM-YYYY</code>\n"
            "<i>Or type:</i> <code>None</code> <i>for one-time class</i>",
            parse_mode=ParseMode.HTML
        )
        return INPUT_END_DATE
    except:
        await update.message.reply_text(
            "❌ <b>INVALID FORMAT!</b>\n\n"
            "<i>Please use:</i> <code>DD-MM-YYYY</code>",
            parse_mode=ParseMode.HTML
        )
        return INPUT_START_DATE

async def wizard_end_date(update, context):
    text = update.message.text.strip().lower()
    try:
        if text == 'none': context.user_data['end_dt'] = None
        else: context.user_data['end_dt'] = datetime.strptime(text, "%d-%m-%Y").replace(tzinfo=IST)
        await update.message.reply_text(
            "⏰ <b>CLASS START TIME</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Enter start time in 24h format:</i> <code>HH:MM</code>\n"
            "<i>Example:</i> <code>14:30</code>",
            parse_mode=ParseMode.HTML
        )
        return INPUT_TIME
    except:
        await update.message.reply_text(
            "❌ <b>INVALID FORMAT!</b>\n\n"
            "<i>Please use:</i> <code>DD-MM-YYYY</code>",
            parse_mode=ParseMode.HTML
        )
        return INPUT_END_DATE

async def wizard_time(update, context):
    text = update.message.text.strip()
    try:
        parts = text.split(":")
        if len(parts) != 2:
            raise ValueError
        h, m = int(parts[0]), int(parts[1])
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
        start_time_str = f"{h:02d}:{m:02d}"
    except Exception:
        await update.message.reply_text(
            "❌ <b>INVALID TIME FORMAT!</b>\n\n"
            "<i>Please use 24h format:</i> <code>HH:MM</code>\n"
            "<i>Example:</i> <code>14:30</code>",
            parse_mode=ParseMode.HTML
        )
        return INPUT_TIME

    context.user_data['sch_start_time'] = start_time_str
    context.user_data['sch_time'] = start_time_str
    context.user_data['sch_time_display'] = start_time_str

    await update.message.reply_text(
        "⏰ <b>CLASS END TIME</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Enter end time in 24h format:</i> <code>HH:MM</code>\n"
        "<i>Example:</i> <code>15:30</code>\n\n"
        "<i>Or type:</i> <code>None</code> <i>to skip end time</i>",
        parse_mode=ParseMode.HTML
    )
    return INPUT_END_TIME

async def wizard_end_time(update, context):
    text = update.message.text.strip()
    start_time = context.user_data.get('sch_start_time', context.user_data.get('sch_time', '12:00'))

    if text.lower() in ('none', 'skip', 'no', '-'):
        context.user_data['sch_end_time'] = None
        context.user_data['sch_time_display'] = start_time
    else:
        try:
            parts = text.split(":")
            if len(parts) != 2:
                raise ValueError
            h, m = int(parts[0]), int(parts[1])
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError
            end_time_str = f"{h:02d}:{m:02d}"
            context.user_data['sch_end_time'] = end_time_str
            context.user_data['sch_time_display'] = f"{start_time} - {end_time_str}"
        except Exception:
            await update.message.reply_text(
                "❌ <b>INVALID TIME FORMAT!</b>\n\n"
                "<i>Please use 24h format:</i> <code>HH:MM</code> (e.g. <code>15:30</code>)\n"
                "<i>Or type:</i> <code>None</code> <i>to skip.</i>",
                parse_mode=ParseMode.HTML
            )
            return INPUT_END_TIME

    await update.message.reply_text(
        "🔗 <b>CLASS LINK</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Paste the meeting link</i>\n"
        "<i>Or type:</i> <code>None</code>",
        parse_mode=ParseMode.HTML
    )
    return INPUT_LINK

async def wizard_link(update, context):
    context.user_data['sch_link'] = update.message.text
    
    # Check for topics
    topics = DB.get("topics", {})
    default_tid, default_name = get_class_topic()

    if topics:
        kb = []
        # The default set by /classtopic goes first, so the common case is one
        # tap and per-class overrides stay available underneath.
        if default_tid:
            kb.append([InlineKeyboardButton(
                f"⭐ Use Default ({default_name})", callback_data="topic_default")])
        row = []
        for tid, name in topics.items():
            row.append(InlineKeyboardButton(name, callback_data=f"topic_{tid}"))
            if len(row) == 2:
                kb.append(row)
                row = []
        if row: kb.append(row)
        kb.append([InlineKeyboardButton("📢 General (No Topic)", callback_data="topic_general")])

        hint = (f"<i>Default is</i> <b>{html.escape(str(default_name))}</b>"
                f" <i>(set with /classtopic).</i>\n\n" if default_tid else "")
        await update.message.reply_text(
            "💬 <b>SELECT TOPIC</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{hint}"
            "<i>Select the topic where this class notification should be posted:</i>",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )
        return SELECT_TOPIC
    else:
        # No topics registered — inherit whatever /classtopic points at.
        context.user_data['sch_topic_id'] = None
        return await show_offset_selection(update)

async def wizard_topic_selection(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "topic_default":
        # None means "inherit", so the class follows /classtopic even if it
        # changes later.
        context.user_data['sch_topic_id'] = None
    elif data == "topic_general":
        context.user_data['sch_topic_id'] = GENERAL_TOPIC
    else:
        tid = data.replace("topic_", "")
        try:
            context.user_data['sch_topic_id'] = int(tid)
        except ValueError:
            context.user_data['sch_topic_id'] = None

    return await show_offset_selection(update)

async def show_offset_selection(update):
    kb = [
        [InlineKeyboardButton("⏰ Exact Time", callback_data="offset_0")],
        [InlineKeyboardButton("⏱️ 5 Mins Before", callback_data="offset_5"),
         InlineKeyboardButton("⏱️ 10 Mins Before", callback_data="offset_10")],
        [InlineKeyboardButton("⏱️ 15 Mins Before", callback_data="offset_15"),
         InlineKeyboardButton("✏️ Custom", callback_data="offset_custom")]
    ]
    
    msg_text = (
        "⌛ <b>NOTIFICATION TIMING</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>How far in advance should the notification be sent?</i>"
    )
    
    if hasattr(update, 'callback_query') and update.callback_query:
        await update.callback_query.edit_message_text(
            msg_text,
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text(
            msg_text,
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )
    return SELECT_OFFSET

async def wizard_offset(update, context):
    query = update.callback_query
    await query.answer()
    
    if query.data == "offset_custom":
        await query.edit_message_text(
            "⏱️ <b>CUSTOM TIMING</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Enter minutes before class (1-60):</i>\n"
            "<i>Example:</i> <code>20</code>",
            parse_mode=ParseMode.HTML
        )
        return CUSTOM_OFFSET_INPUT
    
    context.user_data['sch_offset'] = int(query.data.split("_")[1])
    kb = [
        [InlineKeyboardButton("✨ AI Auto-Write", callback_data="msg_ai")],
        [InlineKeyboardButton("✍️ Manual Message", callback_data="msg_manual")]
    ]
    await query.edit_message_text(
        "📝 <b>MESSAGE STYLE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Select how the class announcement should be composed:</i>",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.HTML
    )
    return MSG_TYPE_CHOICE

async def wizard_custom_offset(update, context):
    """Handle custom offset input"""
    try:
        mins = int(update.message.text.strip())
        if mins < 1 or mins > 60:
            await update.message.reply_text(
                "❌ <b>INVALID!</b> Enter 1-60 minutes.",
                parse_mode=ParseMode.HTML
            )
            return CUSTOM_OFFSET_INPUT
        
        context.user_data['sch_offset'] = mins
        kb = [
            [InlineKeyboardButton("✨ AI Auto-Write", callback_data="msg_ai")],
            [InlineKeyboardButton("✍️ Manual Message", callback_data="msg_manual")]
        ]
        await update.message.reply_text(
            "📝 <b>MESSAGE STYLE</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"⏱️ <i>Notification timing:</i> <b>{mins} minutes before class</b>\n\n"
            "<i>Select how the class announcement should be composed:</i>",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )
        return MSG_TYPE_CHOICE
    except ValueError:
        await update.message.reply_text(
            "❌ <b>INVALID!</b> Enter a number (1-60).",
            parse_mode=ParseMode.HTML
        )
        return CUSTOM_OFFSET_INPUT

async def wizard_msg_choice(update, context):
    if update.callback_query.data == "msg_manual":
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "✍️ <b>CUSTOM MESSAGE</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Type your announcement below:</i>",
            parse_mode=ParseMode.HTML
        )
        return INPUT_MANUAL_MSG
    else:
        context.user_data['sch_manual_msg'] = None
        return await wizard_finalize(update.callback_query, context)

async def wizard_manual_msg(update, context):
    context.user_data['sch_manual_msg'] = update.message.text
    return await wizard_finalize(update, context)

async def wizard_finalize(update_obj, context):
    d = context.user_data
    batch, sub, days = d['sch_batch'], d['sch_sub'], d['sch_days']
    start_dt, end_dt = d['start_dt'], d['end_dt']
    t_str = d['sch_time']
    try: h, m = map(int, t_str.split(':'))
    except: return ConversationHandler.END

    day_map = {"Mon":0, "Tue":1, "Wed":2, "Thu":3, "Fri":4, "Sat":5, "Sun":6}
    target_weekdays = [day_map[day] for day in days]
    dates = []
    
    if end_dt:
        curr = start_dt
        while curr <= end_dt:
            if curr.weekday() in target_weekdays: dates.append(curr)
            curr += timedelta(days=1)
    else:
        for wd in target_weekdays:
            curr = start_dt
            delta = wd - curr.weekday()
            if delta < 0: delta += 7
            dates.append(curr + timedelta(days=delta))

    count = 0
    gid = DB["config"]["group_id"]
    if not gid: return ConversationHandler.END

    for dt in dates:
        run_dt = dt.replace(hour=h, minute=m, second=0)
        notify_dt = run_dt - timedelta(minutes=d['sch_offset'])
        job_id = f"{batch}_{int(time.time())}_{count}"
        display_time_str = d.get('sch_time_display', t_str)
        job_data = {
            "batch": batch, "subject": sub, "time_display": display_time_str, 
            "link": d['sch_link'], "manual_msg": d.get('sch_manual_msg'),
            "msg_type": "MANUAL" if d.get('sch_manual_msg') else "AI",
            "message_thread_id": d.get('sch_topic_id')
        }
        context.job_queue.run_once(send_alert_job, notify_dt, chat_id=gid, name=job_id, data=job_data)
        add_job_to_db(job_id, notify_dt.timestamp(), gid, job_data)
        count += 1
    
    topic_name = _topic_label(d.get('sch_topic_id'))
    time_summary = _format_time_12h(d.get('sch_time_display', t_str))
    
    msg = (
        f"✅ <b>SCHEDULED SUCCESSFULLY</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>{count} class(es) added to schedule.</b>\n\n"
        f"📌 <i>Subject:</i> <b>{sub}</b>\n"
        f"🎯 <i>Batch:</i> <b>{batch}</b>\n"
        f"💬 <i>Topic:</i> <b>{topic_name}</b>\n"
        f"⏰ <i>Time:</i> <b>{time_summary}</b>\n\n"
        f"<i>Notifications will be dispatched automatically.</i>"
    )
    if isinstance(update_obj, Update): await update_obj.message.reply_text(msg, parse_mode=ParseMode.HTML)
    else: await update_obj.message.reply_text(msg, parse_mode=ParseMode.HTML)
    return ConversationHandler.END

# ==============================================================================
# 📅 9B. COMBINED SCHEDULE WIZARD (Both CSDA & AICS)
# ==============================================================================
async def init_combined_schedule_wizard(update, context):
    """Schedule a class for BOTH CSDA and AICS simultaneously"""
    if not await require_private_admin(update, context): return ConversationHandler.END
    if not DB["config"]["group_id"]:
        await update.message.reply_text(
            "⛔ <b>NO GROUP LINKED!</b>\n\n"
            "<i>Add me to a group first, then use /start there.</i>",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END
    
    context.user_data['sch_batch'] = 'BOTH'  # Mark as combined
    context.user_data['sch_days'] = []
    
    # Combine subjects from both batches (union, no duplicates)
    csda_subs = DB["subjects"].get("CSDA", [])
    aics_subs = DB["subjects"].get("AICS", [])
    all_subs = list(dict.fromkeys(csda_subs + aics_subs))  # Preserve order, remove dupes
    
    if not all_subs:
        await update.message.reply_text(
            "⚠️ <b>NO SUBJECTS FOUND!</b>\n\n"
            "<i>Use</i> ➕ <b>Add Subject</b> <i>to add subjects to CSDA or AICS first.</i>",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END
    
    rows = [[InlineKeyboardButton(f"📖 {s}", callback_data=f"cpick_{s}")] for s in all_subs]
    await update.message.reply_text(
        "📅 <b>SCHEDULE FOR BOTH BATCHES</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🎯 <i>Batch:</i> <b>CSDA + AICS</b>\n\n"
        "<i>Select a subject. The class will be scheduled for both batches.</i>",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML
    )
    return COMBINED_SELECT_SUB

async def combined_pick_sub(update, context):
    """Handle subject selection for combined schedule"""
    context.user_data['sch_sub'] = update.callback_query.data.replace("cpick_", "")
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "📅 <b>SELECT DAYS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Tap to toggle days, then hit</i> <b>DONE</b> 🚀",
        reply_markup=days_keyboard([]),
        parse_mode=ParseMode.HTML
    )
    return SELECT_DAYS

async def combined_wizard_finalize(update_obj, context):
    """Finalize scheduling for BOTH CSDA and AICS batches"""
    d = context.user_data
    sub, days = d['sch_sub'], d['sch_days']
    start_dt, end_dt = d['start_dt'], d['end_dt']
    t_str = d['sch_time']
    try: h, m = map(int, t_str.split(':'))
    except: return ConversationHandler.END

    day_map = {"Mon":0, "Tue":1, "Wed":2, "Thu":3, "Fri":4, "Sat":5, "Sun":6}
    target_weekdays = [day_map[day] for day in days]
    dates = []
    
    if end_dt:
        curr = start_dt
        while curr <= end_dt:
            if curr.weekday() in target_weekdays: dates.append(curr)
            curr += timedelta(days=1)
    else:
        for wd in target_weekdays:
            curr = start_dt
            delta = wd - curr.weekday()
            if delta < 0: delta += 7
            dates.append(curr + timedelta(days=delta))

    count = 0
    gid = DB["config"]["group_id"]
    if not gid: return ConversationHandler.END

    # Schedule a SINGLE message for BOTH batches
    for dt in dates:
        run_dt = dt.replace(hour=h, minute=m, second=0)
        notify_dt = run_dt - timedelta(minutes=d['sch_offset'])
        job_id = f"COMBINED_{int(time.time())}_{count}"
        display_time_str = d.get('sch_time_display', t_str)
        job_data = {
            "batch": "CSDA & AICS", "subject": sub, "time_display": display_time_str, 
            "link": d['sch_link'], "manual_msg": d.get('sch_manual_msg'),
            "msg_type": "MANUAL" if d.get('sch_manual_msg') else "AI",
            "message_thread_id": d.get('sch_topic_id')
        }
        context.job_queue.run_once(send_alert_job, notify_dt, chat_id=gid, name=job_id, data=job_data)
        add_job_to_db(job_id, notify_dt.timestamp(), gid, job_data)
        count += 1
    
    topic_name = _topic_label(d.get('sch_topic_id'))
    time_summary = _format_time_12h(d.get('sch_time_display', t_str))
    
    msg = (
        f"✅ <b>SCHEDULED SUCCESSFULLY</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>{count} class(es) added to schedule.</b>\n\n"
        f"📌 <i>Subject:</i> <b>{sub}</b>\n"
        f"🎯 <i>Batch:</i> <b>CSDA + AICS</b>\n"
        f"💬 <i>Topic:</i> <b>{topic_name}</b>\n"
        f"⏰ <i>Time:</i> <b>{time_summary}</b>\n\n"
        f"<i>Notifications will be dispatched to both batches.</i>"
    )
    if isinstance(update_obj, Update): await update_obj.message.reply_text(msg, parse_mode=ParseMode.HTML)
    else: await update_obj.message.reply_text(msg, parse_mode=ParseMode.HTML)
    return ConversationHandler.END

async def combined_wizard_msg_choice(update, context):
    """Handle message type choice for combined wizard"""
    if update.callback_query.data == "msg_manual":
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "✍️ <b>CUSTOM MESSAGE</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Type your announcement below:</i>",
            parse_mode=ParseMode.HTML
        )
        return INPUT_MANUAL_MSG
    else:
        context.user_data['sch_manual_msg'] = None
        return await combined_wizard_finalize(update.callback_query, context)

async def combined_wizard_manual_msg(update, context):
    """Handle manual message input for combined wizard"""
    context.user_data['sch_manual_msg'] = update.message.text
    return await combined_wizard_finalize(update, context)

# ==============================================================================
# ➕ 10. ADD SUBJECT & EDIT
# ==============================================================================
async def start_add_sub(update, context):
    if not await require_private_admin(update, context): return ConversationHandler.END
    kb = [
        [InlineKeyboardButton("🟦 CSDA", callback_data="sub_CSDA"),
         InlineKeyboardButton("🟧 AICS", callback_data="sub_AICS")],
        [InlineKeyboardButton("🟪 Both (CSDA + AICS)", callback_data="sub_BOTH")]
    ]
    await update.message.reply_text(
        "➕ <b>ADD NEW SUBJECT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Select the batch:</i> 👇",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.HTML
    )
    return SELECT_BATCH

async def save_batch_for_sub(update, context):
    # callback_data is "sub_CSDA", "sub_AICS", or "sub_BOTH"
    # Use split with maxsplit=1 so "BOTH" (one part after prefix) is captured correctly
    raw = update.callback_query.data  # e.g. "sub_CSDA"
    context.user_data['temp_batch'] = raw.split("_", 1)[1]  # "CSDA" / "AICS" / "BOTH"
    await update.callback_query.answer()
    batch_label = context.user_data['temp_batch']
    if batch_label == "BOTH":
        batch_display = "CSDA + AICS"
    else:
        batch_display = batch_label
    await update.callback_query.edit_message_text(
        f"📝 <b>SUBJECT NAME</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎯 <i>Batch:</i> <b>{html.escape(batch_display)}</b>\n\n"
        f"<i>Type the subject name below:</i>",
        parse_mode=ParseMode.HTML
    )
    return NEW_SUBJECT_INPUT

async def save_new_sub(update, context):
    b = context.user_data.get('temp_batch', 'CSDA')
    s = update.message.text.strip() if update.message.text else ""
    if not s:
        await update.message.reply_text(
            "⚠️ <b>Subject name cannot be empty.</b>\n\n<i>Please type a name.</i>",
            parse_mode=ParseMode.HTML
        )
        return NEW_SUBJECT_INPUT
    _ensure_db_shape()
    if b == "BOTH":
        added_to = []
        for batch_key in ("CSDA", "AICS"):
            if not isinstance(DB["subjects"].get(batch_key), list):
                DB["subjects"][batch_key] = []
            if s not in DB["subjects"][batch_key]:
                DB["subjects"][batch_key].append(s)
                added_to.append(batch_key)
        save_db()
        if added_to:
            added_str = " &amp; ".join(added_to)
        else:
            added_str = "CSDA &amp; AICS (already existed)"
        await update.message.reply_text(
            f"✅ <b>SUBJECT ADDED TO BOTH BATCHES</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📖 <b>{html.escape(s)}</b>\n"
            f"🎯 <i>Batches:</i> <b>{added_str}</b>\n\n"
            f"<i>This subject is now available for scheduling.</i>",
            parse_mode=ParseMode.HTML
        )
    else:
        if not isinstance(DB["subjects"].get(b), list):
            DB["subjects"][b] = []
        if s not in DB["subjects"][b]:
            DB["subjects"][b].append(s)
            save_db()
        await update.message.reply_text(
            f"✅ <b>SUBJECT ADDED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📖 <b>{html.escape(s)}</b>\n"
            f"🎯 <i>Batch:</i> <b>{html.escape(b)}</b>\n\n"
            f"<i>This subject is now available for scheduling.</i>",
            parse_mode=ParseMode.HTML
        )
    return ConversationHandler.END

# ==============================================================================
# 🧭 CLASS BROWSER — SHARED BATCH ▸ SUBJECT ▸ SESSION NAVIGATION
# ==============================================================================
# Edit, Delete and View Schedule all used to dump every scheduled session into
# one flat paginated list, with subject, batch, day and time crammed into a
# single truncated button label. Past ~15 classes that is unreadable.
#
# These helpers build the same three-level drill-down for all three features, so
# the navigation is learned once. Callback data carries INDEXES, never job names
# or subject text: it keeps every callback comfortably under Telegram's 64-byte
# cap, and it stops the job name being echoed into button data (the old
# "kill_<job name>" scheme published the exact string needed to delete a class
# to anyone who could see the keyboard).

CLASS_NAV_PAGE_SIZE = 8

# key, chip, label. Order is the order shown.
BATCH_VIEWS = (
    ("csda", "🟦", "CSDA"),
    ("aics", "🟧", "AICS"),
    ("both", "🟪", "Both / Shared"),
    ("all",  "📋", "All Batches"),
)


def _batch_kind(batch):
    """
    Bucket a stored batch string into one of the picker's views.

    Batch text is not a clean enum: the combined wizard writes "CSDA & AICS",
    the custom-message wizard writes "BOTH", and the timetable scanner writes
    whatever the vision model produced. Classify on content instead of equality.
    """
    b = str(batch or "").strip().upper()
    has_csda, has_aics = "CSDA" in b, "AICS" in b
    if b in ("BOTH", "ALL", "COMBINED") or (has_csda and has_aics):
        return "both"
    if has_csda:
        return "csda"
    if has_aics:
        return "aics"
    return "other"


def _job_sort_key(job):
    d = safe_job_data(job)
    return (
        str(d.get('subject') or '').lower(),
        str(d.get('batch') or '').lower(),
        job.next_t if job.next_t else datetime.min.replace(tzinfo=IST),
    )


def _collect_class_jobs(context):
    """Every schedulable class/message job, sorted by subject then batch then time.

    safe_job_data matters here: job_queue.jobs() also returns internal jobs
    (cleanup, keep-alive, night summary) created with data=None.
    """
    out = []
    for j in context.job_queue.jobs():
        d = safe_job_data(j)
        if j.name and d and 'batch' in d:
            out.append(j)
    out.sort(key=_job_sort_key)
    return out


def _jobs_for_view(jobs, view):
    if view == "all":
        return list(jobs)
    return [j for j in jobs if _batch_kind(safe_job_data(j).get('batch')) == view]


def _subject_groups(jobs):
    """
    [(subject, batch, [jobs])] sorted by subject, sessions sorted by time.

    Grouped by subject AND batch, not subject alone: the edit/delete scope steps
    match on both, so a subject taught separately to each batch has to stay two
    separate rows or "apply to all" would silently reach across batches.
    """
    grouped = {}
    for j in jobs:
        d = safe_job_data(j)
        key = (str(d.get('subject') or 'Class'), str(d.get('batch') or '—'))
        grouped.setdefault(key, []).append(j)
    for sessions in grouped.values():
        sessions.sort(key=lambda x: x.next_t if x.next_t else datetime.min.replace(tzinfo=IST))
    return [
        (subject, batch, sessions)
        for (subject, batch), sessions in sorted(
            grouped.items(), key=lambda i: (i[0][0].lower(), i[0][1].lower())
        )
    ]


def _batch_chip(batch):
    kind = _batch_kind(batch)
    for key, chip, _ in BATCH_VIEWS:
        if key == kind:
            return chip
    return "🟪"


def _view_label(view):
    for key, chip, label in BATCH_VIEWS:
        if key == view:
            return f"{chip} {label}"
    return "📋 All Batches"


def _short_subject(subject, limit=24):
    """Course code if there is one, else a trimmed subject name."""
    code, name = _split_subject(subject)
    label = code or name or "Class"
    return label if len(label) <= limit else label[:limit - 1] + "…"


def _session_bits(job):
    """(day label, formatted time) for one session, never raising."""
    d = safe_job_data(job)
    try:
        day_str = job.next_t.strftime("%a, %d %b")
        time_raw = d.get('time_display') or job.next_t.strftime("%H:%M")
    except Exception:
        day_str = ""
        time_raw = d.get('time_display', '')
    return day_str, _format_time_12h(time_raw)


def _paginate(items, page):
    total = max(1, (len(items) + CLASS_NAV_PAGE_SIZE - 1) // CLASS_NAV_PAGE_SIZE)
    page = max(0, min(int(page or 0), total - 1))
    start = page * CLASS_NAV_PAGE_SIZE
    return items[start:start + CLASS_NAV_PAGE_SIZE], page, total


def _batch_picker_rows(jobs, prefix):
    """Level-1 rows. Empty batches are hidden; 'All' is always offered."""
    rows = []
    for key, chip, label in BATCH_VIEWS:
        count = len(_jobs_for_view(jobs, key))
        if key != "all" and count == 0:
            continue
        rows.append([InlineKeyboardButton(
            f"{chip} {label} ({count})", callback_data=f"{prefix}b_{key}"
        )])
    return rows


def _nav_row(prefix, kind, page, total):
    row = []
    if page > 0:
        row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"{prefix}pg{kind}_{page - 1}"))
    if page < total - 1:
        row.append(InlineKeyboardButton("➡️ Next", callback_data=f"{prefix}pg{kind}_{page + 1}"))
    return row


def _crumb(title, view=None, subject=None):
    """Breadcrumb header so the current depth is always visible."""
    parts = [title]
    if view:
        parts.append(_view_label(view))
    if subject:
        parts.append(html.escape(_short_subject(subject, 28)))
    return " › ".join(parts)


async def start_edit(update, context):
    """Level 1 of the edit browser: pick a batch."""
    if not await require_private_admin(update, context): return ConversationHandler.END

    class_jobs = _collect_class_jobs(context)
    if not class_jobs:
        await update.message.reply_text(
            "📭 <b>NO CLASSES FOUND!</b>\n\n"
            "<i>Schedule some classes first.</i>",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    context.user_data.pop('edit_view', None)
    context.user_data.pop('edit_sub_idx', None)

    rows = _batch_picker_rows(class_jobs, "edit_")
    rows.append([InlineKeyboardButton("🔙 Cancel", callback_data="edit_cancel")])

    await update.message.reply_text(
        f"✏️ <b>EDIT CLASS</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>{len(class_jobs)} scheduled session(s)</i>\n\n"
        "<i>Select a batch to manage:</i> 👇",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML
    )
    return EDIT_SELECT_BATCH


async def _edit_render_batches(query, context, class_jobs):
    rows = _batch_picker_rows(class_jobs, "edit_")
    rows.append([InlineKeyboardButton("🔙 Cancel", callback_data="edit_cancel")])
    await query.edit_message_text(
        f"✏️ <b>EDIT CLASS</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>{len(class_jobs)} scheduled session(s)</i>\n\n"
        "<i>Select a batch to manage:</i> 👇",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML
    )
    return EDIT_SELECT_BATCH


async def _edit_render_subjects(query, context, class_jobs):
    """Level 2: subjects inside the chosen batch view."""
    view = context.user_data.get('edit_view', 'all')
    view_jobs = _jobs_for_view(class_jobs, view)
    groups = _subject_groups(view_jobs)

    if not groups:
        return await _edit_render_batches(query, context, class_jobs)

    page_items, page, total_pages = _paginate(groups, context.user_data.get('edit_sub_page', 0))
    context.user_data['edit_sub_page'] = page
    offset = page * CLASS_NAV_PAGE_SIZE

    rows = []
    for i, (subject, batch, sessions) in enumerate(page_items):
        chip = _batch_chip(batch) if view == "all" else "📖"
        label = f"{chip} {_short_subject(subject, 22)} ({len(sessions)})"
        rows.append([InlineKeyboardButton(label, callback_data=f"edit_s_{offset + i}")])

    nav = _nav_row("edit_", "sub", page, total_pages)
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔙 Back to Batches", callback_data="edit_back_batches")])

    page_note = f"<i>Page {page + 1}/{total_pages} · </i>" if total_pages > 1 else ""
    await query.edit_message_text(
        f"✏️ <b>{_crumb('EDIT CLASS', view)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{page_note}<i>{len(groups)} subject(s) · {len(view_jobs)} session(s)</i>\n\n"
        "<i>Select a subject:</i> 👇",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML
    )
    return EDIT_SELECT_SUBJECT


async def _edit_render_sessions(query, context, class_jobs):
    """Level 3: the individual sessions of one subject."""
    view = context.user_data.get('edit_view', 'all')
    groups = _subject_groups(_jobs_for_view(class_jobs, view))
    idx = context.user_data.get('edit_sub_idx')

    if idx is None or idx >= len(groups):
        # The subject vanished (fired or deleted) — fall back a level rather
        # than showing an index error.
        return await _edit_render_subjects(query, context, class_jobs)

    subject, batch, sessions = groups[idx]
    context.user_data['edit_subject'] = subject
    context.user_data['edit_batch'] = batch

    page_items, page, total_pages = _paginate(sessions, context.user_data.get('edit_job_page', 0))
    context.user_data['edit_job_page'] = page
    offset = page * CLASS_NAV_PAGE_SIZE

    rows = []
    for i, job in enumerate(page_items):
        day_str, time_str = _session_bits(job)
        rows.append([InlineKeyboardButton(
            f"🗓 {day_str} · ⏰ {time_str}", callback_data=f"edit_j_{offset + i}"
        )])

    nav = _nav_row("edit_", "job", page, total_pages)
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔙 Back to Subjects", callback_data="edit_back_subjects")])

    page_note = f"<i>Page {page + 1}/{total_pages}</i>\n" if total_pages > 1 else ""
    await query.edit_message_text(
        f"✏️ <b>{_crumb('EDIT CLASS', view, subject)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📖 <b>{html.escape(safe_text(subject, 'Class'))}</b>\n"
        f"🎯 {_batch_chip(batch)} <b>{html.escape(safe_text(batch, '—'))}</b> · "
        f"{len(sessions)} session(s)\n{page_note}\n"
        "<i>Select a session to modify:</i> 👇",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML
    )
    return EDIT_SELECT_JOB


async def _edit_render_fields(query, context, job):
    """Level 4: what to change on the chosen session."""
    job_data = safe_job_data(job)
    context.user_data['edit_job_name'] = job.name
    context.user_data['old_job_data'] = job_data
    context.user_data['old_next_t'] = job.next_t

    kb = [
        [InlineKeyboardButton("⏰ Start Time", callback_data="field_time"),
         InlineKeyboardButton("⏰ End Time", callback_data="field_endtime")],
        [InlineKeyboardButton("📅 Date", callback_data="field_date"),
         InlineKeyboardButton("🔗 Link", callback_data="field_link")],
        [InlineKeyboardButton("📝 Message", callback_data="field_msg"),
         InlineKeyboardButton("💬 Topic", callback_data="field_topic")],
        [InlineKeyboardButton("🔙 Back to Sessions", callback_data="field_back")],
        [InlineKeyboardButton("❌ Cancel", callback_data="field_cancel")],
    ]

    day_str, time_str = _session_bits(job)
    mode = "Custom text" if job_data.get('manual_msg') else "Auto-generated"
    # _topic_label, not a raw DB["topics"] lookup: the /classtopic default may
    # not be present in the topics table, and it carries its own cached name.
    topic_name = _topic_label(job_data.get('message_thread_id'))

    await query.edit_message_text(
        f"🔧 <b>WHAT TO EDIT?</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📖 <b>{html.escape(safe_text(job_data.get('subject'), 'Class'))}</b>\n"
        f"🎯 {_batch_chip(job_data.get('batch'))} "
        f"{html.escape(safe_text(job_data.get('batch'), '—'))}\n"
        f"🗓 {html.escape(day_str)} · ⏰ "
        f"{html.escape(_format_time_12h(job_data.get('time_display', '')))}\n"
        f"💬 Topic: <i>{html.escape(safe_text(topic_name, 'General'))}</i>\n"
        f"✍️ Message: <i>{mode}</i>\n\n"
        f"<i>Select what you want to change:</i> 👇",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.HTML
    )
    return EDIT_CHOOSE_FIELD

async def edit_nav(update, context):
    """
    Single router for every level of the edit browser.

    One function handles all three levels because back-navigation crosses
    conversation states in both directions; splitting it per state meant each
    handler had to know how to render its neighbours anyway.
    """
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "edit_cancel":
        await query.edit_message_text("❌ Edit cancelled.")
        return ConversationHandler.END

    class_jobs = _collect_class_jobs(context)
    if not class_jobs:
        await query.edit_message_text(
            "📭 <b>NO CLASSES LEFT</b>\n\n"
            "<i>Every scheduled session has fired or been deleted.</i>",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    # Level 1 — batches
    if data == "edit_back_batches":
        context.user_data['edit_sub_page'] = 0
        return await _edit_render_batches(query, context, class_jobs)

    if data.startswith("edit_b_"):
        context.user_data['edit_view'] = data[len("edit_b_"):]
        context.user_data['edit_sub_page'] = 0
        return await _edit_render_subjects(query, context, class_jobs)

    # Level 2 — subjects
    if data.startswith("edit_pgsub_"):
        try:
            context.user_data['edit_sub_page'] = int(data.rsplit("_", 1)[1])
        except ValueError:
            context.user_data['edit_sub_page'] = 0
        return await _edit_render_subjects(query, context, class_jobs)

    if data == "edit_back_subjects":
        return await _edit_render_subjects(query, context, class_jobs)

    if data.startswith("edit_s_"):
        try:
            context.user_data['edit_sub_idx'] = int(data.rsplit("_", 1)[1])
        except ValueError:
            return await _edit_render_subjects(query, context, class_jobs)
        context.user_data['edit_job_page'] = 0
        return await _edit_render_sessions(query, context, class_jobs)

    # Level 3 — sessions
    if data.startswith("edit_pgjob_"):
        try:
            context.user_data['edit_job_page'] = int(data.rsplit("_", 1)[1])
        except ValueError:
            context.user_data['edit_job_page'] = 0
        return await _edit_render_sessions(query, context, class_jobs)

    if data.startswith("edit_j_"):
        view = context.user_data.get('edit_view', 'all')
        groups = _subject_groups(_jobs_for_view(class_jobs, view))
        idx = context.user_data.get('edit_sub_idx')
        try:
            session_idx = int(data.rsplit("_", 1)[1])
        except ValueError:
            return await _edit_render_sessions(query, context, class_jobs)

        if idx is None or idx >= len(groups):
            return await _edit_render_subjects(query, context, class_jobs)

        sessions = groups[idx][2]
        if session_idx >= len(sessions):
            # List shifted under the admin (a class fired mid-navigation).
            return await _edit_render_sessions(query, context, class_jobs)

        return await _edit_render_fields(query, context, sessions[session_idx])

    # Unrecognised / stale callback from an older message.
    return await _edit_render_batches(query, context, class_jobs)

# Sentinel meaning "revert this class to auto-generated text". Stored as the
# pending edit value so the AI path can share the scope-selection step.
EDIT_MSG_AUTO = "__AUTO__"


async def _edit_expired(target, is_query=False):
    """Wizard state lost (timeout / restart / stale button)."""
    text = ("⌛ <b>THIS EDIT EXPIRED</b>\n\n"
            "<i>Open ✏️ Edit Class again to retry.</i>")
    try:
        if is_query:
            await target.edit_message_text(text, parse_mode=ParseMode.HTML)
        else:
            await target.reply_text(text, parse_mode=ParseMode.HTML)
    except Exception:
        pass
    return ConversationHandler.END


async def _edit_show_scope(context, send, field, new_val):
    """
    Render the 'apply to which classes?' step.

    `send` is an awaitable taking (text, reply_markup) so this works from both a
    plain message (edit_save) and a callback (the AI message path).
    """
    original_name = context.user_data.get('edit_job_name')
    if not original_name:
        return None

    jobs = context.job_queue.get_jobs_by_name(original_name)
    if not jobs:
        await send("❌ <b>CLASS NO LONGER SCHEDULED</b>\n\n"
                   "<i>It may have already fired or been deleted.</i>", None)
        return ConversationHandler.END

    job = jobs[0]
    d = safe_job_data(job)                      # job.data can be None
    subject = d.get('subject', 'Unknown')
    batch = d.get('batch', 'Unknown')
    try:
        day_name = job.next_t.strftime('%A')
    except Exception:
        day_name = "Unknown"

    # Count siblings. safe_job_data is essential here: job_queue.jobs() also
    # returns internal jobs (e.g. the cleanup job) created with data=None, and
    # calling .get() on those raised
    # "'NoneType' object has no attribute 'get'".
    same_subject, same_day = 0, 0
    for j in context.job_queue.jobs():
        jd = safe_job_data(j)
        if jd.get('subject') == subject and jd.get('batch') == batch:
            same_subject += 1
            try:
                if j.next_t and j.next_t.strftime('%A') == day_name:
                    same_day += 1
            except Exception:
                pass

    if field == "msg":
        shown = ("Auto-generated" if new_val == EDIT_MSG_AUTO
                 else f"Custom · {new_val[:24]}…" if len(new_val) > 24
                 else f"Custom · {new_val}")
    elif field == "endtime":
        shown = "None (Removed)" if str(new_val).lower() in ('none', 'skip', 'no', '-') else _format_single_time_12h(new_val)
    elif field == "time":
        shown = _format_single_time_12h(new_val)
    else:
        shown = new_val[:30]

    kb = [
        [InlineKeyboardButton("🎯 This class only", callback_data="scope_single")],
        [InlineKeyboardButton(f"📅 All {subject[:14]} on {day_name} ({same_day})",
                              callback_data="scope_day")],
        [InlineKeyboardButton(f"📚 All {subject[:18]} ({same_subject})",
                              callback_data="scope_subject")],
        [InlineKeyboardButton("🔙 Cancel", callback_data="scope_cancel")],
    ]

    await send(
        f"✅ <b>APPLY TO WHICH CLASSES?</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📖 Subject: <b>{html.escape(safe_text(subject, 'Unknown'))}</b>\n"
        f"🎯 Batch: <b>{html.escape(safe_text(batch, 'Unknown'))}</b>\n"
        f"🔧 Change: <b>{field.upper()}</b> → <code>{html.escape(shown)}</code>\n\n"
        f"<i>Select scope:</i> 👇",
        InlineKeyboardMarkup(kb)
    )
    return EDIT_SELECT_SCOPE


async def edit_choose_field(update, context):
    query = update.callback_query
    await query.answer()

    field = query.data.replace("field_", "")
    if field == "cancel":
        await query.edit_message_text("❌ Edit cancelled.")
        return ConversationHandler.END

    # Step back up into the session list rather than dead-ending the wizard.
    if field == "back":
        class_jobs = _collect_class_jobs(context)
        if not class_jobs:
            await query.edit_message_text(
                "📭 <b>NO CLASSES LEFT</b>", parse_mode=ParseMode.HTML)
            return ConversationHandler.END
        return await _edit_render_sessions(query, context, class_jobs)

    context.user_data['edit_field'] = field

    # Message editing first asks HOW, mirroring the scheduling wizard.
    if field == "msg":
        kb = [
            [InlineKeyboardButton("✨ AI / Auto-generated", callback_data="editmsg_ai")],
            [InlineKeyboardButton("✍️ Write it myself", callback_data="editmsg_manual")],
            [InlineKeyboardButton("🔙 Cancel", callback_data="editmsg_cancel")],
        ]
        await query.edit_message_text(
            "📝 <b>MESSAGE TYPE</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "✨ <b>AI / Auto-generated</b>\n"
            "<i>A fresh styled notification is built at send time — different "
            "layout and wording every class.</i>\n\n"
            "✍️ <b>Write it myself</b>\n"
            "<i>Your exact text is sent as-is.</i>",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )
        return EDIT_MSG_TYPE

    prompts = {
        "time": "⏰ <b>NEW START TIME</b>\n\n<i>Enter HH:MM (24-hour), e.g. <code>14:30</code></i>",
        "endtime": "⏰ <b>NEW END TIME</b>\n\n<i>Enter HH:MM (24-hour), e.g. <code>15:30</code>\nOr type <code>None</code> to remove end time.</i>",
        "date": "📅 <b>NEW DATE</b>\n\n<i>Enter YYYY-MM-DD, e.g. <code>2026-08-20</code></i>",
        "link": "🔗 <b>NEW LINK</b>\n\n<i>Paste the new meeting link (or type <code>None</code>)</i>",
        "topic": ("💬 <b>NEW TOPIC ID</b>\n\n"
                  "<i>Enter a Topic ID, or</i> <code>0</code> <i>for General.</i>\n"
                  "<i>See</i> /topics <i>for the list.</i>"),
    }
    await query.edit_message_text(
        prompts.get(field, "❓ Enter new value:"),
        parse_mode=ParseMode.HTML
    )
    return EDIT_NEW_VALUE


async def edit_msg_type(update, context):
    """AI vs manual for the message-edit path."""
    query = update.callback_query
    await query.answer()
    choice = query.data.replace("editmsg_", "")

    if choice == "cancel":
        await query.edit_message_text("❌ Edit cancelled.")
        return ConversationHandler.END

    if choice == "manual":
        await query.edit_message_text(
            "✍️ <b>SEND THE NEW MESSAGE</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Telegram HTML is supported: </i>"
            "<code>&lt;b&gt;bold&lt;/b&gt;</code>, "
            "<code>&lt;i&gt;italic&lt;/i&gt;</code>, "
            "<code>&lt;a href=\"…\"&gt;link&lt;/a&gt;</code>\n\n"
            "<i>Or /cancel to abort.</i>",
            parse_mode=ParseMode.HTML
        )
        return EDIT_NEW_VALUE

    # AI path needs no text input — go straight to scope selection.
    context.user_data['edit_new_value'] = EDIT_MSG_AUTO

    async def send(text, markup):
        await query.edit_message_text(text, reply_markup=markup,
                                      parse_mode=ParseMode.HTML)

    result = await _edit_show_scope(context, send, "msg", EDIT_MSG_AUTO)
    if result is None:
        return await _edit_expired(query, is_query=True)
    return result

async def edit_save(update, context):
    """Validate the typed value, then show scope selection."""
    new_val = safe_text(update.message.text, "").strip()
    field = context.user_data.get('edit_field')

    if not field or not context.user_data.get('edit_job_name'):
        return await _edit_expired(update.message)

    if field == "time":
        try:
            h, m = map(int, new_val.split(":"))
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError
            new_val = f"{h:02d}:{m:02d}"
        except Exception:
            await update.message.reply_text(
                "❌ <b>INVALID TIME</b>\n<i>Use HH:MM between 00:00 and 23:59.</i>",
                parse_mode=ParseMode.HTML)
            return EDIT_NEW_VALUE

    elif field == "endtime":
        if new_val.lower() in ('none', 'skip', 'no', '-'):
            new_val = "None"
        else:
            try:
                parts = new_val.split(":")
                if len(parts) != 2:
                    raise ValueError
                h, m = int(parts[0]), int(parts[1])
                if not (0 <= h <= 23 and 0 <= m <= 59):
                    raise ValueError
                new_val = f"{h:02d}:{m:02d}"
            except Exception:
                await update.message.reply_text(
                    "❌ <b>INVALID END TIME</b>\n<i>Use HH:MM (e.g. 15:30) or type None to remove.</i>",
                    parse_mode=ParseMode.HTML)
                return EDIT_NEW_VALUE

    elif field == "date":
        try:
            datetime.strptime(new_val, "%Y-%m-%d")
        except Exception:
            await update.message.reply_text(
                "❌ <b>INVALID DATE</b>\n<i>Use YYYY-MM-DD, e.g. 2026-08-20.</i>",
                parse_mode=ParseMode.HTML)
            return EDIT_NEW_VALUE

    elif field == "topic":
        if not new_val.isdigit():
            await update.message.reply_text(
                "❌ <b>INVALID TOPIC ID</b>\n<i>Numbers only (0 for General).</i>",
                parse_mode=ParseMode.HTML)
            return EDIT_NEW_VALUE

    elif field == "msg":
        if not new_val:
            await update.message.reply_text(
                "❌ <b>MESSAGE CANNOT BE EMPTY</b>\n<i>Send some text or /cancel.</i>",
                parse_mode=ParseMode.HTML)
            return EDIT_NEW_VALUE
        # Reject unsupported tags now rather than failing at send time.
        valid, err = validate_html(new_val)
        if not valid:
            await update.message.reply_text(
                f"❌ <b>UNSUPPORTED HTML</b>\n\n{err}",
                parse_mode=ParseMode.HTML)
            return EDIT_NEW_VALUE

    elif field == "link":
        if not new_val:
            await update.message.reply_text(
                "❌ <b>LINK CANNOT BE EMPTY</b>", parse_mode=ParseMode.HTML)
            return EDIT_NEW_VALUE

    context.user_data['edit_new_value'] = new_val

    async def send(text, markup):
        await update.message.reply_text(text, reply_markup=markup,
                                       parse_mode=ParseMode.HTML)

    result = await _edit_show_scope(context, send, field, new_val)
    if result is None:
        return await _edit_expired(update.message)
    return result

async def edit_scope_handler(update, context):
    """Handle scope selection and apply edits"""
    query = update.callback_query
    await query.answer()
    scope = query.data.replace("scope_", "")
    
    if scope == "cancel":
        await query.edit_message_text("❌ Edit cancelled.")
        return ConversationHandler.END
    
    # Get stored edit data
    field = context.user_data.get('edit_field')
    new_val = context.user_data.get('edit_new_value')
    original_name = context.user_data.get('edit_job_name')
    if not field or new_val is None or not original_name:
        return await _edit_expired(query, is_query=True)

    # Get original job for reference
    jobs = context.job_queue.get_jobs_by_name(original_name)
    if not jobs:
        await query.edit_message_text(
            "❌ <b>CLASS NO LONGER SCHEDULED</b>\n\n"
            "<i>It may have already fired or been deleted.</i>",
            parse_mode=ParseMode.HTML)
        return ConversationHandler.END
    
    ref_job = jobs[0]
    ref_data = safe_job_data(ref_job)
    subject = ref_data.get('subject')
    batch = ref_data.get('batch')
    try:
        ref_day = ref_job.next_t.strftime('%A')
    except:
        ref_day = "Unknown"
    
    # Find jobs to edit based on scope
    all_jobs = context.job_queue.jobs()
    jobs_to_edit = []
    
    if scope == "single":
        jobs_to_edit = [ref_job]
    elif scope == "day":
        for j in all_jobs:
            d = safe_job_data(j)
            j_day = j.next_t.strftime('%A') if j.next_t else "Unknown"
            if d.get('subject') == subject and d.get('batch') == batch and j_day == ref_day:
                jobs_to_edit.append(j)
    elif scope == "subject":
        for j in all_jobs:
            d = safe_job_data(j)
            if d.get('subject') == subject and d.get('batch') == batch:
                jobs_to_edit.append(j)
    
    if not jobs_to_edit:
        await query.edit_message_text("❌ <b>NO MATCHING JOBS!</b>", parse_mode=ParseMode.HTML)
        return ConversationHandler.END
    
    # Apply edits to all matching jobs
    edited_count = 0
    failures = []
    for job in jobs_to_edit:
        try:
            data = dict(safe_job_data(job))      # never .copy() on a None
            next_t = job.next_t
            chat_id = job.chat_id
            old_name = job.name
            # Preserve the original callback. Hard-coding send_alert_job here
            # silently converted scheduled custom messages into class alerts.
            callback = getattr(job, "callback", None) or send_alert_job

            if field == "time":
                h, m = map(int, new_val.split(":"))
                next_t = next_t.replace(hour=h, minute=m)
                old_td = str(data.get('time_display', '')).strip()
                if " - " in old_td:
                    end_part = old_td.split(" - ", 1)[1]
                    data['time_display'] = f"{new_val} - {end_part}"
                elif "-" in old_td and len(old_td.split("-")) == 2:
                    end_part = old_td.split("-", 1)[1].strip()
                    data['time_display'] = f"{new_val} - {end_part}"
                else:
                    data['time_display'] = new_val
            elif field == "endtime":
                old_td = str(data.get('time_display', '')).strip()
                if " - " in old_td:
                    start_part = old_td.split(" - ", 1)[0].strip()
                elif "-" in old_td and len(old_td.split("-")) == 2:
                    start_part = old_td.split("-", 1)[0].strip()
                else:
                    start_part = old_td or next_t.strftime('%H:%M')
                
                if new_val.lower() in ('none', 'skip', 'no', '-'):
                    data['time_display'] = start_part
                else:
                    data['time_display'] = f"{start_part} - {new_val}"
            elif field == "date":
                d = datetime.strptime(new_val, "%Y-%m-%d")
                next_t = next_t.replace(year=d.year, month=d.month, day=d.day)
            elif field == "link":
                data['link'] = new_val
            elif field == "msg":
                # msg_type is what send_alert_job branches on. Setting only
                # manual_msg left msg_type == "AI", so the custom text was
                # generated over and silently ignored.
                if new_val == EDIT_MSG_AUTO:
                    data['msg_type'] = "AI"
                    data['manual_msg'] = None
                else:
                    data['msg_type'] = "MANUAL"
                    data['manual_msg'] = new_val
            elif field == "topic":
                # '0' is an explicit "General" choice, which must survive the
                # /classtopic default rather than being treated as "inherit".
                data['message_thread_id'] = GENERAL_TOPIC if new_val == "0" else int(new_val)

            # Don't reschedule into the past — it would fire instantly.
            if next_t <= datetime.now(IST):
                failures.append(f"{old_name}: new time is in the past")
                continue

            job.schedule_removal()
            # job_tag(), not a bare replace(' ', ''): "CSDA & AICS" would keep
            # its '&' and the attendance button built from this ID would be
            # rejected as an unknown class.
            batch_tag = job_tag(safe_text(data.get('batch'), 'CLASS'), 'CLASS')
            new_job_id = f"{batch_tag}_{int(time.time())}_{edited_count}"
            context.job_queue.run_once(callback, next_t, chat_id=chat_id,
                                       name=new_job_id, data=data)

            remove_job_from_db(old_name)
            add_job_to_db(new_job_id, next_t.timestamp(), chat_id, data)
            edited_count += 1

        except Exception as e:
            logger.error(f"Failed to edit job {getattr(job,'name','?')}: {e}",
                         exc_info=True)
            failures.append(f"{getattr(job,'name','?')}: {e}")
            continue

    if field == "msg":
        shown = ("Auto-generated (fresh design each time)"
                 if new_val == EDIT_MSG_AUTO else f"Custom text ({len(new_val)} chars)")
    elif field == "endtime":
        shown = "None (Removed)" if str(new_val).lower() in ('none', 'skip', 'no', '-') else _format_single_time_12h(new_val)
    elif field == "time":
        shown = _format_single_time_12h(new_val)
    else:
        shown = new_val[:30]

    scope_text = {"single": "this class",
                  "day": f"all {subject} on {ref_day}",
                  "subject": f"all {subject}"}

    if edited_count:
        body = (f"✅ <b>EDIT APPLIED</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📊 <b>{edited_count}</b> class(es) updated\n"
                f"🔧 <b>{field.upper()}</b> → <code>{html.escape(shown)}</code>\n"
                f"📌 Scope: <i>{html.escape(scope_text.get(scope, scope))}</i>")
    else:
        body = ("⚠️ <b>NOTHING WAS UPDATED</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "<i>No class could be rescheduled.</i>")

    if failures:
        body += f"\n\n⚠️ <i>{len(failures)} skipped:</i>\n"
        body += "\n".join(f"• <code>{html.escape(f[:60])}</code>"
                          for f in failures[:3])

    await query.edit_message_text(body, parse_mode=ParseMode.HTML)
    return ConversationHandler.END

# ==============================================================================
# 📨 11. JOB EXECUTION
# ==============================================================================

def _format_time_12h(time_str):
    """Convert 'HH:MM' or 'HH:MM - HH:MM' (24h) to readable '2:30 PM' or '2:30 PM - 3:30 PM'."""
    try:
        s = str(time_str).strip()
        if not s:
            return ""
        for sep in (" - ", " to ", " – ", "-"):
            if sep in s:
                parts = s.split(sep, 1)
                t1 = _format_single_time_12h(parts[0])
                t2 = _format_single_time_12h(parts[1])
                return f"{t1} - {t2}"
        return _format_single_time_12h(s)
    except Exception:
        return str(time_str)


def _format_single_time_12h(time_str):
    """Convert a single 'HH:MM' (24h) to '2:30 PM'."""
    try:
        s = str(time_str).strip()
        if "AM" in s.upper() or "PM" in s.upper():
            return s
        h, m = s.split(":")[:2]
        h, m = int(h), int(m[:2])
        suffix = "AM" if h < 12 else "PM"
        h12 = h % 12 or 12
        return f"{h12}:{m:02d} {suffix}"
    except Exception:
        return str(time_str).strip()


# ------------------------------------------------------------------------------
# 🖼 ASCII FRAMES
# ------------------------------------------------------------------------------
# A framed card can only line up if two things are true on the reader's device:
#
#   1. Every glyph occupies exactly one monospace cell. That rules out the
#      box-drawing characters (┌ ╔ ▛ ╌) the earlier version used: most mobile
#      monospace fonts do not carry them, the client silently substitutes a
#      glyph from another font, and the substitute brings its own advance width.
#      That is what tore the right edge. Only +, -, =, |, # and * are safe —
#      they are ASCII, so every font has them.
#   2. The text is laid out in a monospace font at all, which on Telegram means
#      <pre>. The trade-off is fixed by the Bot API: no entity may nest inside
#      <pre>, so nothing in the frame can be bold, linked or coloured, and emoji
#      are double-width. All of that lives OUTSIDE the frame.
#
# Interiors are folded to ASCII so len() IS the rendered width — no width table
# to get wrong.

# style -> (corner, horizontal, vertical)
ASCII_FRAMES = {
    "eq":    ("+", "=", "|"),
    "dash":  ("+", "-", "|"),
    "hash":  ("#", "=", "#"),
    "star":  ("*", "-", "*"),
    "dot":   ("+", ".", ":"),
}

# Punctuation that would otherwise be dropped by the ASCII fold.
_ASCII_FOLD = {
    "—": "-", "–": "-", "―": "-", "·": "-", "•": "*",
    "’": "'", "‘": "'", "“": '"', "”": '"', "…": "...",
    "×": "x", "→": "->", "&": "&",
}


def _ascii_fold(text):
    """
    Reduce text to printable ASCII so one character equals one monospace cell.

    Accents are stripped via NFKD (José -> Jose), known punctuation is mapped to
    its ASCII twin, and anything left over (emoji, CJK) is dropped rather than
    guessed at — a double-width glyph inside the frame would push that row one
    cell wider than the rest.
    """
    import unicodedata
    out = []
    for ch in unicodedata.normalize("NFKD", str(text)):
        if unicodedata.combining(ch):
            continue
        if ch in _ASCII_FOLD:
            out.append(_ASCII_FOLD[ch])
        elif 32 <= ord(ch) < 127:
            out.append(ch)
        elif ch in ("\t",):
            out.append(" ")
    return "".join(out)


def _frame_rows(pairs, width):
    """'Label  value' rows, label column padded, truncated to the frame width."""
    labels = [_ascii_fold(k) for k, _ in pairs]
    label_w = max((len(x) for x in labels), default=0)
    rows = []
    for label, (_, val) in zip(labels, pairs):
        row = f"{label.ljust(label_w)}  {_ascii_fold(val)}"
        rows.append(row[:width] if len(row) > width else row)
    return rows


def _ascii_frame(rows, style="eq", title=None, width=30, pad=1):
    """
    Fixed-width ASCII card, wrapped in <pre> so it renders monospace.

    Width is fixed rather than fitted to the content: a card that changes width
    every class reads as unstable, and a fixed 30 columns still fits the
    narrowest phone without the <pre> block scrolling sideways.
    """
    corner, h, v = ASCII_FRAMES.get(style, ASCII_FRAMES["eq"])
    inner = width - pad * 2

    body = []
    for r in rows:
        r = _ascii_fold(r)
        if not r:
            body.append("")
            continue
        # Only re-wrap rows that genuinely overflow. Wrapping splits on
        # whitespace and rejoins on single spaces, which would collapse the
        # label column _frame_rows() just built ("Batch  CSDA" -> "Batch CSDA").
        if len(r) <= inner:
            body.append(r)
            continue
        line = ""
        for word in r.split():
            if not line:
                line = word
            elif len(line) + 1 + len(word) <= inner:
                line += " " + word
            else:
                body.append(line)
                line = word
            while len(line) > inner:
                body.append(line[:inner])
                line = line[inner:]
        body.append(line)

    if title:
        label = _ascii_fold(title).strip()
        room = inner - 4
        if len(label) > room:
            # Cut at a word boundary. A hard slice produces borders like
            # "+-- CYBER SECURITY FOUNDATIO --+", which reads as a defect.
            cut = label[:room]
            label = cut[:cut.rindex(" ")] if " " in cut else cut
        t = f" {label} "
        left = 2
        right = max(1, width - left - len(t))
        top = f"{corner}{h * left}{t}{h * right}{corner}"
    else:
        top = f"{corner}{h * width}{corner}"

    out = [top]
    for r in body:
        out.append(f"{v}{' ' * pad}{r.ljust(inner)}{' ' * pad}{v}")
    out.append(f"{corner}{h * width}{corner}")

    # Escape LAST: padding is computed on the real characters, so '&' counts as
    # one cell here and Telegram unescapes '&amp;' back to one cell on display.
    return "<pre>" + html.escape("\n".join(out)) + "</pre>"


def _split_subject(subject):
    """
    Split 'CDA/ACS 205 : Machine Learning Techniques' into
    ('CDA/ACS 205', 'Machine Learning Techniques').

    Visual hierarchy is the single biggest driver of "wow" — a big bold
    subject name with a small course code above it reads like a designed
    poster. One long undifferentiated line reads like a database dump.
    """
    s = str(subject or "Class").strip()
    for sep in (":", "-", "—", "|"):
        if sep in s:
            left, right = s.split(sep, 1)
            left, right = left.strip(), right.strip()
            # Course code is the short side that contains a digit
            if left and right and len(left) <= 18 and any(c.isdigit() for c in left):
                return left, right
            break
    return "", s


def _minutes_until(time_str, now):
    """Minutes from `now` until today's HH:MM. None if unparseable."""
    try:
        h, m = str(time_str).strip().split(":")[:2]
        target = now.replace(hour=int(h), minute=int(m[:2]), second=0, microsecond=0)
        return int(round((target - now).total_seconds() / 60.0))
    except Exception:
        return None


def _generate_class_notification(batch, subject, time_str, link=None):
    """
    Premium class notification generator.

    ── Why this looks good (design rationale) ──────────────────────────
    Telegram renders messages in a PROPORTIONAL font, and only honours a
    fixed set of entities. That drives every decision here:

      1. Framed cards are ASCII-only and live in <pre>. See ASCII_FRAMES
         for the full reasoning: box-drawing glyphs (┌ ╔ ▛) are missing
         from most mobile monospace fonts, the client substitutes them,
         and the substitute's advance width is what tears the right edge.
         +, -, = and | exist everywhere, so they hold. Everything
         formatted — bold, links, emoji — sits outside the frame, because
         the Bot API forbids nesting entities inside <pre>.
      2. The unframed designs get their structure from entities the client
         lays out itself: <blockquote> (indent + coloured left bar), <b>
         for hierarchy, <code> for tinted values. No padding characters,
         and they adapt to any screen width.
      3. No multi-space alignment outside <pre>. Runs of spaces in a
         proportional font do not form a column, they just look uneven.
      4. Hierarchy beats decoration. Big bold NAME, small course code,
         then data. The eye lands in the right place instantly.
      5. Urgency drives engagement more than ornament. A live countdown
         ("starts in 10 minutes") is the actual hook.

    ── Variety ─────────────────────────────────────────────────────────
    10 design systems (3 framed, 7 native) × 3 title treatments × 5 frame
    styles × 10 rules × 10 openers × 12 flavours/subject × 10 CTAs
    × 8 link styles, all tracked by per-category deques → no repeat for
    weeks of daily classes.
    """
    import random
    from collections import deque

    # ─── ANTI-REPETITION ───
    if not hasattr(_generate_class_notification, '_history'):
        _generate_class_notification._history = {
            # 6 of 10: blocks any near-term repeat while still leaving a real
            # choice each time. A maxlen of 9 would leave exactly one candidate
            # and turn the rotation into a fixed cycle.
            'design': deque(maxlen=6),
            'title': deque(maxlen=2),
            'rule': deque(maxlen=8),
            'opener': deque(maxlen=12),
            'flavour': deque(maxlen=12),
            'cta': deque(maxlen=8),
            'link': deque(maxlen=6),
            'urgency': deque(maxlen=5),
            'frame': deque(maxlen=3),
        }
    history = _generate_class_notification._history

    def pick(pool, cat):
        avail = [x for x in pool if x not in history[cat]]
        if not avail:
            history[cat].clear()
            avail = pool
        c = random.choice(avail)
        history[cat].append(c)
        return c

    def pick_named(options, cat):
        """
        pick() for pools of functions, rotated on the NAME.

        The design and title builders are closures redefined on every call, so
        the deque only ever held stale function objects — `x not in history`
        was always True and the anti-repetition silently degraded to a plain
        random.choice. That is why the same layout kept reappearing, sometimes
        twice in a row. Names are stable across calls, so the rotation holds.
        """
        return options[pick(list(options), cat)]

    now = datetime.now(IST)
    day_name = now.strftime('%A')
    day_short = now.strftime('%a')
    date_full = now.strftime('%d %B %Y')
    date_short = now.strftime('%d %b')
    time_12h = _format_time_12h(time_str)
    hour = now.hour

    code, name = _split_subject(subject)
    code_e = html.escape(code)
    name_e = html.escape(name)
    # Upper-case the RAW text, then escape. Doing it the other way round
    # (name_e.upper()) turns the escape sequences themselves into &AMP; and
    # &LT;, and Telegram only recognises the lowercase forms &amp; &lt; &gt;
    # &quot; — so any subject containing '&' rendered as literal '&AMP;' or
    # failed the parse outright and fell back to unformatted plain text.
    name_upper_e = html.escape(str(name).upper())
    batch_e = html.escape(str(batch or "—"))
    sub_lower = str(subject or "").lower()

    # ─── SUBJECT IDENTITY (icon + accent) ───
    sub_icon, accent = "📘", "◆"
    identity = [
        (("statistic", "cda 201", "cda/acs 201"), "📊", "◆"),
        (("algorithm", "203"),                    "🧩", "◈"),
        (("machine", "learning", "205"),          "🤖", "◆"),
        (("financial", "economic", "207"),        "💹", "◇"),
        (("cyber", "security", "foundation"),     "🛡", "◈"),
    ]
    for keys, ic, ac in identity:
        if any(k in sub_lower for k in keys):
            sub_icon, accent = ic, ac
            break

    # ─── COLOUR ACCENTS ───
    # Telegram allows NO colour tag (no <font>, no CSS) — the permitted set is
    # b/i/u/s, a, code, pre, blockquote, spoiler, tg-emoji. So real colour comes
    # from only three places, all used here:
    #   1. emoji glyphs      — genuinely coloured pixels
    #   2. <code>            — clients render it tinted (usually orange/red) on
    #                          a shaded background
    #   3. <a> links         — the one text colour Telegram guarantees (blue)
    batch_chip = ("🟦" if batch_e.strip() == "CSDA"
                  else "🟧" if batch_e.strip() == "AICS"
                  else "🟪")

    # ─── LIVE URGENCY (the real engagement hook) ───
    mins = _minutes_until(time_str, now)
    urgency = ""
    status_chip = "🔵"
    if mins is not None:
        if mins <= 2:
            status_chip = "🔴"
        elif mins <= 20:
            status_chip = "🟠"
        else:
            status_chip = "🟢"
    if mins is not None:
        if mins <= 0:
            urgency = pick([
                "🔴 <b>LIVE NOW</b>",
                "🔴 <b>Starting right now</b>",
                "🔴 <b>Class is live</b>",
            ], 'urgency')
        elif mins <= 2:
            urgency = f"🔴 <b>Starting now</b>"
        elif mins <= 20:
            urgency = pick([
                f"⏳  Starts in <b>{mins} minutes</b>",
                f"⏳  <b>{mins} minutes</b> to go",
                f"⏳  Live in <b>{mins} minutes</b>",
                f"⏳  <b>T-{mins} min</b>",
                f"⏳  Doors open in <b>{mins} min</b>",
            ], 'urgency')
        elif mins <= 90:
            urgency = f"⏳  Starts in <b>{mins} minutes</b>"
        # Beyond 90 min a countdown adds nothing the data block doesn't
        # already say, so leave it off rather than duplicate the time.

    # ─── HORIZONTAL RULES (full-width, nothing to align against) ───
    rules = [
        "━━━━━━━━━━━━━━━━━━━━━━",
        "──────────────────────",
        "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬",
        "═══════════════════════",
        "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯",
        "▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔",
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈",
        "･･･････････････････････",
        f"{accent}━━━━━━━━━━━━━━━━━━━━{accent}",
        f"{accent} ─────────────────── {accent}",
    ]
    rule = pick(rules, 'rule')

    # ─── TIME-AWARE OPENERS ───
    if hour < 12:
        openers = [
            "Good morning.", "Morning, everyone.", f"Happy {day_name}.",
            "Rise and shine.", "Morning roll call.", "New day, new session.",
            "Coffee first, then class.", "Early start today.",
            f"{day_name} morning.", "Let's begin.",
        ]
    elif hour < 17:
        openers = [
            "Afternoon session.", "Good afternoon.", "Post-lunch session.",
            f"{day_name} afternoon.", "Midday session.", "Back to it.",
            "Afternoon roll call.", "Next up.", "Half the day left.",
            "Let's keep going.",
        ]
    else:
        openers = [
            "Evening session.", "Good evening.", "Last one for today.",
            f"{day_name} evening.", "Evening roll call.", "Winding down.",
            "One more to go.", "Finishing strong.", "Late session.",
            "Evening slot.",
        ]
    opener = pick(openers, 'opener')

    # ─── SUBJECT-AWARE FLAVOUR ───
    if any(k in sub_lower for k in ("statistic", "cda 201", "cda/acs 201")):
        flavours = [
            "Probability isn't going to learn itself.",
            "Distributions, variance, the good stuff.",
            "Numbers tell stories. Let's read them.",
            "Bayes would want you to show up.",
            "Correlation is not causation. Let's discuss.",
            "Standard deviation waits for no one.",
            "Time to make the data make sense.",
            "Hypothesis testing today.",
            "Mean, median, mode. The holy trinity.",
            "p &lt; 0.05. Attendance is significant.",
            "Confidence intervals are calling.",
            "Sampling the good knowledge today.",
        ]
    elif any(k in sub_lower for k in ("algorithm", "203")):
        flavours = [
            "Sorting out complexity. Literally.",
            "Big-O notation enters the chat.",
            "Greedy or dynamic? Find out today.",
            "Graphs, trees, paths. Pick your weapon.",
            "Divide and conquer.",
            "Pseudocode time. Bring your brain.",
            "Optimal solutions don't find themselves.",
            "Algorithmic thinking: enabled.",
            "Time complexity won't optimise itself.",
            "BFS your way to class.",
            "No shortcut has better complexity than showing up.",
            "Today's problem set won't solve itself.",
        ]
    elif any(k in sub_lower for k in ("machine", "learning", "205")):
        flavours = [
            "Models don't train themselves.",
            "Gradient descent, incoming.",
            "Features, labels, accuracy. The usual trio.",
            "Let's teach machines something.",
            "Overfitting? Not today.",
            "Neural connections start with attendance.",
            "Supervised or unsupervised. One way to find out.",
            "The loss from missing class is high.",
            "Your learning rate depends on this.",
            "Epoch one of today's session.",
            "Bias-variance tradeoff: skip vs attend.",
            "Training data: your notes.",
        ]
    elif any(k in sub_lower for k in ("financial", "economic", "207")):
        flavours = [
            "Markets move. So should we.",
            "Risk, return, and real talk.",
            "More than GDP and inflation.",
            "Time value of money, and of your time.",
            "Portfolio theory hits different live.",
            "Supply, demand, and your attendance.",
            "Compound interest on knowledge starts now.",
            "Bull or bear, class is always on.",
            "Attendance: a blue-chip investment.",
            "Opportunity cost of skipping is high.",
            "Diversify your skills.",
            "Today's yield: knowledge.",
        ]
    elif any(k in sub_lower for k in ("cyber", "security", "foundation")):
        flavours = [
            "Firewalls won't cover a missed class.",
            "Encryption starts with understanding.",
            "Threats are real. So is this session.",
            "The CIA triad. Not that agency.",
            "Defence in depth starts here.",
            "Patch your knowledge gaps.",
            "Social engineering won't fool the attendance bot.",
            "Zero-day on ignorance. Fix it now.",
            "Authentication required: your presence.",
            "Access granted to those who show up.",
            "Brute force won't crack this syllabus.",
            "Your knowledge base needs an update.",
        ]
    else:
        flavours = [
            "One step closer to the goal.",
            "Today's effort, tomorrow's edge.",
            "Show up, take notes, thank yourself later.",
            "Small consistency, big results.",
            "Your future self is watching.",
            "Another session, another win.",
            "The syllabus waits for no one.",
            "Knowledge compounds.",
            "Worth the twenty minutes.",
            "Let's get into it.",
            "Notes ready?",
            "This one matters.",
        ]
    flavour = pick(flavours, 'flavour')

    # ─── LINK ───
    link_line = ""
    has_link = link and str(link).strip().lower() not in ("none", "check group", "")
    if has_link:
        href = html.escape(str(link), quote=True)
        link_line = pick([
            f"▶️ <a href=\"{href}\"><b>JOIN CLASS</b></a>",
            f"🔗 <a href=\"{href}\"><b>Join the class</b></a>",
            f"⚡ <a href=\"{href}\"><b>Join now</b></a>",
            f"🎯 <a href=\"{href}\"><b>Enter class</b></a>",
            f"▶️ <a href=\"{href}\"><b>Open session</b></a>",
            f"🔗 <a href=\"{href}\"><b>Tap to join</b></a>",
            f"🚀 <a href=\"{href}\"><b>Launch class</b></a>",
            f"↗️ <a href=\"{href}\"><b>Go to class</b></a>",
        ], 'link')
    elif str(link).strip().lower() == "check group":
        link_line = "🔗 <i>Link will be shared in the group.</i>"

    # ─── CTA ───
    cta = pick([
        "👇 Mark your attendance",
        "👇 Tap below to check in",
        "👇 Confirm you're here",
        "👇 One tap to mark present",
        "👇 Attendance below",
        "👇 Check in below",
        "👇 Register your attendance",
        "👇 Let us know you're in",
        "👇 Sign in below",
        "👇 Mark yourself present",
    ], 'cta')

    # ─── TITLE TREATMENTS (visual hierarchy) ───
    def title_hero():
        """Icon + big name, course code underneath. Maximum impact."""
        out = f"{sub_icon} <b>{name_upper_e}</b>"
        if code_e:
            out += f"\n<i>{code_e}</i>"
        return out

    def title_stacked():
        """Course code first as a label, then the name."""
        out = ""
        if code_e:
            out += f"<code>{code_e}</code>\n"
        out += f"{sub_icon} <b>{name_e}</b>"
        return out

    def title_inline():
        """Single strong line."""
        label = f"{code_e} · {name_e}" if code_e else name_e
        return f"{sub_icon} <b>{label}</b>"

    title = pick_named({
        'hero': title_hero,
        'stacked': title_stacked,
        'inline': title_inline,
    }, 'title')

    # ─── DATA BLOCKS ───
    # Every row is one label + one value on its own line. No attempt is made to
    # line values up into a column: Telegram's message font is proportional, so
    # runs of spaces produce a ragged edge rather than a grid. Structure comes
    # from <blockquote>, <b> and <code> instead, which the client renders
    # natively at whatever width the screen happens to be.
    def data_quote():
        """Native quote block — indent plus a coloured left bar, zero characters spent."""
        return (
            "<blockquote>"
            f"🕒 <b>{time_12h}</b>\n"
            f"📅 {day_name}, {date_full}\n"
            f"🎓 {batch_e}"
            "</blockquote>"
        )

    def data_inline():
        """Two tight lines, for the scannable alert layout."""
        return (
            f"🕒 <b>{time_12h}</b> · 📅 {day_short}, {date_short}\n"
            f"🎓 {batch_e}"
        )

    def data_rows():
        """Bold labels, plain values. Reads like a document, wraps cleanly."""
        return (
            f"<b>Time</b> · {time_12h}\n"
            f"<b>Date</b> · {day_name}, {date_short}\n"
            f"<b>Batch</b> · {batch_e}"
        )

    def data_chips():
        """Coloured emoji chips + <code> values — the most colour Telegram allows."""
        # batch_e is already HTML-escaped; unescaping here would emit a raw '&'
        # from "CSDA & AICS" and break the parser.
        return (
            f"⏰ <code>{time_12h}</code>\n"
            f"📅 <code>{day_name}, {date_short}</code>\n"
            f"{batch_chip} <code>{batch_e}</code>"
        )

    def data_quote_chips():
        """Quote block with tinted values — the most 'designed' of the four."""
        return (
            "<blockquote>"
            f"⏰ <code>{time_12h}</code>\n"
            f"📅 <code>{day_name}, {date_short}</code>\n"
            f"{batch_chip} <code>{batch_e}</code>"
            "</blockquote>"
        )

    # ── Framed cards ──
    # Plain text only inside: no emoji, no <b>, no links. Those go around it.
    _frame_pairs = [("Time", time_12h),
                    ("Date", f"{day_short}, {date_short}"),
                    ("Batch", batch)]

    def frame_plain(style):
        return _ascii_frame(_frame_rows(_frame_pairs, 28), style=style)

    def frame_titled(style):
        """Course code sits in the top border: +== CDA 201 ==========+"""
        return _ascii_frame(_frame_rows(_frame_pairs, 28), style=style,
                            title=(code or name).upper())

    def frame_full(style):
        """Self-contained card — subject inside the frame with the data."""
        rows = [name]
        if code:
            rows.append(code)
        rows.append("")
        rows.extend(_frame_rows(_frame_pairs, 28))
        return _ascii_frame(rows, style=style)

    def data_card():
        """Self-contained quote block: subject inside the card with the data."""
        head = f"{sub_icon} <b>{name_e}</b>"
        if code_e:
            head += f"\n<i>{code_e}</i>"
        return (
            "<blockquote>"
            f"{head}\n\n"
            f"🕒 <b>{time_12h}</b>\n"
            f"📅 {day_name}, {date_short}\n"
            f"🎓 {batch_e}"
            "</blockquote>"
        )

    # ══════════════════════════════════════════════════════════════════
    #  DESIGN SYSTEMS — genuinely different visual identities
    # ══════════════════════════════════════════════════════════════════

    def design_hero():
        """Poster style. Rules top and bottom, big title, countdown, quoted data."""
        parts = [rule, "", title()]
        if urgency:
            parts += ["", urgency]
        parts += ["", data_quote_chips()]
        if link_line:
            parts += ["", link_line]
        parts += ["", f"<i>{flavour}</i>", rule, "", cta]
        return "\n".join(parts)

    def design_editorial():
        """
        Magazine style. Small opener, title, native blockquote for data,
        flavour as a pull quote. Airy and premium.
        """
        parts = [f"<i>{opener}</i>", "", title()]
        if urgency:
            parts += ["", urgency]
        parts += ["", data_quote()]
        if link_line:
            parts += ["", link_line]
        parts += ["", f"<i>{flavour}</i>", "", cta]
        return "\n".join(parts)

    def design_ticker():
        """
        Alert style. Countdown leads, tight data, urgent feel.
        Shortest of the four — scannable in one glance.
        """
        parts = []
        if urgency:
            parts += [urgency, ""]
        parts += [title(), "", data_inline()]
        if link_line:
            parts += ["", link_line]
        parts += ["", f"<i>{flavour}</i>", "", cta]
        return "\n".join(parts)

    def design_brief():
        """
        Structured brief. Rule-separated sections, labelled rows.
        Reads like a well-formatted document.
        """
        parts = [title(), rule, ""]
        if urgency:
            parts += [urgency, ""]
        parts += [data_rows()]
        if link_line:
            parts += ["", link_line]
        parts += ["", f"<blockquote>{flavour}</blockquote>", "", cta]
        return "\n".join(parts)

    def design_chips():
        """Maximum colour: coloured batch chip and tinted <code> values.

        No status_chip prefix here — the title treatments already lead with a
        subject icon, and two emoji back to back read as clutter. The urgency
        line below carries the 🔴/⏳ signal instead.
        """
        parts = [title()]
        if urgency:
            parts += ["", urgency]
        parts += ["", data_chips()]
        if link_line:
            parts += ["", link_line]
        parts += ["", f"<i>{flavour}</i>", "", cta]
        return "\n".join(parts)

    def design_card():
        """Everything inside one quote block — the most contained layout."""
        parts = []
        if urgency:
            parts += [urgency, ""]
        parts += [data_card()]
        if link_line:
            parts += ["", link_line]
        parts += ["", f"<i>{flavour}</i>", "", cta]
        return "\n".join(parts)

    def design_framed():
        """ASCII card with the title, countdown and link outside the frame."""
        style = pick(list(ASCII_FRAMES), 'frame')
        # No status_chip: the title treatments already lead with a subject icon,
        # and the urgency line below carries the 🔴/⏳ signal.
        parts = [title()]
        if urgency:
            parts += ["", urgency]
        parts += ["", frame_plain(style)]
        if link_line:
            parts += ["", link_line]
        parts += ["", f"<i>{flavour}</i>", "", cta]
        return "\n".join(parts)

    def design_framed_titled():
        """Course code embedded in the top border, subject named below."""
        style = pick(list(ASCII_FRAMES), 'frame')
        parts = []
        if urgency:
            parts += [urgency, ""]
        parts += [frame_titled(style), "", f"{sub_icon} <b>{name_e}</b>"]
        if link_line:
            parts += ["", link_line]
        parts += ["", f"<i>{flavour}</i>", "", cta]
        return "\n".join(parts)

    def design_framed_full():
        """Everything inside one frame — the most poster-like layout."""
        style = pick(list(ASCII_FRAMES), 'frame')
        parts = [frame_full(style)]
        if urgency:
            parts += ["", urgency]
        if link_line:
            parts += ["", link_line]
        parts += ["", f"<i>{flavour}</i>", "", cta]
        return "\n".join(parts)

    def design_notice():
        """Status line leads, title and data follow, flavour quoted at the end."""
        parts = [f"{status_chip} <b>CLASS NOTICE</b>", rule, ""]
        if urgency:
            parts += [urgency, ""]
        parts += [title(), "", data_quote()]
        if link_line:
            parts += ["", link_line]
        parts += ["", f"<i>{flavour}</i>", "", cta]
        return "\n".join(parts)

    design = pick_named({
        'hero': design_hero, 'editorial': design_editorial,
        'ticker': design_ticker, 'brief': design_brief,
        'chips': design_chips, 'card': design_card,
        'notice': design_notice, 'framed': design_framed,
        'framed_titled': design_framed_titled,
        'framed_full': design_framed_full,
    }, 'design')
    return design()





async def send_alert_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Bulletproof scheduled class alert with multi-layer fallback:
    1. Try sending with topic ID + HTML
    2. If topic fails → Send to General 
    3. If HTML fails → Strip and send plain text
    4. If all fails → Retry with exponential backoff
    """
    import random
    job = context.job
    data = job.data
    max_retries = 4  # Increased retries
    retry_count = data.get('retry_count', 0)
    
    try:
        link = data.get('link') if data.get('link') != 'None' else None
        link_text = f"🔗 <a href='{link}'>JOIN CLASS NOW →</a>" if link else ""
        
        # Generate message content.
        # Use .get() throughout: job data edited by older builds may be missing
        # keys, and a KeyError here would send nothing at all.
        _batch = data.get('batch', '—')
        _subject = data.get('subject', 'Class')
        _time = data.get('time_display', '')
        manual = data.get('manual_msg')

        if data.get('msg_type') == "AI" or not manual:
            # ━━━━ RICH TEMPLATE SYSTEM (with ASCII Frames & Layout Cards) ━━━━
            text = _generate_class_notification(_batch, _subject, _time, link)
        else:
            text = str(manual)
        
        # Sanitize any forbidden HTML tags
        text = sanitize_html(text)
        
        msg = text
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Mark me present", callback_data=f"att_{job.name}")]])
        
        sent = False
        
        # FALLBACK LEVEL 1: Try with topic + HTML
        # _resolve_thread_id applies the /classtopic default when this class has
        # no override of its own. Fallback levels 2 and 3 below still drop to
        # General if the resolved topic is gone, so a stale default can't
        # swallow an alert.
        try:
            await context.bot.send_message(
                job.chat_id, 
                text=msg, 
                parse_mode=ParseMode.HTML, 
                reply_markup=kb, 
                disable_web_page_preview=True,
                message_thread_id=_resolve_thread_id(data)
            )
            sent = True
        except Exception as e1:
            err1 = str(e1)
            logger.warning(f"Fallback 1 failed for {job.name}: {err1}")
            
            # FALLBACK LEVEL 2: Send to General (no topic)
            if "thread" in err1.lower() or "topic" in err1.lower():
                try:
                    await context.bot.send_message(
                        job.chat_id, 
                        text=f"⚠️ <i>Topic unavailable</i>\n\n{msg}", 
                        parse_mode=ParseMode.HTML, 
                        reply_markup=kb, 
                        disable_web_page_preview=True,
                        message_thread_id=None
                    )
                    sent = True
                except Exception as e2:
                    err1 = str(e2)  # Update error for next fallback
                    logger.warning(f"Fallback 2 failed for {job.name}: {e2}")
            
            # FALLBACK LEVEL 3: Strip HTML, send plain text
            if not sent and ("parse" in err1.lower() or "entity" in err1.lower() or "tag" in err1.lower()):
                try:
                    clean_msg = re.sub(r'<[^>]+>', '', msg)
                    await context.bot.send_message(
                        job.chat_id, 
                        text=clean_msg, 
                        reply_markup=kb, 
                        disable_web_page_preview=True,
                        message_thread_id=None
                    )
                    sent = True
                    logger.info(f"Sent {job.name} as plain text (HTML stripped)")
                except Exception as e3:
                    logger.warning(f"Fallback 3 failed for {job.name}: {e3}")
        
        if sent:
            # Record which class this attendance button belongs to NOW, while we
            # still have the job data. The report must never guess it from the ID.
            seed_attendance_record(job.name, data)
            remove_job_from_db(job.name)
            logger.info(f"✅ Alert sent: {job.name}")
        else:
            raise Exception("All fallback attempts failed")
        
    except Exception as e:
        logger.error(f"❌ Failed to send alert (attempt {retry_count + 1}): {e}")
        
        if retry_count < max_retries:
            # Retry with jitter (1-2 minutes)
            new_data = data.copy()

            new_data['retry_count'] = retry_count + 1
            
            # Add jitter to prevent thundering herd (60-90 seconds)
            jitter = random.randint(0, 30)
            retry_time = datetime.now(IST) + timedelta(seconds=60 + jitter)
            context.job_queue.run_once(
                send_alert_job, 
                retry_time, 
                chat_id=job.chat_id, 
                name=f"{job.name}_retry{retry_count + 1}", 
                data=new_data
            )
            logger.info(f"🔄 Retry scheduled for {job.name} in ~1 minute")
        else:
            # Final fallback - log for admin review
            logger.error(f"❌ CRITICAL: Max retries ({max_retries}) reached for {job.name}. Alert LOST.")
            # Could add notification to admin here in future

async def restore_jobs(application: Application):
    count = 0
    now_ts = datetime.now(IST).timestamp()
    jobs_to_restore = DB.get("active_jobs", [])[:]
    
    for job_entry in jobs_to_restore:
        try:
            if job_entry["timestamp"] < now_ts:
                remove_job_from_db(job_entry["name"])
                continue
            run_dt = datetime.fromtimestamp(job_entry["timestamp"], IST)
            application.job_queue.run_once(send_alert_job, run_dt, chat_id=job_entry["chat_id"], name=job_entry["name"], data=job_entry["data"])
            count += 1
        except Exception: continue
    if count > 0: logger.info(f"♻️ RESTORED {count} JOBS")

# ==============================================================================
# 📝 CUSTOM MESSAGE SCHEDULER
# ==============================================================================
async def start_custom_msg(update, context):
    """Start custom message scheduler"""
    try:
        if not await require_private_admin(update, context): return ConversationHandler.END
        if not DB["config"]["group_id"]:
            await update.message.reply_text(
                "⛔ <b>NO GROUP LINKED!</b>\n\n"
                "<i>Add me to a group first.</i>",
                parse_mode=ParseMode.HTML
            )
            return ConversationHandler.END
        
        kb = [
            [InlineKeyboardButton("🟦 CSDA", callback_data="cmsg_CSDA"),
             InlineKeyboardButton("🟧 AICS", callback_data="cmsg_AICS")],
            [InlineKeyboardButton("📢 Both Batches", callback_data="cmsg_BOTH")]
        ]
        await update.message.reply_text(
            "📝 <b>CUSTOM MESSAGE SCHEDULER</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Schedule a custom announcement!</i>\n\n"
            "👇 <b>Select target batch:</b>",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )
        return CUSTOM_MSG_BATCH
    except Exception as e:
        logger.error(f"Error in start_custom_msg: {e}")
        await update.message.reply_text("❌ An error occurred.")
        return ConversationHandler.END

async def cmsg_batch_selected(update, context):
    """Handle batch selection"""
    try:
        query = update.callback_query
        await query.answer()
        context.user_data['cmsg_batch'] = query.data.replace("cmsg_", "")
        context.user_data['cmsg_days'] = []  # Initialize empty days list
        
        await query.edit_message_text(
            "📅 <b>SELECT DAYS</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Tap to toggle days, then hit</i> <b>DONE</b> 🚀",
            reply_markup=days_keyboard([]),
            parse_mode=ParseMode.HTML
        )
        return CUSTOM_MSG_DAYS
    except Exception as e:
        logger.error(f"Error in cmsg_batch_selected: {e}")
        return ConversationHandler.END

async def cmsg_toggle_days(update, context):
    """Handle day toggling for custom message"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "days_done":
        if not context.user_data.get('cmsg_days'): 
            await query.answer("⚠️ Please select at least one day!", show_alert=True)
            return CUSTOM_MSG_DAYS
            
        await query.edit_message_text(
            "📅 <b>START DATE</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Enter in format:</i> <code>DD-MM-YYYY</code>\n"
            "<i>Or type:</i> <code>Today</code>",
            parse_mode=ParseMode.HTML
        )
        return CUSTOM_MSG_START
    
    # Toggle individual day
    if query.data.startswith("toggle_"):
        day = query.data.split("_")[1]
        days = context.user_data.get('cmsg_days', [])
        
        if day in days: 
            days.remove(day)
        else: 
            days.append(day)
            
        context.user_data['cmsg_days'] = days
        await query.edit_message_reply_markup(days_keyboard(days))
        
    return CUSTOM_MSG_DAYS

async def cmsg_time_input(update, context):
    """Handle time input (Moved to after End Date)"""
    try:
        text = update.message.text.strip()
        try:
            h, m = map(int, text.split(':'))
            if h < 0 or h > 23 or m < 0 or m > 59:
                raise ValueError()
            context.user_data['cmsg_time'] = text
        except:
            await update.message.reply_text(
                "❌ <b>INVALID TIME!</b>\n\n"
                "<i>Use format:</i> <code>HH:MM</code>",
                parse_mode=ParseMode.HTML
            )
            return CUSTOM_MSG_TIME
        
        await update.message.reply_text(
            "✍️ <b>MESSAGE CONTENT</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Type your announcement message:</i>\n\n"
            "💡 <b>Tip:</b> You can use HTML:\n"
            "<code>&lt;b&gt;bold&lt;/b&gt;</code>, <code>&lt;i&gt;italic&lt;/i&gt;</code>\n"
            "<code>&lt;a href='url'&gt;link&lt;/a&gt;</code>",
            parse_mode=ParseMode.HTML
        )
        return CUSTOM_MSG_TEXT
    except Exception as e:
        logger.error(f"Error in cmsg_time_input: {e}")
        return ConversationHandler.END

async def cmsg_start_date(update, context):
    """Handle start date input"""
    try:
        text = update.message.text.strip().lower()
        if text == 'today':
            start_dt = datetime.now(IST).replace(hour=0, minute=0, second=0)
        else:
            try:
                start_dt = datetime.strptime(text, "%d-%m-%Y").replace(tzinfo=IST)
            except:
                await update.message.reply_text(
                    "❌ <b>INVALID FORMAT!</b>\n\n"
                    "<i>Use:</i> <code>DD-MM-YYYY</code>",
                    parse_mode=ParseMode.HTML
                )
                return CUSTOM_MSG_START
        
        context.user_data['cmsg_start'] = start_dt
        await update.message.reply_text(
            "📅 <b>END DATE (Optional)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Enter in format:</i> <code>DD-MM-YYYY</code>\n"
            "<i>Or type:</i> <code>None</code> <i>for one-time message</i>",
            parse_mode=ParseMode.HTML
        )
        return CUSTOM_MSG_END
    except Exception as e:
        logger.error(f"Error in cmsg_start_date: {e}")
        return ConversationHandler.END

async def cmsg_end_date(update, context):
    """Handle end date input"""
    try:
        text = update.message.text.strip().lower()
        if text == 'none':
            context.user_data['cmsg_end'] = None
        else:
            try:
                end_dt = datetime.strptime(text, "%d-%m-%Y").replace(tzinfo=IST)
                context.user_data['cmsg_end'] = end_dt
            except:
                await update.message.reply_text(
                    "❌ <b>INVALID FORMAT!</b>\n\n"
                    "<i>Use:</i> <code>DD-MM-YYYY</code> or <code>None</code>",
                    parse_mode=ParseMode.HTML
                )
                return CUSTOM_MSG_END
        
        await update.message.reply_text(
            "⏰ <b>SCHEDULE TIME</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Enter time in 24h format:</i>\n"
            "<code>HH:MM</code> (e.g., <code>14:30</code>)",
            parse_mode=ParseMode.HTML
        )
        return CUSTOM_MSG_TIME
    except Exception as e:
        logger.error(f"Error in cmsg_end_date: {e}")
        return ConversationHandler.END

async def cmsg_text_input(update, context):
    """Handle message text input"""
    try:
        context.user_data['cmsg_text'] = update.message.text
        
        kb = [
            [InlineKeyboardButton("⏭️ Skip (No Link)", callback_data="cmsg_link_skip")]
        ]
        await update.message.reply_text(
            "🔗 <b>ADD LINK (Optional)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Enter a link to include:</i>\n"
            "<i>Or tap Skip to continue without link</i>",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )
        return CUSTOM_MSG_LINK
    except Exception as e:
        logger.error(f"Error in cmsg_text_input: {e}")
        return ConversationHandler.END

async def cmsg_link_input(update, context):
    """Handle link input"""
    try:
        if update.callback_query:
            await update.callback_query.answer()
            context.user_data['cmsg_link'] = None
        else:
            context.user_data['cmsg_link'] = update.message.text.strip()
        
        # Check for topics
        topics = DB.get("topics", {})
        default_tid, default_name = get_class_topic()
        if topics:
            kb = []
            if default_tid:
                kb.append([InlineKeyboardButton(
                    f"⭐ Use Default ({default_name})", callback_data="ctopic_default")])
            row = []
            for tid, name in topics.items():
                row.append(InlineKeyboardButton(name, callback_data=f"ctopic_{tid}"))
                if len(row) == 2:
                    kb.append(row)
                    row = []
            if row: kb.append(row)
            kb.append([InlineKeyboardButton("📢 General (No Topic)", callback_data="ctopic_general")])
            
            msg_obj = update.callback_query.message if update.callback_query else update.message
            await msg_obj.reply_text(
                "💬 <b>SELECT TOPIC</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "<i>Where should this announcement go?</i> 👇",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.HTML
            )
            return SELECT_TOPIC
        else:
            context.user_data['cmsg_topic_id'] = None
            return await cmsg_finalize(update, context)
            
    except Exception as e:
        logger.error(f"Error in cmsg_link_input: {e}")
        return ConversationHandler.END

async def cmsg_topic_selection(update, context):
    """Handle topic selection for custom message"""
    try:
        query = update.callback_query
        await query.answer()
        data = query.data
        
        if data == "ctopic_default":
            context.user_data['cmsg_topic_id'] = None
        elif data == "ctopic_general":
            context.user_data['cmsg_topic_id'] = GENERAL_TOPIC
        else:
            tid = data.replace("ctopic_", "")
            try:
                context.user_data['cmsg_topic_id'] = int(tid)
            except ValueError:
                context.user_data['cmsg_topic_id'] = None

        return await cmsg_finalize(update, context)
    except Exception as e:
        logger.error(f"Error in cmsg_topic_selection: {e}")
        return ConversationHandler.END

async def cmsg_finalize(update, context):
    """Finalize and schedule custom message"""
    try:
        d = context.user_data
        batch = d['cmsg_batch']
        time_str = d['cmsg_time']
        start_dt = d['cmsg_start']
        end_dt = d.get('cmsg_end')
        msg_text = d['cmsg_text']
        link = d.get('cmsg_link')
        topic_id = d.get('cmsg_topic_id')
        
        h, m = map(int, time_str.split(':'))
        gid = DB["config"]["group_id"]
        
        # Determine days to schedule
        # Determine days to schedule
        selected_days = d.get('cmsg_days', [])
        day_map = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
        target_weekdays = [day_map[day] for day in selected_days]
        days = []

        if end_dt:
            current = start_dt
            while current <= end_dt:
                if current.weekday() in target_weekdays:
                    days.append(current)
                current += timedelta(days=1)
        else:
            # If no end date, find the next occurrence for EACH selected day
            for target_wd in target_weekdays:
                current = start_dt
                # Calculate days until next target weekday
                days_ahead = target_wd - current.weekday()
                if days_ahead < 0:
                    days_ahead += 7
                next_date = current + timedelta(days=days_ahead)
                days.append(next_date)
            
            # Sort days just in case
            days.sort()
        
        count = 0
        for day in days:
            run_dt = day.replace(hour=h, minute=m, second=0)
            if run_dt < datetime.now(IST):
                continue
            
            job_id = f"cmsg_{batch}_{int(time.time())}_{count}"
            job_data = {
                "batch": batch,
                "subject": "Custom",
                "time_display": time_str,
                "link": link or "None",
                "msg_type": "custom",
                "manual_msg": msg_text,
                "message_thread_id": topic_id
            }
            
            context.job_queue.run_once(send_custom_msg_job, run_dt, chat_id=gid, name=job_id, data=job_data)
            add_job_to_db(job_id, run_dt.timestamp(), gid, job_data)
            count += 1
        
        save_db()
        
        msg_obj = update.callback_query if update.callback_query else update
        reply_func = msg_obj.message.reply_text if hasattr(msg_obj, 'message') else msg_obj.reply_text
        
        topic_name = _topic_label(topic_id)
        
        await reply_func(
            f"✅ <b>CUSTOM MESSAGE SCHEDULED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📢 <b>Batch:</b> {batch}\n"
            f"💬 <b>Topic:</b> {topic_name}\n"
            f"⏰ <b>Time:</b> {time_str}\n"
            f"📅 <b>Messages queued:</b> {count}\n\n"
            f"<i>The announcement will be dispatched at the scheduled time.</i>",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Error in cmsg_finalize: {e}")
        if update.callback_query:
            await update.callback_query.message.reply_text("❌ An error occurred.")
        else:
            await update.message.reply_text("❌ An error occurred.")
        return ConversationHandler.END

async def send_custom_msg_job(context: ContextTypes.DEFAULT_TYPE):
    """Send custom scheduled message"""
    try:
        job = context.job
        data = job.data
        msg = data.get('manual_msg', '')
        link = data.get('link')
        topic_id = _resolve_thread_id(data)
        
        if link and link != "None":
            msg += f"\n\n🔗 <a href='{link}'>Click Here</a>"
        
        await context.bot.send_message(
            job.chat_id, 
            text=msg, 
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=False,
            message_thread_id=topic_id
        )
        remove_job_from_db(job.name)
        logger.info(f"✅ Custom message sent: {job.name}")
    except Exception as e:
        logger.error(f"❌ Failed to send custom message: {e}")

# ==============================================================================
# 💬 FORUM TOPIC MANAGEMENT
# ==============================================================================

async def register_topic_command(update, context):
    """Command to register current topic: /topic <name>"""
    try:
        user = update.effective_user
        if not is_admin(user.username):
            await deny_access(update, context)
            return
        
        chat = update.effective_chat
        if not chat.is_forum:
            await update.message.reply_text("⛔ This command is only for Supergroups with Topics enabled.")
            return

        thread_id = update.message.message_thread_id
        if not thread_id:
            await update.message.reply_text("⛔ Use this command INSIDE a topic.")
            return

        topic_name = " ".join(context.args)
        if not topic_name:
            # Try to get from reply or just default
            topic_name = f"Topic {thread_id}"
            await update.message.reply_text("⚠️ Please provide a name: `/topic Class Updates`")
            return

        if "topics" not in DB: DB["topics"] = {}
        DB["topics"][str(thread_id)] = topic_name
        save_db()

        await update.message.reply_text(
            f"✅ <b>TOPIC REGISTERED!</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>ID:</b> {thread_id}\n"
            f"🏷️ <b>Name:</b> {topic_name}",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Error in register_topic: {e}")

async def classtopic_command(update, context):
    """
    /classtopic — choose the forum topic every scheduled class alert posts into.

    Three forms:
      • run inside a topic, in the linked group -> that topic becomes the default
      • run in a DM                             -> read-only status
      • /classtopic off                         -> back to General

    Deliberately additive: classes that carry their own topic override still win,
    and clearing this setting restores exactly the old behaviour.
    """
    try:
        user = update.effective_user
        if not is_admin(user.username if user else None):
            await deny_access(update, context)
            return

        remember_owner_id(user, update)

        message = update.effective_message
        chat = update.effective_chat
        arg = (context.args[0].strip().lower() if context.args else "")
        cur_id, cur_name = get_class_topic()

        # ── Clear ──────────────────────────────────────────────────────────
        if arg in ("off", "none", "general", "clear", "reset"):
            DB["config"]["class_topic_id"] = None
            DB["config"]["class_topic_name"] = None
            save_db()
            audit("class_topic_cleared", user, f"was={cur_id or 'unset'}")
            await message.reply_text(
                "✅ <b>CLASS TOPIC CLEARED</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "<i>Class alerts will now post to</i> <b>General</b>.\n\n"
                "<i>Classes with their own topic override are unaffected.</i>",
                parse_mode=ParseMode.HTML
            )
            return

        # ── Status (DM) ────────────────────────────────────────────────────
        if is_private_chat(update):
            grp_name = DB.get("config", {}).get("group_name", "❌ No Group Linked")
            if cur_id:
                body = (
                    f"🏷️ Current: <b>{html.escape(str(cur_name))}</b> "
                    f"(ID <code>{cur_id}</code>)\n"
                    f"📍 Group: <b>{html.escape(safe_text(grp_name, '—'))}</b>\n\n"
                )
            else:
                body = (
                    "🏷️ Current: <b>General</b> <i>(no topic set)</i>\n"
                    f"📍 Group: <b>{html.escape(safe_text(grp_name, '—'))}</b>\n\n"
                )
            await message.reply_text(
                "#️⃣ <b>CLASS TOPIC</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"{body}"
                "<i>To change it, run</i> <code>/classtopic</code> <i>inside the "
                "topic you want, in the group.</i>\n"
                "<i>Use</i> <code>/classtopic off</code> <i>to send to General.</i>",
                parse_mode=ParseMode.HTML
            )
            return

        # ── Set (in group) ─────────────────────────────────────────────────
        linked_id = DB.get("config", {}).get("group_id")
        if linked_id and chat.id != linked_id:
            await message.reply_text(
                "⛔ <b>WRONG GROUP</b>\n\n"
                "<i>This isn't the group I'm scheduling classes for, so a topic "
                "here would never receive alerts.</i>",
                parse_mode=ParseMode.HTML
            )
            return

        if not getattr(chat, "is_forum", False):
            await message.reply_text(
                "⛔ <b>TOPICS NOT ENABLED</b>\n\n"
                "<i>This command only works in a supergroup with Topics turned "
                "on.</i>",
                parse_mode=ParseMode.HTML
            )
            return

        thread_id = message.message_thread_id
        if not thread_id:
            await message.reply_text(
                "⛔ <b>RUN THIS INSIDE A TOPIC</b>\n\n"
                "<i>Open the topic you want class alerts in, then send</i> "
                "<code>/classtopic</code> <i>there.</i>",
                parse_mode=ParseMode.HTML
            )
            return

        # Prefer a name we already know; fall back to the creation event.
        topic_name = DB.get("topics", {}).get(str(thread_id))
        if not topic_name:
            created = getattr(message, "reply_to_message", None)
            created = getattr(created, "forum_topic_created", None) if created else None
            topic_name = getattr(created, "name", None) or f"Topic {thread_id}"

        DB["config"]["class_topic_id"] = int(thread_id)
        DB["config"]["class_topic_name"] = topic_name
        # Keep it visible in /topics too, so the two lists never disagree.
        if not isinstance(DB.get("topics"), dict):
            DB["topics"] = {}
        DB["topics"][str(thread_id)] = topic_name
        save_db()

        audit("class_topic_set", user, f"topic={thread_id} name={topic_name}")

        await message.reply_text(
            "✅ <b>CLASS TOPIC SET</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🏷️ Topic: <b>{html.escape(safe_text(topic_name, '—'))}</b>\n"
            f"📌 ID: <code>{thread_id}</code>\n\n"
            "<i>All scheduled class alerts will now post here by default.</i>\n"
            "<i>Use</i> <code>/classtopic off</code> <i>to send to General.</i>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Error in classtopic_command: {e}")
        try:
            await update.effective_message.reply_text(
                "❌ <b>COULD NOT SET CLASS TOPIC</b>\n\n"
                f"<code>{html.escape(str(e)[:150])}</code>",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass


async def auto_register_topic(update, context):
    """Auto-register new topics created in the group"""
    try:
        if not update.message or not update.message.forum_topic_created: return
        
        topic = update.message.forum_topic_created
        thread_id = update.message.message_thread_id
        name = topic.name
        
        if "topics" not in DB: DB["topics"] = {}
        DB["topics"][str(thread_id)] = name
        save_db()
        
        # Notify about auto-registration
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            message_thread_id=thread_id,
            text=f"✅ <b>TOPIC DETECTED!</b>\nAdded to Titan database.",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Error in auto_register_topic: {e}")

async def admin_command(update, context):
    """Show admin tools keyboard"""
    if not await require_private_admin(update, context): return
    
    kb = [
        [KeyboardButton("➕ Add Subject"), KeyboardButton("🗑️ Delete Class")],
        [KeyboardButton("📤 Export Data"), KeyboardButton("📥 Import Data")],
        [KeyboardButton("👥 Manage Admins"), KeyboardButton("💬 Manage Topics")],
        [KeyboardButton("🌙 Night Schedule"), KeyboardButton("☁️ Force Save")],
        [KeyboardButton("🔄 Reset System"), KeyboardButton("🔙 Back to Main")]
    ]
    await update.message.reply_text(
        "🛠️ <b>ADMIN TOOLS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Select an action:</i> 👇",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True),
        parse_mode=ParseMode.HTML
    )

async def manage_topics_handler(update, context):
    """Show Manage Topics Menu"""
    if not await require_private_admin(update, context): return
    
    kb = [
        [KeyboardButton("➕ Add Topic Manual"), KeyboardButton("🗑️ Remove Topic")],
        [KeyboardButton("📋 List Topics"), KeyboardButton("🔙 Back to Main")]
    ]
    await update.message.reply_text(
        "💬 <b>MANAGE TOPICS</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Manage your forum topics for scheduling.</i>\n\n"
        "💡 <b>Tip:</b> Go to a topic and type <code>/topic Name</code> to add it quickly!",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True),
        parse_mode=ParseMode.HTML
    )

async def view_topics(update, context):
    """List all registered topics"""
    if not await require_private_admin(update, context): return
    
    topics = DB.get("topics", {})
    if not topics:
        await update.message.reply_text("📭 <b>NO TOPICS FOUND.</b>")
        return

    class_tid = get_class_topic()[0]
    msg = "💬 <b>REGISTERED TOPICS</b>\n━━━━━━━━━━━━━━━━━━\n\n"
    for tid, name in topics.items():
        star = " ⭐ <i>class alerts</i>" if class_tid and str(tid) == str(class_tid) else ""
        msg += f"🏷️ <b>{name}</b> (ID: {tid}){star}\n"
    
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

# --- Add Topic Manual Wizard ---
async def start_add_topic(update, context):
    if not await require_private_admin(update, context): return ConversationHandler.END
    await update.message.reply_text(
        "➕ <b>ADD TOPIC</b>\n"
        "<i>Enter the Topic Name:</i>",
        parse_mode=ParseMode.HTML
    )
    return ADD_TOPIC_NAME

async def save_topic_name(update, context):
    context.user_data['new_topic_name'] = update.message.text
    await update.message.reply_text(
        "🆔 <b>ENTER TOPIC ID</b>\n"
        "<i>Enter the Message Thread ID:</i>\n"
        "(You can find this by forwarding a message from the topic to bots like @userinfobot)",
        parse_mode=ParseMode.HTML
    )
    return ADD_TOPIC_ID

async def save_topic_id(update, context):
    try:
        tid = update.message.text.strip()
        if not tid.isdigit():
            await update.message.reply_text("❌ <b>INVALID ID!</b> Numbers only.")
            return ADD_TOPIC_ID
            
        name = context.user_data['new_topic_name']
        if "topics" not in DB: DB["topics"] = {}
        DB["topics"][str(tid)] = name
        save_db()
        
        await update.message.reply_text(
            f"✅ <b>TOPIC ADDED!</b>\nName: {name}\nID: {tid}",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Error in save_topic_id: {e}")
        return ConversationHandler.END

# --- Remove Topic Wizard ---
async def start_remove_topic(update, context):
    if not await require_private_admin(update, context): return ConversationHandler.END
    topics = DB.get("topics", {})
    if not topics:
        await update.message.reply_text("📭 <b>NO TOPICS TO REMOVE.</b>")
        return ConversationHandler.END
        
    msg = "🗑️ <b>REMOVE TOPIC</b>\n<i>Enter the Topic ID to remove:</i>\n\n"
    for tid, name in topics.items():
        msg += f"• {name} (ID: <code>{tid}</code>)\n"
        
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    return REMOVE_TOPIC_INPUT

async def remove_topic_save(update, context):
    tid = update.message.text.strip()
    topics = DB.get("topics", {})
    
    if tid in topics:
        name = topics[tid]
        del DB["topics"][tid]
        note = _clear_class_topic_if(tid)
        save_db()
        await update.message.reply_text(
            f"✅ <b>REMOVED:</b> {name}{note}", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("❌ <b>ID NOT FOUND!</b>", parse_mode=ParseMode.HTML)
    return ConversationHandler.END

# --- Edit Topic Command ---
async def start_edit_topic(update, context):
    """Start edit topic wizard - /edittopic"""
    if not await require_private_admin(update, context): return ConversationHandler.END
    topics = DB.get("topics", {})
    if not topics:
        await update.message.reply_text(
            "📭 <b>NO TOPICS TO EDIT.</b>\n\n"
            "<i>Add topics first using /topic in a forum thread.</i>",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END
    
    rows = []
    for tid, name in topics.items():
        rows.append([InlineKeyboardButton(f"🏷️ {name}", callback_data=f"edtopic_{tid}")])
    rows.append([InlineKeyboardButton("🔙 Cancel", callback_data="edtopic_cancel")])
    
    await update.message.reply_text(
        "✏️ <b>EDIT TOPIC</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Select a topic to rename:</i> 👇",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML
    )
    return EDIT_TOPIC_SELECT

async def edit_topic_select(update, context):
    """Handle topic selection for editing"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "edtopic_cancel":
        await query.edit_message_text("❌ Cancelled.")
        return ConversationHandler.END
    
    tid = query.data.replace("edtopic_", "")
    context.user_data['edit_topic_id'] = tid
    context.user_data['edit_topic_old_name'] = DB.get("topics", {}).get(tid, "Unknown")
    
    await query.edit_message_text(
        f"✏️ <b>RENAME TOPIC</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Current name: <b>{context.user_data['edit_topic_old_name']}</b>\n\n"
        f"<i>Enter the new name:</i>",
        parse_mode=ParseMode.HTML
    )
    return EDIT_TOPIC_NEW_NAME

async def edit_topic_save(update, context):
    """Save the renamed topic"""
    new_name = update.message.text.strip()
    tid = context.user_data['edit_topic_id']
    old_name = context.user_data['edit_topic_old_name']
    
    if "topics" not in DB:
        DB["topics"] = {}
    
    DB["topics"][tid] = new_name
    # Keep the cached class-topic name from going stale after a rename.
    current = DB.get("config", {}).get("class_topic_id")
    if current is not None and str(current) == str(tid):
        DB["config"]["class_topic_name"] = new_name
    save_db()
    
    await update.message.reply_text(
        f"✅ <b>TOPIC RENAMED!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔄 <b>{old_name}</b> ➡️ <b>{new_name}</b>",
        parse_mode=ParseMode.HTML
    )
    return ConversationHandler.END

# --- Delete Topic Command ---
async def start_delete_topic(update, context):
    """Start delete topic - /deletetopic"""
    if not await require_private_admin(update, context): return ConversationHandler.END
    topics = DB.get("topics", {})
    if not topics:
        await update.message.reply_text("📭 <b>NO TOPICS TO DELETE.</b>", parse_mode=ParseMode.HTML)
        return ConversationHandler.END
    
    rows = []
    for tid, name in topics.items():
        rows.append([InlineKeyboardButton(f"🗑️ {name}", callback_data=f"deltopic_{tid}")])
    rows.append([InlineKeyboardButton("🔙 Cancel", callback_data="deltopic_cancel")])
    
    await update.message.reply_text(
        "🗑️ <b>DELETE TOPIC</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Select a topic to delete:</i> 👇",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML
    )
    return DELETE_TOPIC_CONFIRM

async def delete_topic_confirm(update, context):
    """Handle topic deletion"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "deltopic_cancel":
        await query.edit_message_text("❌ Cancelled.")
        return ConversationHandler.END
    
    tid = query.data.replace("deltopic_", "")
    topics = DB.get("topics", {})
    
    if tid in topics:
        name = topics[tid]
        del DB["topics"][tid]
        note = _clear_class_topic_if(tid)
        save_db()
        await query.edit_message_text(
            f"✅ <b>DELETED:</b> {name}\n\n"
            f"<i>Topic ID {tid} removed.</i>{note}",
            parse_mode=ParseMode.HTML
        )
    else:
        await query.edit_message_text("❌ <b>Topic not found!</b>", parse_mode=ParseMode.HTML)
    
    return ConversationHandler.END

# --- Topics List Command ---
async def topics_command(update, context):
    """List all topics - /topics"""
    if not await require_private_admin(update, context): return
    
    topics = DB.get("topics", {})
    if not topics:
        await update.message.reply_text(
            "📭 <b>NO TOPICS REGISTERED</b>\n\n"
            "<i>Go to a forum topic and type:</i>\n"
            "<code>/topic TopicName</code>",
            parse_mode=ParseMode.HTML
        )
        return
    
    class_tid, class_name = get_class_topic()
    msg = "💬 <b>REGISTERED TOPICS</b>\n━━━━━━━━━━━━━━━━━━\n\n"
    for tid, name in topics.items():
        star = "  ⭐" if class_tid and str(tid) == str(class_tid) else ""
        msg += f"🏷️ <b>{name}</b>{star}\n    ID: <code>{tid}</code>\n\n"

    dest = (f"⭐ <b>{html.escape(str(class_name))}</b>" if class_tid
            else "<b>General</b> <i>(no topic set)</i>")
    msg += (
        "━━━━━━━━━━━━━━━━━━\n"
        f"#️⃣ <b>Class alerts go to:</b> {dest}\n\n"
        "📝 <b>Commands:</b>\n"
        "• /classtopic - Set topic for class alerts\n"
        "• /edittopic - Rename a topic\n"
        "• /deletetopic - Remove a topic"
    )
    
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

# ==============================================================================
# 🌙 NIGHT SCHEDULE (NEXT-DAY SUMMARY)
# ==============================================================================
async def start_night_schedule(update, context):
    """Setup night schedule for next-day class summary"""
    try:
        if not await require_private_admin(update, context): return ConversationHandler.END
        
        current_time = DB.get("config", {}).get("night_schedule_time", None)
        status = f"Currently set to: <b>{current_time}</b>" if current_time else "Not currently set"
        
        await update.message.reply_text(
            "🌙 <b>NIGHT SCHEDULE SETUP</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{status}\n\n"
            "<i>Enter time for daily next-day summary:</i>\n"
            "<code>HH:MM</code> (e.g., <code>21:00</code> for 9 PM)\n\n"
            "<i>Or type</i> <code>off</code> <i>to disable</i>",
            parse_mode=ParseMode.HTML
        )
        return NIGHT_SCHEDULE_TIME
    except Exception as e:
        logger.error(f"Error in start_night_schedule: {e}")
        await update.message.reply_text("❌ An error occurred.")
        return ConversationHandler.END

async def save_night_schedule_time(update, context):
    """Save the night schedule time"""
    try:
        text = update.message.text.strip().lower()
        
        if text == "off":
            DB["config"]["night_schedule_time"] = None
            save_db()
            # Remove any existing night schedule job
            for job in context.job_queue.jobs():
                if job.name == "night_summary_job":
                    job.schedule_removal()
            
            await update.message.reply_text(
                "🌙 <b>NIGHT SCHEDULE DISABLED</b>\n\n"
                "<i>No more daily summaries will be sent.</i>",
                parse_mode=ParseMode.HTML
            )
            return ConversationHandler.END
        
        try:
            h, m = map(int, text.split(':'))
            if h < 0 or h > 23 or m < 0 or m > 59:
                raise ValueError()
        except:
            await update.message.reply_text(
                "❌ <b>INVALID TIME!</b>\n\n"
                "<i>Use format:</i> <code>HH:MM</code>",
                parse_mode=ParseMode.HTML
            )
            return NIGHT_SCHEDULE_TIME
        
        DB["config"]["night_schedule_time"] = text
        save_db()
        
        # Schedule the nightly job
        schedule_night_summary(context.application, h, m)
        
        await update.message.reply_text(
            f"🌙 <b>NIGHT SCHEDULE SET!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"⏰ <b>Time:</b> {text}\n\n"
            f"<i>I'll send a summary of tomorrow's classes every night!</i> 🌟",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Error in save_night_schedule_time: {e}")
        await update.message.reply_text("❌ An error occurred.")
        return ConversationHandler.END

def schedule_night_summary(app, hour, minute):
    """Schedule the nightly summary job"""
    # Remove existing job if any
    for job in app.job_queue.jobs():
        if job.name == "night_summary_job":
            job.schedule_removal()
    
    # Schedule daily job at the specified time
    target_time = dtime(hour=hour, minute=minute, tzinfo=IST)
    app.job_queue.run_daily(
        send_night_summary,
        time=target_time,
        name="night_summary_job"
    )
    logger.info(f"🌙 Night summary scheduled for {hour:02d}:{minute:02d}")

async def send_night_summary(context: ContextTypes.DEFAULT_TYPE):
    """Send summary of next day's classes"""
    try:
        gid = DB["config"].get("group_id")
        if not gid:
            logger.error("No group ID for night summary")
            return
        
        tomorrow = datetime.now(IST) + timedelta(days=1)
        tomorrow_weekday = tomorrow.weekday()
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        tomorrow_day = day_names[tomorrow_weekday]
        
        # Get jobs scheduled for tomorrow
        jobs = context.job_queue.jobs()
        tomorrow_classes = []
        
        for job in jobs:
            if job.name and isinstance(job.data, dict) and 'batch' in job.data:
                if job.next_t.date() == tomorrow.date():
                    tomorrow_classes.append({
                        'batch': job.data.get('batch', '—'),
                        'subject': job.data.get('subject', 'Class'),
                        'time': job.data.get('time_display', '')
                    })
        
        if not tomorrow_classes:
            msg = (
                "🌙 <b>TOMORROW'S SCHEDULE</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📅 <b>{tomorrow_day}, {tomorrow.strftime('%d %b')}</b>\n\n"
                "🎉 <i>No classes scheduled! Enjoy your day!</i>"
            )
        else:
            msg = (
                "🌙 <b>TOMORROW'S SCHEDULE</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📅 <b>{tomorrow_day}, {tomorrow.strftime('%d %b')}</b>\n\n"
            )
            for c in sorted(tomorrow_classes, key=lambda x: x['time']):
                msg += f"📖 <b>{c['batch']}</b> • {c['subject']}\n"
                msg += f"     ⏰ {c['time']}\n\n"
            msg += "<i>Get ready for tomorrow! 💪</i>"
        
        # Same destination as the class alerts it summarises.
        try:
            await context.bot.send_message(
                gid, text=msg, parse_mode=ParseMode.HTML,
                message_thread_id=get_class_topic()[0]
            )
        except Exception as topic_err:
            logger.warning(f"Night summary topic send failed, using General: {topic_err}")
            await context.bot.send_message(gid, text=msg, parse_mode=ParseMode.HTML)
        logger.info("🌙 Night summary sent")
        
    except Exception as e:
        logger.error(f"Error sending night summary: {e}")

# ==============================================================================
# 📊 12. EXTRAS
# ==============================================================================
async def export_data(update, context):
    """Export complete database backup"""
    # The dump contains the admin list, every class link, attendance records and
    # the feedback table with real user IDs behind the "anonymous" promise. That
    # is the whole database in one file, so it's super-admin only.
    if not await require_super_admin(update, context, action="export_denied"): return

    audit("export_data", update.effective_user, "full database dump")

    # Ensure all keys exist in export
    export_db = {
        "config": DB.get("config", {"group_id": None, "group_name": "❌ No Group Linked"}),
        "subjects": DB.get("subjects", {"CSDA": [], "AICS": []}),
        "active_jobs": DB.get("active_jobs", []),
        "attendance": DB.get("attendance", {}),
        "feedback": DB.get("feedback", []),
        "system_stats": DB.get("system_stats", {}),
        "schedules": DB.get("schedules", []),
        "admins": DB.get("admins", []),
        "topics": DB.get("topics", {}),
        "audit_log": DB.get("audit_log", [])
    }
    
    f = io.BytesIO(json.dumps(export_db, indent=2).encode())
    f.name = f"vasuki_backup_{datetime.now(IST).strftime('%Y%m%d_%H%M')}.json"
    
    # Count stats
    total_subjects = sum(len(s) for s in export_db['subjects'].values())
    
    await context.bot.send_document(
        update.effective_chat.id,
        document=f,
        caption=(
            "📦 <b>BACKUP EXPORTED!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<b>Contents:</b>\n"
            f"┣ 📚 Subjects: {total_subjects}\n"
            f"┣ 📅 Scheduled Jobs: {len(export_db['active_jobs'])}\n"
            f"┣ 💬 Topics: {len(export_db['topics'])}\n"
            f"┣ 👥 Admins: {len(export_db['admins'])}\n"
            f"┣ 📊 Attendance Records: {len(export_db['attendance'])}\n"
            f"┗ 💬 Feedback: {len(export_db['feedback'])}\n\n"
            "💾 <i>Import this file to restore all data!</i>"
        ),
        parse_mode=ParseMode.HTML
    )

async def import_request(update, context):
    # Import rewrites the whole database, so it sits at the same privilege tier
    # as adding an admin. Otherwise a password-tier admin could upload a file
    # that hands themselves the admin list and points every job at their chat.
    if not await require_super_admin(update, context, action="import_request"): return
    await update.message.reply_text(
        "📥 <b>IMPORT DATA</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "⚠️ <b>WARNING:</b> <i>This will OVERWRITE classes, subjects, topics, "
        "attendance and feedback!</i>\n\n"
        "🔒 <i>Protected — never taken from the file:</i>\n"
        "┣ 👥 Admin list\n"
        "┣ 📍 Group link\n"
        "┗ 🧾 Audit log\n\n"
        "<i>Upload your</i> <code>.json</code> <i>backup file below:</i>",
        parse_mode=ParseMode.HTML
    )
    context.user_data['wait_import'] = True

# Keys an uploaded file is never allowed to set. Everything here either grants
# privilege or redirects where the bot posts, so it stays under the live config.
IMPORT_PROTECTED_KEYS = ("admins", "config", "owner_ids", "audit_log")


async def handle_import_file(update, context):
    """Import database and restore scheduled jobs"""
    user = update.effective_user

    # Any JSON document sent in DM lands here. Re-authorise rather than trusting
    # the user_data flag alone: it's the only thing standing between an uploaded
    # file and a full database overwrite.
    if not is_super_admin(getattr(user, "username", None)):
        audit("import_denied", user, "non-super-admin uploaded a JSON file", ok=False)
        await mirror_non_admin(context, update, bot_reply=None,
                               event="uploaded a JSON file (ignored)")
        return

    if not context.user_data.get('wait_import'):
        await update.message.reply_text(
            "📥 <i>Tap</i> <b>📥 Import Data</b> <i>first, then send the file.</i>",
            parse_mode=ParseMode.HTML
        )
        return

    try:
        file = await update.message.document.get_file()
        raw = await file.download_as_bytearray()
        imported_data = json.loads(raw.decode())
        
        # Validate imported data
        if not isinstance(imported_data, dict):
            await update.message.reply_text("❌ <b>INVALID FILE!</b>\n\nExpected JSON object.", parse_mode=ParseMode.HTML)
            return
        
        # Clear existing jobs from job queue
        jobs = context.job_queue.jobs()
        cleared = 0
        for job in jobs:
            if job.name and isinstance(job.data, dict):
                job.schedule_removal()
                cleared += 1
        
        # Merge with defaults to ensure all keys exist. Protected keys are read
        # from the LIVE database, never from the file.
        global DB
        kept = {k: DB.get(k) for k in IMPORT_PROTECTED_KEYS}
        ignored = [k for k in IMPORT_PROTECTED_KEYS if k in imported_data]

        DB = {
            "config": kept.get("config") or {"group_id": None, "group_name": "❌ No Group Linked"},
            "subjects": imported_data.get("subjects", {"CSDA": [], "AICS": []}),
            "active_jobs": imported_data.get("active_jobs", []),
            "attendance": imported_data.get("attendance", {}),
            "feedback": imported_data.get("feedback", []),
            "system_stats": imported_data.get("system_stats", {"start_time": time.time(), "classes_scheduled": 0, "ai_requests": 0}),
            "schedules": imported_data.get("schedules", []),
            "admins": kept.get("admins") or [],
            "topics": imported_data.get("topics", {}),
            "audit_log": kept.get("audit_log") or [],
            "owner_ids": kept.get("owner_ids") or [],
        }
        # A key present but set to null bypasses the defaults above, so coerce
        # types before anything reads the imported shape.
        _ensure_db_shape()

        # Jobs carry their own chat_id, so an uploaded file could redirect every
        # class alert (and its join link) into a chat the uploader controls.
        # Force them all onto the linked group.
        linked_group = DB["config"].get("group_id")
        redirected = 0
        clean_jobs = []
        for job_entry in DB["active_jobs"]:
            if not isinstance(job_entry, dict) or "name" not in job_entry \
                    or "timestamp" not in job_entry:
                continue
            if linked_group is not None and job_entry.get("chat_id") != linked_group:
                job_entry["chat_id"] = linked_group
                redirected += 1
            clean_jobs.append(job_entry)
        DB["active_jobs"] = clean_jobs

        # Save to cloud
        save_db()
        audit("import_data", user,
              f"jobs={len(clean_jobs)} redirected={redirected} "
              f"ignored_keys={','.join(ignored) or 'none'}")
        
        # Restore jobs from imported data
        restored = 0
        now_ts = datetime.now(IST).timestamp()
        for job_entry in DB.get("active_jobs", []):
            try:
                if job_entry["timestamp"] < now_ts:
                    continue  # Skip expired jobs
                run_dt = datetime.fromtimestamp(job_entry["timestamp"], IST)
                context.job_queue.run_once(
                    send_alert_job, 
                    run_dt, 
                    chat_id=job_entry["chat_id"], 
                    name=job_entry["name"], 
                    data=job_entry["data"]
                )
                restored += 1
            except Exception as e:
                logger.error(f"Failed to restore job: {e}")
                continue

        if ignored or redirected:
            await notify_owner(
                context,
                "📥 <b>DATABASE IMPORTED</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 {user_tag(user)}\n"
                f"🔒 Ignored protected keys: <code>{html.escape(', '.join(ignored) or 'none')}</code>\n"
                f"🔄 Jobs redirected to the linked group: <b>{redirected}</b>"
            )

        await update.message.reply_text(
            "✅ <b>IMPORT SUCCESSFUL!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📦 <b>Data Imported:</b>\n"
            f"┣ 📚 Subjects: {sum(len(s) for s in DB['subjects'].values())}\n"
            f"┣ 📅 Schedules Restored: {restored}\n"
            f"┣ 💬 Topics: {len(DB.get('topics', {}))}\n"
            f"┣ 👥 Admins: {len(DB.get('admins', []))}\n"
            f"┗ 🎯 Group: {DB['config'].get('group_name', 'None')}\n\n"
            "✅ <i>All data is now live!</i>",
            parse_mode=ParseMode.HTML
        )
        
    except json.JSONDecodeError:
        await update.message.reply_text("❌ <b>INVALID JSON!</b>\n\nFile is not valid JSON format.", parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Import error: {e}")
        await update.message.reply_text(f"❌ <b>IMPORT FAILED!</b>\n\n<code>{str(e)[:100]}</code>", parse_mode=ParseMode.HTML)
    finally:
        context.user_data['wait_import'] = False

# ------------------------------------------------------------------------------
# 📊 ATTENDANCE RECORDS
# ------------------------------------------------------------------------------
# An attendance record is a dict:
#   {
#     "batch": str, "subject": str, "time_display": "HH:MM",
#     "class_ts": epoch of class start, "sent_ts": epoch the alert went out,
#     "users": [ {"id", "username", "name", "ts"} ]
#   }
# Older builds stored a bare list of name strings, so every reader goes through
# _normalize_attendance() rather than assuming a shape.

def _class_timestamp(time_display, fallback_ts=None):
    """Epoch for today's HH:MM in IST. Falls back if unparseable."""
    try:
        h, m = str(time_display).strip().split(":")[:2]
        now = datetime.now(IST)
        return now.replace(hour=int(h), minute=int(m[:2]), second=0,
                           microsecond=0).timestamp()
    except Exception:
        return fallback_ts if fallback_ts is not None else time.time()


def _normalize_attendance(rec):
    """Return a record dict regardless of whether `rec` is new-style or a
    legacy list of plain name strings."""
    if isinstance(rec, dict):
        users = rec.get("users")
        if not isinstance(users, list):
            users = []
        clean = []
        for u in users:
            if isinstance(u, dict):
                clean.append(u)
            else:
                clean.append({"id": None, "username": None,
                              "name": safe_text(u, "Unknown"), "ts": None})
        out = dict(rec)
        out["users"] = clean
        return out
    if isinstance(rec, list):
        return {
            "batch": None, "subject": None, "time_display": None,
            "class_ts": None, "sent_ts": None,
            "users": [{"id": None, "username": None,
                       "name": safe_text(u, "Unknown"), "ts": None}
                      for u in rec],
            "_legacy": True,
        }
    return {"batch": None, "subject": None, "time_display": None,
            "class_ts": None, "sent_ts": None, "users": []}


def _display_name(user):
    """Prefer @username; fall back to full name, then first name, then ID."""
    uname = safe_text(getattr(user, "username", None) or "", "")
    if uname:
        return uname
    first = safe_text(getattr(user, "first_name", None) or "", "")
    last = safe_text(getattr(user, "last_name", None) or "", "")
    full = (first + " " + last).strip()
    return full or f"User {getattr(user, 'id', '?')}"


def seed_attendance_record(job_name, data):
    """Store class metadata the moment the alert is sent, so the report never
    has to reverse-engineer it out of the job-ID string."""
    try:
        if not isinstance(DB.get("attendance"), dict):
            DB["attendance"] = {}
        existing = DB["attendance"].get(job_name)
        users = _normalize_attendance(existing)["users"] if existing else []
        DB["attendance"][job_name] = {
            "batch": safe_text(data.get("batch"), "—"),
            "subject": safe_text(data.get("subject"), "Class"),
            "time_display": safe_text(data.get("time_display"), ""),
            "class_ts": _class_timestamp(data.get("time_display")),
            "sent_ts": time.time(),
            "users": users,
        }
    except Exception as e:
        logger.error(f"Failed to seed attendance for {job_name}: {e}")


# Attendance is the one callback every member may use, which makes it the only
# unauthenticated write path into the database. Two guards:
#   • the job ID must belong to a real class (seeded when the alert was sent, or
#     still live in the job queue) — otherwise arbitrary callback data could
#     create unbounded keys, each one triggering a full-database upload
#   • a per-user rate limit, so nobody can pump those uploads in a loop
# Historic IDs (created before job_tag() normalised them) can still contain
# '&', '-' or '.', and those buttons are still live in the group.
ATT_ID_RE = re.compile(r"^[A-Za-z0-9_&.\-]{1,64}$")
ATT_MAX_TAPS = 8
ATT_WINDOW = 60
_att_taps = {}


def _attendance_rate_ok(user_id):
    now = time.time()
    taps = _att_taps.setdefault(user_id, deque(maxlen=ATT_MAX_TAPS))
    while taps and now - taps[0] > ATT_WINDOW:
        taps.popleft()
    if len(taps) >= ATT_MAX_TAPS:
        return False
    taps.append(now)
    if len(_att_taps) > 500:
        for k in [k for k, v in _att_taps.items()
                  if not v or now - v[-1] > ATT_WINDOW]:
            _att_taps.pop(k, None)
    return True


def _tap_came_from_our_alert(query, context):
    """
    True when this callback belongs to a button THIS bot attached to a message
    it posted in the linked group.

    This is the guard that actually matters. Only the bot can attach an inline
    keyboard, so a matching button on a bot-authored message is proof the class
    ID is genuine — no database lookup required. The DB check alone was too
    strict: the seeded record lives behind a debounced cloud write, so a restart
    (or a record aged out by cleanup_old_data) left real buttons permanently
    dead with "that class no longer exists".
    """
    msg = getattr(query, "message", None)
    if msg is None:
        return False

    sender = getattr(msg, "from_user", None)
    bot_id = getattr(getattr(context, "bot", None), "id", None)
    if not sender or not bot_id or getattr(sender, "id", None) != bot_id:
        return False

    group_id = (DB.get("config") or {}).get("group_id")
    chat_id = getattr(getattr(msg, "chat", None), "id", None)
    try:
        # group_id has been stored as a string by older imports.
        if group_id is not None and int(chat_id) != int(group_id):
            return False
    except (TypeError, ValueError):
        return False

    markup = getattr(msg, "reply_markup", None)
    rows = getattr(markup, "inline_keyboard", None) or []
    return any(getattr(btn, "callback_data", None) == query.data
               for row in rows for btn in row)


def _is_known_class_id(job_id, context, query=None):
    """True only for a job ID this bot actually created."""
    if isinstance(DB.get("attendance"), dict) and job_id in DB["attendance"]:
        return True
    try:
        if context.job_queue.get_jobs_by_name(job_id):
            return True
    except Exception:
        pass
    if any(j.get("name") == job_id for j in DB.get("active_jobs", [])
           if isinstance(j, dict)):
        return True
    return query is not None and _tap_came_from_our_alert(query, context)


async def mark_attendance(update, context):
    query = update.callback_query
    job_id = query.data.replace("att_", "")
    user = query.from_user

    # Rate limit BEFORE validating, so a flood of forged IDs can't fill the audit
    # log (which would push real entries out of the capped list).
    if not _attendance_rate_ok(getattr(user, "id", 0)):
        await query.answer("⏳ Slow down a moment.", show_alert=True)
        return

    if not ATT_ID_RE.match(job_id) or not _is_known_class_id(job_id, context, query):
        logger.warning(f"Rejected attendance for unknown class id {job_id!r} "
                       f"from {getattr(user, 'id', '?')}")
        audit("attendance_forged", user, f"unknown class id: {job_id[:60]}", ok=False)
        await query.answer("⚠️ That class no longer exists.", show_alert=True)
        return

    try:
        if not isinstance(DB.get("attendance"), dict):
            DB["attendance"] = {}

        rec = _normalize_attendance(DB["attendance"].get(job_id))
        # Alert was sent by an older build (or before a restart) — keep whatever
        # we can rather than dropping the tap.
        rec.pop("_legacy", None)
        if rec.get("class_ts") is None:
            rec["class_ts"] = time.time()

        name = _display_name(user)
        if any(u.get("id") == user.id for u in rec["users"] if u.get("id")) or \
           any(u.get("name") == name for u in rec["users"]):
            await query.answer("⚠️ Already marked!", show_alert=True)
            return

        rec["users"].append({
            "id": user.id,
            "username": safe_text(user.username, "") or None,
            "name": name,
            "ts": time.time(),
        })
        DB["attendance"][job_id] = rec
        save_db()
        await query.answer(f"✅ Marked present: {name}")
    except Exception as e:
        logger.error(f"mark_attendance failed for {job_id}: {e}")
        try:
            await query.answer("❌ Could not mark attendance. Try again.",
                               show_alert=True)
        except Exception:
            pass

async def view_schedule_handler(update, context):
    """Level 1 of the schedule browser: pick a batch."""
    if not await require_private_admin(update, context): return

    class_jobs = _collect_class_jobs(context)
    if not class_jobs:
        msg = (
            "📭 <b>NO UPCOMING CLASSES!</b>\n\n"
            "<i>Schedule some classes first.</i>"
        )
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(msg, parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
        return

    context.user_data.pop('sch_view', None)
    context.user_data.pop('sch_sub_idx', None)

    rows = _batch_picker_rows(class_jobs, "sch_")
    text = (
        f"📅 <b>CLASS SCHEDULE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>{len(class_jobs)} upcoming session(s)</i>\n\n"
        "<i>Select a batch to view:</i> 👇"
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.HTML)


async def _sch_render_batches(query, context, class_jobs):
    rows = _batch_picker_rows(class_jobs, "sch_")
    await query.edit_message_text(
        f"📅 <b>CLASS SCHEDULE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>{len(class_jobs)} upcoming session(s)</i>\n\n"
        "<i>Select a batch to view:</i> 👇",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML
    )


async def _sch_render_subjects(query, context, class_jobs):
    """Level 2: subjects inside the chosen batch view."""
    view = context.user_data.get('sch_view', 'all')
    view_jobs = _jobs_for_view(class_jobs, view)
    groups = _subject_groups(view_jobs)

    if not groups:
        return await _sch_render_batches(query, context, class_jobs)

    page_items, page, total_pages = _paginate(groups, context.user_data.get('sch_sub_page', 0))
    context.user_data['sch_sub_page'] = page
    offset = page * CLASS_NAV_PAGE_SIZE

    rows = []
    for i, (subject, batch, sessions) in enumerate(page_items):
        chip = _batch_chip(batch) if view == "all" else "📖"
        rows.append([InlineKeyboardButton(
            f"{chip} {_short_subject(subject, 22)} ({len(sessions)})",
            callback_data=f"sch_s_{offset + i}"
        )])

    nav = _nav_row("sch_", "sub", page, total_pages)
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔙 Back to Batches", callback_data="sch_back_batches")])

    page_note = f"<i>Page {page + 1}/{total_pages} · </i>" if total_pages > 1 else ""
    await query.edit_message_text(
        f"📅 <b>{_crumb('SCHEDULE', view)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{page_note}<i>{len(groups)} subject(s) · {len(view_jobs)} session(s)</i>\n\n"
        "<i>Select a subject:</i> 👇",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML
    )


async def _sch_render_sessions(query, context, class_jobs):
    """Level 3: read-only detail for every session of one subject."""
    view = context.user_data.get('sch_view', 'all')
    groups = _subject_groups(_jobs_for_view(class_jobs, view))
    idx = context.user_data.get('sch_sub_idx')

    if idx is None or idx >= len(groups):
        return await _sch_render_subjects(query, context, class_jobs)

    subject, batch, sessions = groups[idx]

    msg = (
        f"📅 <b>{_crumb('SCHEDULE', view, subject)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📚 <b>{html.escape(safe_text(subject, 'Class'))}</b>\n"
        f"🎯 {_batch_chip(batch)} <b>{html.escape(safe_text(batch, '—'))}</b> · "
        f"{len(sessions)} session(s)\n\n"
    )

    # This level renders as text, not buttons, so cap it against Telegram's
    # 4096-character message limit rather than paginating.
    SESSION_DETAIL_CAP = 20
    for job in sessions[:SESSION_DETAIL_CAP]:
        d = safe_job_data(job)
        try:
            day_name = job.next_t.strftime("%A")
            date_str = job.next_t.strftime("%d %b")
        except Exception:
            day_name, date_str = "Scheduled", ""
        time_str = _format_time_12h(d.get('time_display') or "")
        if not time_str:
            try:
                time_str = _format_time_12h(job.next_t.strftime("%H:%M"))
            except Exception:
                time_str = "—"

        msg += f"• 🗓 <b>{day_name}</b>, {date_str} — ⏰ <code>{html.escape(time_str)}</code>\n"

        details = []
        link = d.get('link')
        if link and str(link) not in ("None", "Check Group"):
            details.append("🔗 Link attached")
        elif str(link) == "Check Group":
            details.append("🔗 Check group")

        details.append(f"💬 {html.escape(_topic_label(d.get('message_thread_id')))}")

        details.append("✍️ Custom" if d.get('manual_msg') else "✨ Auto")
        msg += f"   <i>{' · '.join(details)}</i>\n"

    if len(sessions) > SESSION_DETAIL_CAP:
        msg += f"\n<i>…and {len(sessions) - SESSION_DETAIL_CAP} more session(s)</i>\n"

    rows = [[InlineKeyboardButton("🔙 Back to Subjects", callback_data="sch_back_subjects")]]
    await query.edit_message_text(
        msg, reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.HTML)


async def schedule_nav(update, context):
    """Router for every level of the schedule browser."""
    if not await require_admin_callback(update, context): return
    query = update.callback_query
    await query.answer()
    data = query.data

    class_jobs = _collect_class_jobs(context)
    if not class_jobs:
        await query.edit_message_text(
            "📭 <b>NO UPCOMING CLASSES!</b>\n\n"
            "<i>Schedule some classes first.</i>",
            parse_mode=ParseMode.HTML
        )
        return

    # Legacy flat-pagination buttons from an older build.
    if data.startswith("schedule_page_") or data == "sch_back_batches":
        context.user_data['sch_sub_page'] = 0
        return await _sch_render_batches(query, context, class_jobs)

    if data.startswith("sch_b_"):
        context.user_data['sch_view'] = data[len("sch_b_"):]
        context.user_data['sch_sub_page'] = 0
        return await _sch_render_subjects(query, context, class_jobs)

    if data.startswith("sch_pgsub_"):
        try:
            context.user_data['sch_sub_page'] = int(data.rsplit("_", 1)[1])
        except ValueError:
            context.user_data['sch_sub_page'] = 0
        return await _sch_render_subjects(query, context, class_jobs)

    if data == "sch_back_subjects":
        return await _sch_render_subjects(query, context, class_jobs)

    if data.startswith("sch_s_"):
        try:
            context.user_data['sch_sub_idx'] = int(data.rsplit("_", 1)[1])
        except ValueError:
            return await _sch_render_subjects(query, context, class_jobs)
        return await _sch_render_sessions(query, context, class_jobs)

    return await _sch_render_batches(query, context, class_jobs)

async def prompt_image_upload(update, context):
    if not await require_private_admin(update, context): return
    await update.message.reply_text(
        "📸 <b>AI TIMETABLE SCANNER</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🧠 <i>Send me a photo of your timetable</i>\n"
        "🤖 <i>I'll automatically schedule all classes!</i>\n\n"
        "✨ <b>Tip:</b> <i>Clearer images = better results!</i>",
        parse_mode=ParseMode.HTML
    )

def _legacy_ts_from_job_id(job_id):
    """
    Best-effort timestamp for records saved before metadata was stored.

    Job IDs come in several shapes:
        CSDA_1755000000_3          -> batch_timestamp_counter
        COMBINED_1755000000_3
        cmsg_CSDA_1755000000_3
        <any of the above>_retry2
    The old report read parts[2], which is the COUNTER for the common 3-part
    form — that is why every row rendered as 01 Jan 1970, 05:30 IST
    (epoch 0 in IST). Scan for a value that is plausibly a unix timestamp
    instead of trusting a fixed position.
    """
    for part in str(job_id).split('_'):
        if part.isdigit() and len(part) >= 9:
            try:
                v = int(part)
                if 1_000_000_000 < v < 4_000_000_000:
                    return v
            except Exception:
                continue
    return None


def _legacy_batch_from_job_id(job_id):
    parts = str(job_id).split('_')
    if not parts:
        return None
    head = parts[0]
    if head == "cmsg":
        return parts[1] if len(parts) > 1 else "Custom"
    if head == "COMBINED":
        return "CSDA & AICS"
    return head or None


async def view_attendance_stats(update, context):
    """Attendance report: who attended which class, when."""
    if not await require_private_admin(update, context): return

    if not isinstance(DB.get("attendance"), dict):
        DB["attendance"] = {}

    raw = DB["attendance"]
    if not raw:
        await update.message.reply_text(
            "📊 <b>NO ATTENDANCE DATA</b>\n\n"
            "<i>No class alerts have gone out yet, so there is nothing to "
            "report.</i>",
            parse_mode=ParseMode.HTML
        )
        return

    # Build a sortable list so the newest class appears first.
    entries = []
    for job_id, rec_raw in raw.items():
        rec = _normalize_attendance(rec_raw)
        ts = rec.get("class_ts") or rec.get("sent_ts") or _legacy_ts_from_job_id(job_id)
        entries.append({
            "job_id": job_id,
            "ts": ts,
            "batch": rec.get("batch") or _legacy_batch_from_job_id(job_id) or "—",
            "subject": rec.get("subject"),
            "time_display": rec.get("time_display"),
            "users": rec.get("users", []),
        })
    entries.sort(key=lambda e: (e["ts"] is not None, e["ts"] or 0), reverse=True)

    shown = entries[:10]
    total_marks = sum(len(e["users"]) for e in entries)

    msg = "📊 <b>ATTENDANCE REPORT</b>\n" + "━" * 24 + "\n\n"

    for e in shown:
        # ── Heading: subject, falling back to batch when unknown ──
        subject = e["subject"]
        if subject and subject != "Custom":
            code, name = _split_subject(subject)
            heading = html.escape(safe_text(name, "Class"))
            sub_line = html.escape(safe_text(code, "")) if code else ""
        else:
            heading = html.escape(safe_text(e["batch"], "Class"))
            sub_line = ""

        msg += f"📖 <b>{heading}</b>\n"
        if sub_line:
            msg += f"   <i>{sub_line}</i>\n"

        # ── When ──
        if e["ts"]:
            dt = datetime.fromtimestamp(e["ts"], IST)
            when = dt.strftime("%a, %d %b %Y")
            clock = e["time_display"] or dt.strftime("%H:%M")
            msg += f"   🕒 {html.escape(_format_time_12h(clock))}  ·  {when}\n"
        else:
            msg += "   🕒 <i>time unknown</i>\n"

        msg += f"   🎓 {html.escape(safe_text(e['batch'], '—'))}\n"

        # ── Who ──
        users = e["users"]
        if users:
            msg += f"   👥 <b>{len(users)} present</b>\n"
            for u in users[:15]:
                uname = safe_text(u.get("username"), "")
                nm = html.escape(safe_text(u.get("name"), "Unknown"))
                label = f"@{html.escape(uname)}" if uname else nm
                marked = ""
                if u.get("ts"):
                    marked = f" <i>({datetime.fromtimestamp(u['ts'], IST).strftime('%H:%M')})</i>"
                msg += f"      • {label}{marked}\n"
            if len(users) > 15:
                msg += f"      <i>…and {len(users) - 15} more</i>\n"
        else:
            msg += "   👥 <i>nobody marked present</i>\n"
        msg += "\n"

    msg += "━" * 24 + "\n"
    msg += (f"<i>Showing {len(shown)} of {len(entries)} classes · "
            f"{total_marks} total check-ins</i>")

    await send_message_safe(
        context.bot, update.effective_chat.id, safe_text(msg),
        parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )

async def handle_photo(update, context):
    if not await require_private_admin(update, context): return
    if not DB["config"]["group_id"]:
        await update.message.reply_text(
            "⛔ <b>NO GROUP LINKED!</b>\n\n"
            "<i>Add me to a group first!</i>",
            parse_mode=ParseMode.HTML
        )
        return

    msg = await update.message.reply_text(
        "🧠 <b>AI ANALYZING...</b>\n\n"
        "⏳ <i>Please wait while I scan your timetable...</i>",
        parse_mode=ParseMode.HTML
    )
    try:
        f = await update.message.photo[-1].get_file()
        b = await f.download_as_bytearray()
        sch = await analyze_timetable_image(b)
        
        if not sch:
            await msg.edit_text(
                "❌ <b>AI VISION FAILED!</b>\n\n"
                "<i>Could not read the timetable. Try a clearer image.</i>",
                parse_mode=ParseMode.HTML
            )
            return

        c = 0
        now = datetime.now(IST)
        day_map = {"Mon":0, "Tue":1, "Wed":2, "Thu":3, "Fri":4, "Sat":5, "Sun":6}
        gid = DB["config"]["group_id"]
        weeks_to_schedule = 4  # Schedule for 4 weeks ahead
        
        for i in sch:
            batch, sub = i.get("batch", "CSDA"), i.get("subject", "Unk")
            day, t = i.get("day", "Mon"), i.get("time", "10:00")
            
            if batch not in DB["subjects"]: DB["subjects"][batch] = []
            if sub not in DB["subjects"][batch]: DB["subjects"][batch].append(sub)
            
            target = day_map.get(day, 0)
            delta = target - now.weekday()
            if delta <= 0: delta += 7
            
            h, m = map(int, t.split(':'))
            
            # Schedule for multiple weeks
            for week in range(weeks_to_schedule):
                run = now + timedelta(days=delta + (week * 7))
                run = run.replace(hour=h, minute=m, second=0)
                
                # Batch text comes straight from the vision model, so it can
                # contain spaces or punctuation — normalise before it becomes
                # part of the attendance callback data.
                jid = f"{job_tag(batch, 'CLASS')}_{job_tag(day, 'DAY')}_{int(time.time())}_{c}"
                jdata = {"batch": batch, "subject": sub, "time_display": t, "link": "Check Group", "msg_type": "AI", "day": day}
                
                context.job_queue.run_once(send_alert_job, run, chat_id=gid, name=jid, data=jdata)
                add_job_to_db(jid, run.timestamp(), gid, jdata)
                c += 1
        
        save_db()
        await msg.edit_text(
            f"✅ <b>AI SCAN COMPLETE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<b>{c} classes scheduled from timetable image.</b>\n\n"
            f"<i>Use</i> 📅 <b>View Schedule</b> <i>to review all entries.</i>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await msg.edit_text(
            f"❌ <b>ERROR!</b>\n\n"
            f"<code>{e}</code>",
            parse_mode=ParseMode.HTML
        )

# ==============================================================================
# 🧠 13. CUSTOM AI
# ==============================================================================
async def start_gemini_tool(update, context):
    if not await require_private_admin(update, context): return ConversationHandler.END
    await update.message.reply_text(
        "🧠 <b>AI ASSISTANT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Enter your prompt below and the AI will generate a response.</i>",
        parse_mode=ParseMode.HTML
    )
    return GEMINI_PROMPT_INPUT

async def process_gemini_prompt(update, context):
    msg = await update.message.reply_text(
        "🧠 <b>THINKING...</b>\n\n"
        "⏳ <i>Processing your request...</i>",
        parse_mode=ParseMode.HTML
    )
    response = await custom_gemini_task(update.message.text)
    await msg.edit_text(response[:4000], parse_mode=ParseMode.HTML)
    return ConversationHandler.END

# ==============================================================================
# 📩 14. UTILS
# ==============================================================================
async def cancelled_command(update, context):
    """Handle class cancellation announcement"""
    try:
        user = update.effective_user
        # Require Admin or specific permission? Assuming admin.
        if not is_admin(user.username):
            await deny_access(update, context)
            return
        
        # Delete the trigger message
        try:
            await update.message.delete()
        except: pass
        
        # Send announcement
        msg = (
            "🚫 <b>CLASS CANCELLED</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>The scheduled class has been cancelled.</i>\n"
            "<i>Please ignore previous notifications.</i>"
        )
        
        # Reply to the same topic
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=msg,
            parse_mode=ParseMode.HTML,
            message_thread_id=update.message.message_thread_id
        )
    except Exception as e:
        logger.error(f"Cancellation error: {e}")

async def feedback_handler(update, context):
    user = update.effective_user
    chat_type = update.effective_chat.type
    
    # In private chat, require admin. In groups, allow anyone.
    if chat_type == 'private' and not is_admin(user.username):
        await deny_access(update, context)
        return

    msg = update.message.text.replace("/feedback", "").strip()
    if msg:
        # Store username and chat info PRIVATELY (admins can see this)
        username = user.username or "no_username"
        name = user.first_name or "Unknown"
        user_id = user.id
        chat_info = f"Group: {update.effective_chat.title}" if chat_type != 'private' else "Private Chat"
        
        # Store detailed info for admin viewing
        feedback_entry = {
            "timestamp": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
            "message": msg,
            "username": username,
            "name": name,
            "user_id": user_id,
            "chat_type": chat_info
        }
        DB["feedback"].append(feedback_entry)
        save_db()
        
        # Show confirmation to user
        confirmation = (
            "✅ <b>FEEDBACK SUBMITTED</b>\n\n"
            "<i>Thank you! Your feedback has been received and recorded successfully.</i>"
        )
        await update.message.reply_text(confirmation, parse_mode=ParseMode.HTML)
        await mirror_non_admin(context, update, bot_reply=confirmation,
                               event="sent /feedback")
        
        # Delete the user's original feedback message from the group
        if chat_type != 'private':
            try:
                await update.message.delete()
            except Exception as e:
                logger.warning(f"Could not delete feedback message: {e}")
    else:
        await update.message.reply_text(
            "📝 <b>ANONYMOUS FEEDBACK</b>\n\n"
            "<i>Type /feedback by yourself! Don't Click on feedback poped up during typing.</i>\n\n"
            "<b>Usage:</b> <code>/feedback ke baad ek space dena fir likhna message</code>",
            parse_mode=ParseMode.HTML
        )

async def viewfeedback_handler(update, context):
    """View all feedback - Super admin only, private chat only.

    The stored entries carry the sender's ID and handle even though the bot tells
    them the feedback is anonymous, so this is not something a password-tier
    admin should be able to read."""
    if not await require_super_admin(update, context, action="viewfeedback_denied"): return
    
    feedback_list = DB.get("feedback", [])
    
    if not feedback_list:
        await update.message.reply_text(
            "📭 <b>NO FEEDBACK YET!</b>\n\n"
            "<i>No feedback has been submitted.</i>",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Build feedback display - handle both old (string) and new (dict) formats
    # NOTE: emoji must be written as real characters. Writing them as UTF-16
    # surrogate escapes ('\ud83d\udcac') creates unpaired surrogates that cannot
    # be encoded to UTF-8, which crashed this command with
    # "UnicodeEncodeError: surrogates not allowed".
    msg = "💬 <b>FEEDBACK INBOX</b>\n" + "━" * 24 + "\n\n"
    
    # Show last 10 feedback entries (newest first)
    recent_feedback = feedback_list[-10:][::-1]
    
    for i, entry in enumerate(recent_feedback, 1):
        if isinstance(entry, dict):
            # New format with user details
            timestamp = entry.get("timestamp", "Unknown time")
            message = safe_decode(entry.get("message", "No message"))
            username = safe_decode(entry.get("username", "no_username"))
            name = safe_decode(entry.get("name", "Unknown"))
            user_id = entry.get("user_id", "N/A")
            chat_type = safe_decode(entry.get("chat_type", "Unknown"))
            
            handle = f" (@{html.escape(username)})" if username and username != "no_username" else ""
            msg += f"<b>{i}.</b> 📅 {html.escape(safe_text(timestamp, 'Unknown time'))}\n"
            msg += f"   👤 <b>{html.escape(name)}</b>{handle}\n"
            msg += f"   🆔 <code>{html.escape(str(user_id))}</code>\n"
            msg += f"   📍 {html.escape(chat_type)}\n"
            msg += f"   📝 <i>{html.escape(message[:100])}{'...' if len(message) > 100 else ''}</i>\n\n"
        else:
            # Old string format (legacy)
            raw = str(entry)
            safe_raw = safe_decode(raw)
            escaped_entry = html.escape(safe_raw[:150])
            msg += f"<b>{i}.</b> {escaped_entry}{'...' if len(safe_raw) > 150 else ''}\n\n"
    
    total = len(feedback_list)
    msg += "━" * 24 + "\n"
    msg += f"<i>Showing {len(recent_feedback)} of {total} total feedback entries</i>"
    
    # Final safety net: one bad character anywhere must not kill the whole reply.
    await send_message_safe(
        context.bot, update.effective_chat.id, safe_text(msg),
        parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )

async def delete_menu(update, context):
    """Level 1 of the delete browser: pick a batch."""
    if not await require_private_admin(update, context): return

    class_jobs = _collect_class_jobs(context)
    if not class_jobs:
        await update.message.reply_text(
            "📭 <b>NO CLASSES TO DELETE!</b>\n\n"
            "<i>Schedule some classes first.</i>",
            parse_mode=ParseMode.HTML
        )
        return

    context.user_data.pop('del_view', None)
    context.user_data.pop('del_sub_idx', None)

    rows = _batch_picker_rows(class_jobs, "del_")
    rows.append([InlineKeyboardButton("🔙 Cancel", callback_data="del_cancel")])

    await update.message.reply_text(
        f"🗑️ <b>DELETE CLASS</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>{len(class_jobs)} scheduled session(s)</i>\n\n"
        "<i>Select a batch:</i> 👇",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML
    )


async def _del_render_batches(query, context, class_jobs):
    rows = _batch_picker_rows(class_jobs, "del_")
    rows.append([InlineKeyboardButton("🔙 Cancel", callback_data="del_cancel")])
    await query.edit_message_text(
        f"🗑️ <b>DELETE CLASS</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>{len(class_jobs)} scheduled session(s)</i>\n\n"
        "<i>Select a batch:</i> 👇",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML
    )


async def _del_render_subjects(query, context, class_jobs):
    """Level 2: subjects inside the chosen batch view."""
    view = context.user_data.get('del_view', 'all')
    view_jobs = _jobs_for_view(class_jobs, view)
    groups = _subject_groups(view_jobs)

    if not groups:
        return await _del_render_batches(query, context, class_jobs)

    page_items, page, total_pages = _paginate(groups, context.user_data.get('del_sub_page', 0))
    context.user_data['del_sub_page'] = page
    offset = page * CLASS_NAV_PAGE_SIZE

    rows = []
    for i, (subject, batch, sessions) in enumerate(page_items):
        chip = _batch_chip(batch) if view == "all" else "📖"
        rows.append([InlineKeyboardButton(
            f"{chip} {_short_subject(subject, 22)} ({len(sessions)})",
            callback_data=f"del_s_{offset + i}"
        )])

    nav = _nav_row("del_", "sub", page, total_pages)
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔙 Back to Batches", callback_data="del_back_batches")])

    page_note = f"<i>Page {page + 1}/{total_pages} · </i>" if total_pages > 1 else ""
    await query.edit_message_text(
        f"🗑️ <b>{_crumb('DELETE CLASS', view)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{page_note}<i>{len(groups)} subject(s) · {len(view_jobs)} session(s)</i>\n\n"
        "<i>Select a subject:</i> 👇",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML
    )


async def _del_render_sessions(query, context, class_jobs):
    """Level 3: sessions of one subject, plus a subject-scoped bulk delete."""
    view = context.user_data.get('del_view', 'all')
    groups = _subject_groups(_jobs_for_view(class_jobs, view))
    idx = context.user_data.get('del_sub_idx')

    if idx is None or idx >= len(groups):
        return await _del_render_subjects(query, context, class_jobs)

    subject, batch, sessions = groups[idx]
    context.user_data['del_subject'] = subject
    context.user_data['del_batch'] = batch

    page_items, page, total_pages = _paginate(sessions, context.user_data.get('del_job_page', 0))
    context.user_data['del_job_page'] = page
    offset = page * CLASS_NAV_PAGE_SIZE

    rows = []
    for i, job in enumerate(page_items):
        day_str, time_str = _session_bits(job)
        rows.append([InlineKeyboardButton(
            f"❌ {day_str} · ⏰ {time_str}", callback_data=f"del_j_{offset + i}"
        )])

    nav = _nav_row("del_", "job", page, total_pages)
    if nav:
        rows.append(nav)

    # Bulk delete is scoped to the subject you are already looking at, instead
    # of the old global "DELETE ALL" that could wipe the whole timetable in one
    # unconfirmed tap.
    if len(sessions) > 1:
        rows.append([InlineKeyboardButton(
            f"🗑️ Delete ALL {_short_subject(subject, 14)} ({len(sessions)})",
            callback_data="del_wipe"
        )])
    rows.append([InlineKeyboardButton("🔙 Back to Subjects", callback_data="del_back_subjects")])

    page_note = f"<i>Page {page + 1}/{total_pages}</i>\n" if total_pages > 1 else ""
    await query.edit_message_text(
        f"🗑️ <b>{_crumb('DELETE CLASS', view, subject)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📖 <b>{html.escape(safe_text(subject, 'Class'))}</b>\n"
        f"🎯 {_batch_chip(batch)} <b>{html.escape(safe_text(batch, '—'))}</b> · "
        f"{len(sessions)} session(s)\n{page_note}\n"
        "<i>Tap a session to delete:</i> 👇",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML
    )


async def delete_nav(update, context):
    """Router for every level of the delete browser."""
    # Global handler, so the tap can come from anyone who can see any of our
    # keyboards. Index-based callback data means a spoofed payload can only
    # address a class the caller could already list.
    if not await require_admin_callback(update, context): return
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "del_cancel":
        await query.edit_message_text("❌ Cancelled.")
        return

    class_jobs = _collect_class_jobs(context)
    if not class_jobs:
        await query.edit_message_text(
            "📭 <b>NO CLASSES LEFT</b>\n\n"
            "<i>Every scheduled session has fired or been deleted.</i>",
            parse_mode=ParseMode.HTML
        )
        return

    # Buttons from an older build addressed jobs by name; those messages are
    # stale now, so send the admin back to the top rather than acting on them.
    if data.startswith("kill_") or data in ("del_page_prev", "del_page_next"):
        return await _del_render_batches(query, context, class_jobs)

    if data == "del_back_batches":
        context.user_data['del_sub_page'] = 0
        return await _del_render_batches(query, context, class_jobs)

    if data.startswith("del_b_"):
        context.user_data['del_view'] = data[len("del_b_"):]
        context.user_data['del_sub_page'] = 0
        return await _del_render_subjects(query, context, class_jobs)

    if data.startswith("del_pgsub_"):
        try:
            context.user_data['del_sub_page'] = int(data.rsplit("_", 1)[1])
        except ValueError:
            context.user_data['del_sub_page'] = 0
        return await _del_render_subjects(query, context, class_jobs)

    if data == "del_back_subjects":
        return await _del_render_subjects(query, context, class_jobs)

    if data.startswith("del_s_"):
        try:
            context.user_data['del_sub_idx'] = int(data.rsplit("_", 1)[1])
        except ValueError:
            return await _del_render_subjects(query, context, class_jobs)
        context.user_data['del_job_page'] = 0
        return await _del_render_sessions(query, context, class_jobs)

    if data.startswith("del_pgjob_"):
        try:
            context.user_data['del_job_page'] = int(data.rsplit("_", 1)[1])
        except ValueError:
            context.user_data['del_job_page'] = 0
        return await _del_render_sessions(query, context, class_jobs)

    # Bulk delete for the current subject — always confirmed first.
    if data == "del_wipe":
        view = context.user_data.get('del_view', 'all')
        groups = _subject_groups(_jobs_for_view(class_jobs, view))
        idx = context.user_data.get('del_sub_idx')
        if idx is None or idx >= len(groups):
            return await _del_render_subjects(query, context, class_jobs)
        subject, batch, sessions = groups[idx]
        kb = [
            [InlineKeyboardButton(f"✅ Yes, delete {len(sessions)}", callback_data="del_wipe_yes")],
            [InlineKeyboardButton("🔙 No, go back", callback_data="del_back_sessions")],
        ]
        await query.edit_message_text(
            f"⚠️ <b>DELETE ALL SESSIONS?</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📖 <b>{html.escape(safe_text(subject, 'Class'))}</b>\n"
            f"🎯 {_batch_chip(batch)} {html.escape(safe_text(batch, '—'))}\n"
            f"🗑️ <b>{len(sessions)}</b> session(s) will be removed.\n\n"
            f"<i>This cannot be undone.</i>",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )
        return

    if data == "del_back_sessions":
        return await _del_render_sessions(query, context, class_jobs)

    if data == "del_wipe_yes":
        view = context.user_data.get('del_view', 'all')
        groups = _subject_groups(_jobs_for_view(class_jobs, view))
        idx = context.user_data.get('del_sub_idx')
        if idx is None or idx >= len(groups):
            return await _del_render_subjects(query, context, class_jobs)
        subject, batch, sessions = groups[idx]

        count = 0
        for j in list(sessions):
            try:
                remove_job_from_db(j.name)
                j.schedule_removal()
                count += 1
            except Exception:
                pass

        audit("delete_classes", update.effective_user,
              f"scope=subject_bulk subject={subject} batch={batch} removed={count}")
        await query.edit_message_text(
            f"✅ <b>DELETED {count} SESSION(S)</b>\n\n"
            f"📖 <i>{html.escape(safe_text(subject, 'Class'))}</i>",
            parse_mode=ParseMode.HTML
        )
        await asyncio.sleep(1.2)
        context.user_data['del_sub_idx'] = None
        return await _del_render_subjects(query, context, _collect_class_jobs(context))

    # Single session tapped — confirm the scope before removing anything.
    if data.startswith("del_j_"):
        view = context.user_data.get('del_view', 'all')
        groups = _subject_groups(_jobs_for_view(class_jobs, view))
        idx = context.user_data.get('del_sub_idx')
        try:
            session_idx = int(data.rsplit("_", 1)[1])
        except ValueError:
            return await _del_render_sessions(query, context, class_jobs)

        if idx is None or idx >= len(groups):
            return await _del_render_subjects(query, context, class_jobs)

        subject, batch, sessions = groups[idx]
        if session_idx >= len(sessions):
            return await _del_render_sessions(query, context, class_jobs)

        job = sessions[session_idx]
        context.user_data['del_job_name'] = job.name

        day_str, time_str = _session_bits(job)
        try:
            day_name = job.next_t.strftime('%A')
        except Exception:
            day_name = "Unknown"

        same_day = sum(
            1 for j in sessions
            if j.next_t and j.next_t.strftime('%A') == day_name
        )

        kb = [
            [InlineKeyboardButton("🎯 This session only", callback_data="del_scope_single")],
            [InlineKeyboardButton(f"📅 All on {day_name} ({same_day})",
                                  callback_data="del_scope_day")],
            [InlineKeyboardButton(f"📚 All {_short_subject(subject, 14)} ({len(sessions)})",
                                  callback_data="del_scope_subject")],
            [InlineKeyboardButton("🔙 Back", callback_data="del_scope_cancel")],
        ]

        await query.edit_message_text(
            f"🗑️ <b>DELETE CONFIRMATION</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📖 Subject: <b>{html.escape(safe_text(subject, 'Class'))}</b>\n"
            f"🎯 Batch: {_batch_chip(batch)} <b>{html.escape(safe_text(batch, '—'))}</b>\n"
            f"🗓 Session: <b>{html.escape(day_str)}</b> · ⏰ {html.escape(time_str)}\n\n"
            f"<i>What do you want to delete?</i> 👇",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )
        return

    return await _del_render_batches(query, context, class_jobs)

async def delete_scope_handler(update, context):
    """Handle delete scope selection"""
    if not await require_admin_callback(update, context): return
    query = update.callback_query
    await query.answer()
    scope = query.data.replace("del_scope_", "")

    if scope == "cancel":
        await _del_render_sessions(query, context, _collect_class_jobs(context))
        return

    job_name = context.user_data.get('del_job_name')
    if not job_name:
        await query.edit_message_text("❌ Error: Job lost.")
        return

    jobs = context.job_queue.get_jobs_by_name(job_name)
    if not jobs:
        await query.edit_message_text("❌ Job already deleted.")
        return
    
    ref_job = jobs[0]
    ref_data = safe_job_data(ref_job)
    subject = ref_data.get('subject')
    batch = ref_data.get('batch')
    try:
        ref_day = ref_job.next_t.strftime('%A')
    except Exception:
        ref_day = None

    all_jobs = context.job_queue.jobs()
    jobs_to_kill = []

    if scope == "single":
        jobs_to_kill = [ref_job]
    elif scope == "day":
        # Same subject/batch, same weekday — the recurring slot, not the term.
        for j in all_jobs:
            d = safe_job_data(j)
            j_day = j.next_t.strftime('%A') if j.next_t else None
            if (d.get('subject') == subject and d.get('batch') == batch
                    and ref_day and j_day == ref_day):
                jobs_to_kill.append(j)
    elif scope == "subject":
        # Delete all future classes for this subject/batch
        for j in all_jobs:
            d = safe_job_data(j)
            if d.get('subject') == subject and d.get('batch') == batch:
                jobs_to_kill.append(j)
    
    count = 0
    for j in jobs_to_kill:
        try:
            remove_job_from_db(j.name)
            j.schedule_removal()
            count += 1
        except: pass

    audit("delete_classes", update.effective_user,
          f"scope={scope} subject={subject} batch={batch} removed={count}")

    await query.edit_message_text(
        f"✅ <b>DELETED {count} SESSION(S)</b>\n\n"
        f"<i>Refreshing list…</i>",
        parse_mode=ParseMode.HTML
    )
    await asyncio.sleep(1.2)

    remaining = _collect_class_jobs(context)
    if not remaining:
        await query.edit_message_text(
            "📭 <b>NO CLASSES LEFT</b>\n\n"
            "<i>Everything scheduled has been removed.</i>",
            parse_mode=ParseMode.HTML
        )
        return

    context.user_data['del_job_page'] = 0
    if scope == "single":
        # The subject probably still has other sessions — stay where we were.
        await _del_render_sessions(query, context, remaining)
    else:
        context.user_data['del_sub_idx'] = None
        await _del_render_subjects(query, context, remaining)


async def handle_expired(update, context):
    await update.callback_query.answer("⚠️ Expired.", show_alert=True)

# ==============================================================================
# 🔄 RESET / REVOKE COMMAND
# ==============================================================================
async def reset_command(update, context):
    """Manual reset command for admins to fix issues - DOES NOT clear database schedules"""
    if not await require_private_admin(update, context): return
    
    # Clear all scheduled jobs from MEMORY ONLY (not database)
    # This fixes issues without losing saved schedules
    jobs = context.job_queue.jobs()
    cleared = 0
    for job in jobs:
        if job.name and isinstance(job.data, dict):
            job.schedule_removal()
            cleared += 1
    
    # DO NOT clear DB["active_jobs"] - preserve schedules in database!
    # Jobs will be restored from database on next bot restart
    
    await update.message.reply_text(
        "🔄 <b>VASUKI MEMORY RESET COMPLETE!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"✅ Cleared <b>{cleared}</b> jobs from memory\n"
        f"💾 Schedules preserved in database\n"
        f"🔄 Jobs will restore on next restart\n\n"
        "<i>If you're still seeing issues:</i>\n"
        "┣ 1️⃣ Go to @BotFather\n"
        "┣ 2️⃣ Send /revoke\n"
        "┣ 3️⃣ Get new token\n"
        "┗ 4️⃣ Update on Render",
        parse_mode=ParseMode.HTML
    )

# ==============================================================================
# 🧨 15. RESET DATABASE COMMAND
# ==============================================================================
async def start_reset_db(update, context):
    """Start the reset database conversation"""
    if not await require_super_admin(update, context, action="resetdatabase_denied"):
        return ConversationHandler.END
    
    kb = [
        [InlineKeyboardButton("💣 YES, DELETE EVERYTHING", callback_data="reset_confirm")],
        [InlineKeyboardButton("🔙 Cancel", callback_data="reset_cancel")]
    ]
    
    await update.message.reply_text(
        "⚠️ <b>DANGER ZONE: RESET DATABASE</b> ⚠️\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "😱 <b>ARE YOU SURE?</b>\n"
        "<i>This will permanently delete:</i>\n"
        "• All scheduled classes\n"
        "• All subjects\n"
        "• Attendance records\n"
        "• System stats\n\n"
        "👉 <i>This action CANNOT be undone!</i>",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.HTML
    )
    return RESET_CONFIRM

async def confirm_reset_db(update, context):
    """Execute the database reset"""
    if not await require_admin_callback(update, context, super_only=True): return
    query = update.callback_query
    await query.answer()
    
    if query.data == "reset_cancel":
        await query.edit_message_text(
            "✅ <b>RESET CANCELLED</b>\n\n"
            "<i>Your data is safe! Phew...</i> 😅",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END
        
    if query.data == "reset_confirm":
        global DB
        # Preserve the group link, the admin list (so nobody gets locked out) and
        # the audit trail (a wipe must not erase its own record).
        old_config = DB.get("config", DEFAULT_DB["config"])
        old_admins = DB.get("admins", [])
        old_audit = DB.get("audit_log", [])
        old_owners = DB.get("owner_ids", [])

        # Deep copy: DEFAULT_DB.copy() is shallow, so the reset DB used to share
        # DEFAULT_DB's nested lists and dicts. Every later mutation then polluted
        # the defaults for the rest of the process lifetime.
        DB = json.loads(json.dumps(DEFAULT_DB))
        DB["config"] = old_config
        DB["admins"] = old_admins
        DB["audit_log"] = old_audit
        DB["owner_ids"] = old_owners
        _ensure_db_shape()

        # Clear schedules from memory
        for job in context.job_queue.jobs():
            job.schedule_removal()

        audit("reset_database", update.effective_user, "factory reset executed")
        save_db()
        await notify_owner(
            context,
            "💥 <b>DATABASE WIPED</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 {user_tag(update.effective_user)}\n"
            f"🕒 {datetime.now(IST).strftime('%d %b %Y, %H:%M:%S IST')}"
        )
        
        await query.edit_message_text(
            "✅ <b>DATABASE RESET COMPLETE</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>All data has been reset to factory defaults.</i>\n"
            "<i>Admin list and group link have been preserved.</i>",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

# ==============================================================================
# ✏️ 16. EDIT SUBJECT COMMAND
# ==============================================================================
def _subject_list(batch):
    """
    Always return a real list for a batch.

    DB["subjects"] can be None after an import whose JSON had "subjects": null,
    and the batch key itself may be missing or hold a non-list. Reading it
    directly is what produced "'NoneType' object has no attribute 'get'".

    When batch == "BOTH", returns a deduplicated union of CSDA + AICS subjects.
    This is a *view* — edits to the returned list will NOT propagate back;
    callers that mutate must handle BOTH explicitly.
    """
    _ensure_db_shape()
    if batch == "BOTH":
        csda = DB["subjects"].get("CSDA") or []
        aics = DB["subjects"].get("AICS") or []
        # Deduplicated union, CSDA order first
        return list(dict.fromkeys(csda + [s for s in aics if s not in csda]))
    subs = DB["subjects"].get(batch)
    if not isinstance(subs, list):
        subs = []
        DB["subjects"][batch] = subs
    return subs


async def _esub_expired(query):
    """Wizard state was lost (timeout, restart, or a stale button)."""
    try:
        await query.edit_message_text(
            "⌛ <b>THIS MENU EXPIRED</b>\n\n"
            "<i>Run /editsubject again to start over.</i>",
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass
    return ConversationHandler.END


async def start_edit_subject(update, context):
    """Start the edit subject wizard"""
    if not await require_private_admin(update, context): return ConversationHandler.END

    context.user_data.pop('esub_batch', None)
    context.user_data.pop('esub_subject', None)

    kb = [
        [InlineKeyboardButton("🟦 CSDA", callback_data="esub_batch_CSDA"),
         InlineKeyboardButton("🟧 AICS", callback_data="esub_batch_AICS")],
        [InlineKeyboardButton("🟪 Both (CSDA + AICS)", callback_data="esub_batch_BOTH")],
        [InlineKeyboardButton("🔙 Cancel", callback_data="esub_cancel")]
    ]
    await update.message.reply_text(
        "✏️ <b>EDIT SUBJECT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Select the batch:</i> 👇",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.HTML
    )
    return EDIT_SUB_SELECT_BATCH


async def edit_sub_select_batch(update, context):
    """Handle batch selection and show subjects"""
    query = update.callback_query
    await query.answer()

    if query.data == "esub_cancel":
        await query.edit_message_text("❌ Cancelled.")
        return ConversationHandler.END

    # callback_data format: "esub_batch_CSDA" / "esub_batch_AICS" / "esub_batch_BOTH"
    parts = query.data.split("_", 2)
    if len(parts) < 3:
        return await _esub_expired(query)
    batch = parts[2]  # "CSDA", "AICS", or "BOTH"
    context.user_data['esub_batch'] = batch

    subs = _subject_list(batch)  # handles BOTH automatically
    if not subs:
        batch_display = "CSDA + AICS" if batch == "BOTH" else batch
        await query.edit_message_text(
            f"⚠️ <b>NO SUBJECTS IN {html.escape(batch_display)}</b>\n\n"
            f"<i>Add a subject first using</i> ➕ <b>Add Subject</b><i>, then come back.</i>",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    # Store the combined list so indices remain stable during the wizard session
    # (the BOTH list is computed fresh here and frozen into user_data).
    if batch == "BOTH":
        context.user_data['esub_both_subs'] = list(subs)

    batch_display = "CSDA + AICS" if batch == "BOTH" else batch

    # Index-based callback data: subject names can exceed Telegram's 64-byte
    # callback_data limit and may contain the '_' used as a delimiter.
    rows = [[InlineKeyboardButton(f"📖 {s}", callback_data=f"esub_pick_{i}")]
            for i, s in enumerate(subs)]
    rows.append([InlineKeyboardButton("🔙 Cancel", callback_data="esub_cancel")])

    await query.edit_message_text(
        f"✏️ <b>EDIT SUBJECT ({html.escape(batch_display)})</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<i>Select a subject to modify:</i> 👇",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML
    )
    return EDIT_SUB_SELECT_SUBJECT


async def edit_sub_select_subject(update, context):
    """Handle subject selection and show actions"""
    query = update.callback_query
    await query.answer()

    if query.data == "esub_cancel":
        await query.edit_message_text("❌ Cancelled.")
        return ConversationHandler.END

    batch = context.user_data.get('esub_batch')
    if not batch:
        return await _esub_expired(query)

    # For BOTH, use the frozen list captured when the batch was selected so that
    # indices don't shift if the DB is modified concurrently.
    if batch == "BOTH":
        subs = context.user_data.get('esub_both_subs') or _subject_list("BOTH")
    else:
        subs = _subject_list(batch)
    try:
        idx = int(query.data.rsplit("_", 1)[1])
        sub = subs[idx]
    except (ValueError, IndexError):
        return await _esub_expired(query)

    context.user_data['esub_subject'] = sub
    batch_display = "CSDA + AICS" if batch == "BOTH" else batch

    kb = [
        [InlineKeyboardButton("✏️ Rename", callback_data="esub_rename")],
        [InlineKeyboardButton("🗑️ Delete", callback_data="esub_delete")],
        [InlineKeyboardButton("🔙 Cancel", callback_data="esub_cancel")]
    ]

    await query.edit_message_text(
        f"🛠️ <b>MANAGE: {html.escape(sub)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎯 <i>Batch:</i> <b>{html.escape(batch_display)}</b>\n\n"
        f"<i>What would you like to do?</i>",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.HTML
    )
    return EDIT_SUB_ACTION


async def edit_sub_action(update, context):
    """Handle rename or delete action"""
    query = update.callback_query
    await query.answer()

    action = query.data
    if action == "esub_cancel":
        await query.edit_message_text("❌ Cancelled.")
        return ConversationHandler.END

    batch = context.user_data.get('esub_batch')
    sub = context.user_data.get('esub_subject')
    if not batch or not sub:
        return await _esub_expired(query)

    if action == "esub_delete":
        if batch == "BOTH":
            # Remove from both CSDA and AICS
            removed_from = []
            for batch_key in ("CSDA", "AICS"):
                subs_list = _subject_list(batch_key)
                if sub in subs_list:
                    subs_list.remove(sub)
                    removed_from.append(batch_key)
            save_db()
            if removed_from:
                removed_str = " &amp; ".join(removed_from)
                await query.edit_message_text(
                    f"🗑️ <b>DELETED FROM BOTH BATCHES</b>\n\n"
                    f"<i>{html.escape(sub)} was removed from {removed_str}.</i>",
                    parse_mode=ParseMode.HTML
                )
            else:
                await query.edit_message_text(
                    f"⚠️ <b>ALREADY GONE</b>\n\n"
                    f"<i>{html.escape(sub)} was not found in CSDA or AICS.</i>",
                    parse_mode=ParseMode.HTML
                )
        else:
            subs = _subject_list(batch)
            if sub in subs:
                subs.remove(sub)
                save_db()
                await query.edit_message_text(
                    f"🗑️ <b>DELETED</b>\n\n"
                    f"<i>{html.escape(sub)} was removed from {html.escape(batch)}.</i>",
                    parse_mode=ParseMode.HTML
                )
            else:
                await query.edit_message_text(
                    f"⚠️ <b>ALREADY GONE</b>\n\n"
                    f"<i>{html.escape(sub)} is no longer in {html.escape(batch)}.</i>",
                    parse_mode=ParseMode.HTML
                )
        return ConversationHandler.END

    if action == "esub_rename":
        batch_display = "CSDA + AICS" if batch == "BOTH" else batch
        await query.edit_message_text(
            f"✍️ <b>RENAME SUBJECT</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<b>Current:</b> {html.escape(sub)}\n"
            f"🎯 <i>Batch:</i> <b>{html.escape(batch_display)}</b>\n\n"
            f"<i>Send the new name, or /cancel to abort.</i>",
            parse_mode=ParseMode.HTML
        )
        return EDIT_SUB_NEW_NAME

    # Unrecognised callback (stale button): stay put rather than fall through
    # returning None with no feedback.
    return await _esub_expired(query)


async def edit_sub_save_rename(update, context):
    """Save the renamed subject"""
    new_name = safe_text(update.message.text, "").strip()
    batch = context.user_data.get('esub_batch')
    old_name = context.user_data.get('esub_subject')

    if not batch or not old_name:
        await update.message.reply_text(
            "⌛ <b>THIS WIZARD EXPIRED</b>\n\n"
            "<i>Run /editsubject again.</i>",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    if not new_name:
        await update.message.reply_text(
            "⚠️ <b>Name cannot be empty.</b>\n\n<i>Send a name or /cancel.</i>",
            parse_mode=ParseMode.HTML
        )
        return EDIT_SUB_NEW_NAME

    if new_name == old_name:
        await update.message.reply_text("⚠️ That is the same name as before.")
        return ConversationHandler.END

    if batch == "BOTH":
        # Rename in both CSDA and AICS where the old name exists
        renamed_in = []
        conflict_in = []
        for batch_key in ("CSDA", "AICS"):
            subs_list = _subject_list(batch_key)
            if new_name in subs_list and new_name != old_name:
                conflict_in.append(batch_key)
        if conflict_in:
            conflict_str = " &amp; ".join(conflict_in)
            await update.message.reply_text(
                f"⚠️ <b>{html.escape(new_name)}</b> already exists in "
                f"{conflict_str}.\n\n<i>Pick a different name.</i>",
                parse_mode=ParseMode.HTML
            )
            return EDIT_SUB_NEW_NAME
        for batch_key in ("CSDA", "AICS"):
            subs_list = _subject_list(batch_key)
            if old_name in subs_list:
                subs_list[subs_list.index(old_name)] = new_name
                renamed_in.append(batch_key)
        save_db()
        if renamed_in:
            renamed_str = " &amp; ".join(renamed_in)
            await update.message.reply_text(
                f"✅ <b>RENAMED IN BOTH BATCHES</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"{html.escape(old_name)}\n"
                f"➡️ <b>{html.escape(new_name)}</b>\n"
                f"🎯 <i>Updated in:</i> <b>{renamed_str}</b>\n\n"
                f"<i>Existing scheduled classes keep their old label.</i>",
                parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text(
                f"⚠️ <b>{html.escape(old_name)}</b> was not found in CSDA or AICS. Nothing was changed.",
                parse_mode=ParseMode.HTML
            )
        return ConversationHandler.END

    # Single-batch rename
    subs = _subject_list(batch)
    if new_name in subs:
        await update.message.reply_text(
            f"⚠️ <b>{html.escape(new_name)}</b> already exists in "
            f"{html.escape(batch)}.\n\n<i>Pick a different name.</i>",
            parse_mode=ParseMode.HTML
        )
        return EDIT_SUB_NEW_NAME

    if old_name not in subs:
        await update.message.reply_text(
            f"⚠️ <b>{html.escape(old_name)}</b> is no longer in "
            f"{html.escape(batch)}. Nothing was changed.",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    subs[subs.index(old_name)] = new_name   # preserve position
    save_db()

    await update.message.reply_text(
        f"✅ <b>RENAMED</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{html.escape(old_name)}\n"
        f"➡️ <b>{html.escape(new_name)}</b>\n\n"
        f"<i>Saved. Existing scheduled classes keep their old label.</i>",
        parse_mode=ParseMode.HTML
    )
    return ConversationHandler.END

# ==============================================================================
# 🛠️ ADMIN COMMAND SHORTCUTS
# ==============================================================================
async def admin_command(update, context):
    """Show admin tools keyboard"""
    if not await require_private_admin(update, context): return
    await update.message.reply_text(
        "🛠️ <b>ADMIN TOOLS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Select an option from the keyboard below:</i> 👇",
        reply_markup=get_main_keyboard(),
        parse_mode=ParseMode.HTML
    )

async def schedule_command(update, context):
    """Quick access to view schedule"""
    if not await require_private_admin(update, context): return
    await view_schedule_handler(update, context)

async def export_command(update, context):
    """Quick access to export data"""
    if not await require_super_admin(update, context, action="export_denied"): return
    await export_data(update, context)

async def subjects_command(update, context):
    """Quick access to view subjects"""
    if not await require_private_admin(update, context): return
    await view_all_subjects(update, context)

async def attendance_command(update, context):
    """Quick access to attendance stats"""
    if not await require_private_admin(update, context): return
    await view_attendance_stats(update, context)

# ==============================================================================
# ⚠️ GLOBAL ERROR HANDLER
# ==============================================================================
# Dedup window for owner error alerts, so a tight failure loop can't spam the DM.
ERROR_ALERT_COOLDOWN = 300
_error_alert_seen = {}

# Transport-level noise that is not a bug and needs no owner DM.
#
#   Conflict   — another getUpdates poller is live. Normal for the few seconds a
#                redeploy overlaps the old instance. It is raised once per
#                running process, and each process has its own dedup table,
#                which is exactly why this arrived as two identical DMs.
#   TimedOut   — a request exceeded its deadline; PTB retries.
#   RetryAfter — Telegram asked us to back off; PTB honours it.
#
# NetworkError is matched by exact type, NOT isinstance: BadRequest subclasses
# it, and a BadRequest (malformed HTML, bad chat_id) is a genuine bug worth a DM.
BENIGN_ERRORS = (Conflict, TimedOut, RetryAfter)


def _is_benign_error(err):
    return isinstance(err, BENIGN_ERRORS) or type(err) is NetworkError


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Log errors, alert the owner privately, keep details out of public chats"""
    if _is_benign_error(context.error):
        # Warning, not error, and no traceback: this is operational noise. It
        # stays in the logs so a persistent conflict is still diagnosable.
        logger.warning("Transient Telegram error (not reported): %s: %s",
                       type(context.error).__name__, context.error)
        return

    # Log the full traceback. Logging only str(error) gave messages like
    # "'NoneType' object has no attribute 'get'" with no indication of where.
    logger.error(
        "Exception: %s\n%s",
        context.error,
        "".join(traceback.format_exception(type(context.error), context.error,
                                          context.error.__traceback__))
        if context.error else "(no traceback)"
    )
    
    error_msg = safe_text(context.error, "Unknown error")

    # Push the detail to the owner instead of the chat, deduplicated so a
    # repeating failure can't flood the DM.
    try:
        sig = error_msg[:120]
        now = time.time()
        if now - _error_alert_seen.get(sig, 0) > ERROR_ALERT_COOLDOWN:
            _error_alert_seen[sig] = now
            if len(_error_alert_seen) > 200:
                _error_alert_seen.clear()
            await notify_owner(
                context,
                "🐞 <b>BOT ERROR</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"<code>{html.escape(error_msg[:400])}</code>"
            )
    except Exception as e:
        logger.error(f"Could not alert owner about error: {e}")

    # Try to notify the user if possible
    if update and hasattr(update, 'effective_message') and update.effective_message:
        # Exception text routinely embeds the Supabase project URL, table names
        # and request internals. Only an admin in a DM sees the detail; everyone
        # else gets a generic line, and groups get nothing at all.
        user = getattr(update, "effective_user", None)
        chat = getattr(update, "effective_chat", None)
        in_private = bool(chat and chat.type == "private")
        detail_ok = in_private and is_admin(getattr(user, "username", None))

        if not detail_ok:
            if in_private:
                try:
                    await update.effective_message.reply_text(
                        "⚠️ <i>Something went wrong. Try again later.</i>",
                        parse_mode=ParseMode.HTML
                    )
                except Exception:
                    pass
            return

        if "Conflict" in error_msg:
            notice = (
                "⚠️ <b>BOT CONFLICT DETECTED!</b>\n\n"
                "<i>Multiple bot instances are running.</i>\n\n"
                "🔧 <b>Quick Fix:</b> Use /reset\n"
                "🛡️ <b>Permanent Fix:</b> Revoke token via @BotFather"
            )
        elif "Button_data_invalid" in error_msg:
            notice = (
                "⚠️ <b>BUTTON ERROR!</b>\n\n"
                "<i>Some buttons have expired data.</i>\n\n"
                "🔧 <b>Fix:</b> Use /reset to clear old jobs"
            )
        else:
            # Escape the error text. Interpolating it raw into <code> meant an
            # error containing '<' or '&' failed to send, hiding the real cause.
            notice = (
                f"❌ <b>AN ERROR OCCURRED</b>\n\n"
                f"<code>{html.escape(error_msg[:200])}</code>\n\n"
                f"<i>Try /reset if issues persist.</i>"
            )

        # Never let the notifier itself raise — that would re-enter the handler.
        try:
            await update.effective_message.reply_text(
                notice, parse_mode=ParseMode.HTML
            )
        except Exception as notify_err:
            logger.error(f"Could not deliver error notice: {notify_err}")

# ==============================================================================
# 🚀 16. MAIN
# ==============================================================================
async def post_init(app):
    # Group commands - feedback + updategroup for admins
    # Deliberately NOT advertising /login here: it takes a plaintext password and
    # must never be typed into a group.
    group_commands = [
        # Set-the-class-topic has to be typed in the group, inside the target
        # topic, so it is the one admin command advertised there.
        BotCommand("classtopic", "#️⃣ Set Topic for Class Alerts (Admin)"),
        BotCommand("feedback", "💬 Send Feedback to Vasuki Bot"),
        BotCommand("updategroup", "🔄 Update Group Link (Admin)"),
    ]
    
    # Private chat commands - all commands including admin tools
    private_commands = [
        BotCommand("start", "🏠 Open Dashboard"),
        BotCommand("admin", "🛠️ Admin Tools"),
        BotCommand("schedule", "📅 View Schedule"),
        BotCommand("subjects", "📚 All Subjects"),
        BotCommand("addsubject", "➕ Add Subject"),
        BotCommand("editsubject", "✏️ Edit Subjects"),
        BotCommand("topics", "💬 View Topics"),
        BotCommand("classtopic", "#️⃣ Class Alert Topic"),
        BotCommand("edittopic", "✏️ Edit Topic"),
        BotCommand("deletetopic", "🗑️ Delete Topic"),
        BotCommand("verifytopics", "🔄 Verify Topics"),
        BotCommand("attendance", "📊 Attendance Report"),
        BotCommand("refresh", "🔄 Refresh Database"),
        BotCommand("export", "📤 Export Data"),
        BotCommand("reset", "🔄 Reset & Fix Issues"),
        BotCommand("manualrestart", "♻️ Safe Restart"),
        BotCommand("updategroup", "🔄 Update Group Link"),
        BotCommand("resetdatabase", "🧨 Factory Reset"),
        BotCommand("feedback", "💬 Send Feedback"),
        BotCommand("viewattendance", "📊 View Attendance"), # Alias added
        BotCommand("preview", "👁 Preview Class Alerts"),
        BotCommand("auditlog", "🧾 Security Audit Log"),
    ]

    
    # Set commands for private chats (admins use all features here)
    await app.bot.set_my_commands(
        private_commands,
        scope=BotCommandScopeAllPrivateChats()
    )
    
    # Set commands for groups (only feedback available)
    await app.bot.set_my_commands(
        group_commands,
        scope=BotCommandScopeAllGroupChats()
    )
    
    # A public-tier database key is a standing takeover risk, so say so on every
    # boot until it's fixed rather than burying it in the logs.
    if SUPABASE_KEY_IS_PUBLIC:
        await notify_owner(
            app,
            "🚨 <b>SUPABASE KEY IS PUBLIC-TIER</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<code>SUPABASE_KEY</code> <i>is an anon/publishable key. That key is "
            "designed to be handed out publicly, so it can't be the thing "
            "protecting</i> <code>bot_storage</code>.\n\n"
            "<i>With RLS off, anyone holding it can rewrite the database — "
            "including the admin list.</i>\n\n"
            "🔧 <b>Fix:</b> switch <code>SUPABASE_KEY</code> to the "
            "<b>service_role</b> key, then enable RLS on the table."
        )

    # Restore scheduled jobs from database
    await restore_jobs(app)
    cleanup_old_data()
    
    # Schedule periodic cleanup (every 24 hours)
    async def scheduled_cleanup(context):
        cleanup_old_data(context)
    
    app.job_queue.run_repeating(scheduled_cleanup, interval=86400, first=86400)
    
    # Schedule Smart Keep-Alive (Ping every 5 mins - 24/7 to prevent Render spin-down)
    async def smart_ping(context):
        try:
            import httpx
            port = int(os.environ.get("PORT", 8080))
            
            # Build URL list: internal + external + health endpoint
            render_url = os.environ.get("RENDER_EXTERNAL_URL")
            urls = [f"http://127.0.0.1:{port}/health"]
            if render_url:
                # Ping both root and health endpoint externally
                urls.append(render_url.rstrip('/') + '/health')
                urls.append(render_url)  # Root endpoint too
            
            async with httpx.AsyncClient(timeout=15) as client:
                for url in urls:
                    try:
                        resp = await client.get(url)
                        logger.info(f"🔔 Ping OK: {url} ({resp.status_code})")
                    except Exception as inner_e:
                        logger.warning(f"⚠️ Ping failed for {url}: {inner_e}")

        except Exception as e:
            logger.warning(f"⚠️ Keep-alive mechanism error: {e}")

    # 5 minutes = 300 seconds  
    app.job_queue.run_repeating(smart_ping, interval=300, first=60)
    
    # ──────────────────────────────────────────────────────────────────────
    # 🛡️ SUPABASE KEEP-ALIVE (Ping every 5 days to prevent 7-day pause)
    # ──────────────────────────────────────────────────────────────────────
    async def supabase_keepalive(context):
        """Ping Supabase with a lightweight query every 5 days to prevent auto-pause.
        Supabase free tier pauses after 7 days of inactivity.
        Running this every 5 days (432000 seconds) keeps it alive."""
        if not supabase:
            logger.warning("⚠️ Supabase keepalive skipped - no connection")
            return
        try:
            # Lightweight SELECT to keep the project active
            response = supabase.table("bot_storage").select("id").eq("id", 1).limit(1).execute()
            logger.info(f"✅ Supabase keepalive ping successful at {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')} - rows: {len(response.data)}")
        except Exception as e:
            logger.error(f"❌ Supabase keepalive ping FAILED: {e}")
            # Retry after 1 hour if first attempt fails
            try:
                await asyncio.sleep(3600)
                response = supabase.table("bot_storage").select("id").eq("id", 1).limit(1).execute()
                logger.info(f"✅ Supabase keepalive retry successful")
            except Exception as retry_e:
                logger.error(f"❌ Supabase keepalive retry also FAILED: {retry_e}")
    
    # 5 days = 432000 seconds. First ping after 1 hour (3600s) to confirm it works.
    app.job_queue.run_repeating(supabase_keepalive, interval=432000, first=3600)
    
    # Memory monitor - Multi-stage protection against OOM kills
    async def memory_monitor(context):
        try:
            import psutil
            import gc
            process = psutil.Process()
            mem_mb = process.memory_info().rss / (1024 * 1024)
            mem_percent = (mem_mb / 512) * 100
            
            # ── STAGE 1: Warning at 70% (358 MB) ──
            if mem_percent >= 70 and mem_percent < 80:
                logger.warning(f"⚠️ Memory at {mem_percent:.1f}% ({mem_mb:.1f} MB) - monitoring closely")
            
            # ── STAGE 2: Aggressive cleanup at 80% (410 MB) ──
            elif mem_percent >= 80 and mem_percent < 90:
                logger.warning(f"🔶 Memory HIGH at {mem_percent:.1f}% ({mem_mb:.1f} MB) - running emergency cleanup")
                
                # Force garbage collection
                gc.collect()
                
                # Clean old data aggressively
                cleanup_old_data()
                
                # Clear user_data caches from all conversations
                if hasattr(context, 'application') and context.application:
                    try:
                        context.application.user_data.clear()
                        context.application.chat_data.clear()
                        logger.info("🧹 Cleared user_data and chat_data caches")
                    except:
                        pass
                
                # Force another gc pass
                gc.collect()
                
                # Check if cleanup helped
                mem_after = process.memory_info().rss / (1024 * 1024)
                logger.info(f"📊 Memory after cleanup: {mem_after:.1f} MB (freed {mem_mb - mem_after:.1f} MB)")
            
            # ── STAGE 3: Emergency auto-restart at 90% (460 MB) ──
            elif mem_percent >= 90:
                logger.critical(f"🔴 CRITICAL MEMORY: {mem_percent:.1f}% ({mem_mb:.1f} MB) - AUTO-RESTART!")
                
                # Save database before restart to prevent data loss
                try:
                    if supabase:
                        loop = asyncio.get_event_loop()
                        if await loop.run_in_executor(None, flush_db_sync):
                            logger.info("✅ Emergency DB save completed before auto-restart")
                        else:
                            logger.error("❌ Emergency save failed after retries")
                except Exception as save_err:
                    logger.error(f"❌ Emergency save failed: {save_err}")
                
                # Graceful exit - Render will auto-restart the process
                # All scheduled jobs will be restored from Supabase on restart
                import sys
                logger.info("🔄 Initiating auto-restart to prevent OOM crash...")
                await asyncio.sleep(2)  # Give logs time to flush
                sys.exit(0)
                
        except ImportError:
            pass  # psutil not installed
        except SystemExit:
            raise  # Don't catch sys.exit()
        except Exception as e:
            logger.error(f"Memory monitor error: {e}")
    
    # Check memory every 3 minutes (more frequent for faster response)
    app.job_queue.run_repeating(memory_monitor, interval=180, first=120)

    logger.info("✅ Vasuki Bot initialized successfully")

def _preview_subjects():
    """Real subjects from the DB, so previews match what students will see."""
    _ensure_db_shape()
    out = []
    for batch in ("CSDA", "AICS"):
        for s in DB["subjects"].get(batch, []):
            out.append((batch, s))
    # Combined-batch variant, which is how most classes actually go out
    combined = [("CSDA & AICS", s) for _, s in out]
    pool = out + combined
    if not pool:
        pool = [("CSDA & AICS", "CDA/ACS 201 : Statistics For Data Science")]
    return pool


async def preview_command(update, context):
    """
    Send real sample notifications so you can see exactly how Telegram renders
    them. Nothing is scheduled and no attendance is recorded.
    """
    if not await require_private_admin(update, context): return

    import random as _r
    pool = _preview_subjects()
    now = datetime.now(IST)

    # Three timing states so the countdown line can be seen in each form
    offsets = [10, 5, 0]
    _r.shuffle(offsets)

    await update.message.reply_text(
        "👁 <b>PREVIEW</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Below is exactly how the next class alerts will appear in the "
        "group — rendered by Telegram itself, not an approximation.</i>\n\n"
        "<i>Nothing is scheduled and no attendance is recorded.</i>",
        parse_mode=ParseMode.HTML
    )

    for off in offsets:
        batch, subject = _r.choice(pool)
        t_str = (now + timedelta(minutes=off)).strftime('%H:%M')
        text = _generate_class_notification(
            batch, subject, t_str, "https://meet.google.com/abc-defg-hij")
        text = sanitize_html(text)
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Mark me present", callback_data="previewatt")
        ]])
        try:
            await send_message_safe(
                context.bot, update.effective_chat.id, safe_text(text),
                parse_mode=ParseMode.HTML, reply_markup=kb,
                disable_web_page_preview=True
            )
        except Exception as e:
            logger.error(f"Preview send failed: {e}")
            await update.message.reply_text(
                f"⚠️ <i>One preview failed to render:</i> "
                f"<code>{html.escape(str(e)[:120])}</code>",
                parse_mode=ParseMode.HTML
            )
        await asyncio.sleep(0.25)   # stay under Telegram's rate limit

    await update.message.reply_text(
        "<i>Each alert uses a different design, countdown and wording — "
        "so consecutive classes never look the same.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Show 3 more", callback_data="preview_more")
        ]])
    )


async def preview_more_callback(update, context):
    """Regenerate previews from the inline button."""
    query = update.callback_query
    await query.answer("Generating…")
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    # Reuse the command path; it only needs .message and .effective_chat
    fake = type("U", (), {
        "message": query.message,
        "effective_chat": query.message.chat,
        "effective_user": query.from_user,
    })()
    await preview_command(fake, context)


async def preview_att_noop(update, context):
    """The preview's attendance button must never record anything."""
    await update.callback_query.answer(
        "👁 Preview only — no attendance recorded.", show_alert=True)


async def stats_command(update, context):
    """Show system statistics (memory, uptime) - Admin Private Only"""
    if not await require_private_admin(update, context): return
    
    # Calculate Uptime
    uptime = datetime.now(IST) - START_TIME
    uptime_str = str(uptime).split('.')[0] # Remove microseconds

    # Memory
    mem_mb = 0
    try:
        import psutil
        process = psutil.Process()
        mem_mb = process.memory_info().rss / (1024 * 1024)
    except:
        pass
    
    # Live DB size — the multiplier on every cloud write
    try:
        db_kb = len(json.dumps(DB).encode()) / 1024
        db_str = f"{db_kb/1024:.2f} MB" if db_kb > 1024 else f"{db_kb:.0f} KB"
    except Exception:
        db_str = "n/a"

    pct = (mem_mb / 512 * 100) if mem_mb else 0
    bar_len = 14
    filled = min(bar_len, int(pct / 100 * bar_len))
    bar = "█" * filled + "░" * (bar_len - filled)

    req = _save_stats["requested"]
    written = _save_stats["written"]
    saved_writes = max(0, req - written)

    msg = (
        f"📊 <b>SYSTEM STATISTICS</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🧠 <b>Memory:</b> {mem_mb:.1f} MB / 512 MB ({pct:.0f}%)\n"
        f"<code>{bar}</code>\n\n"
        f"⏱️ <b>Uptime:</b> {uptime_str}\n"
        f"📅 <b>Pending Jobs:</b> {len(DB.get('active_jobs', []))}\n"
        f"📦 <b>DB Size:</b> {db_str}\n"
        f"📋 <b>Attendance Records:</b> {len(DB.get('attendance', {}))}\n"
        f"💾 <b>Storage:</b> {'☁️ Supabase' if supabase else '💻 Local'}\n"
        f"🔑 <b>DB key tier:</b> "
        f"{'🚨 anon/public — SWITCH TO service_role' if SUPABASE_KEY_IS_PUBLIC else html.escape(SUPABASE_KEY_ROLE or 'unknown')}\n\n"
        f"☁️ <b>CLOUD WRITER</b>\n"
        f"┣ Requests: <code>{req}</code>\n"
        f"┣ Actual writes: <code>{written}</code>\n"
        f"┣ Writes avoided: <code>{saved_writes}</code>\n"
        f"┗ Failed cycles: <code>{_save_stats['failed']}</code>"
    )
    if _save_stats["last_error"]:
        msg += f"\n\n⚠️ <i>Last error:</i> <code>{html.escape(str(_save_stats['last_error'])[:90])}</code>"
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def manual_restart_command(update, context):
    """Admin command to safely restart the bot - preserves all schedules"""
    # Kills the process (Render restarts it), so this is a downtime switch.
    if not await require_super_admin(update, context, action="restart_denied"): return
    audit("manual_restart", update.effective_user, "process exit requested")
    
    await update.message.reply_text(
        "🔄 <b>MANUAL RESTART</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "💾 Saving database to cloud...",
        parse_mode=ParseMode.HTML
    )
    
    # Force save to Supabase
    save_db()
    
    # Get memory info
    try:
        import psutil
        process = psutil.Process()
        mem_mb = process.memory_info().rss / (1024 * 1024)
    except:
        mem_mb = 0
    
    await update.message.reply_text(
        f"✅ <b>DATABASE SAVED!</b>\n\n"
        f"📊 Memory before restart: {mem_mb:.1f} MB\n"
        f"📅 Active schedules: {len(DB.get('active_jobs', []))}\n\n"
        f"🔄 <i>Restarting in 3 seconds...</i>\n"
        f"<i>All schedules will be restored automatically.</i>",
        parse_mode=ParseMode.HTML
    )
    
    # Wait a bit for message to send
    await asyncio.sleep(3)
    
    logger.info("🔄 Manual restart triggered by admin")
    
    # Exit gracefully - Render will auto-restart
    import sys
    sys.exit(0)

async def auditlog_command(update, context):
    """Show the security audit trail - super admin, private chat only."""
    if not await require_super_admin(update, context, action="auditlog_denied"): return

    entries = DB.get("audit_log", [])
    if not entries:
        await update.message.reply_text(
            "🧾 <b>AUDIT LOG EMPTY</b>\n\n"
            "<i>No privileged actions recorded yet.</i>",
            parse_mode=ParseMode.HTML
        )
        return

    # Optional count argument: /auditlog 40
    limit = 20
    if context.args:
        try:
            limit = max(1, min(80, int(context.args[0])))
        except ValueError:
            pass

    recent = entries[-limit:][::-1]
    msg = ["🧾 <b>SECURITY AUDIT LOG</b>", "━" * 24, ""]
    for e in recent:
        if not isinstance(e, dict):
            continue
        mark = "✅" if e.get("ok", True) else "⛔"
        who = e.get("username")
        who = f"@{html.escape(who)}" if who else "<i>no handle</i>"
        msg.append(
            f"{mark} <b>{html.escape(safe_text(e.get('action'), '?'))}</b>\n"
            f"   🕒 {html.escape(safe_text(e.get('ts'), '?'))}\n"
            f"   👤 {who} · <code>{html.escape(str(e.get('user_id')))}</code>"
            + (f"\n   📝 <i>{html.escape(safe_text(e.get('details'), ''))}</i>"
               if e.get("details") else "")
        )
        msg.append("")
    msg.append("━" * 24)
    msg.append(f"<i>Showing {len(recent)} of {len(entries)} entries "
               f"(kept: last {AUDIT_LOG_MAX}).</i>")

    await send_message_safe(context.bot, update.effective_chat.id,
                            safe_text("\n".join(msg)),
                            parse_mode=ParseMode.HTML,
                            disable_web_page_preview=True)

# ------------------------------------------------------------------------------
# 🔑 LOGIN THROTTLE
# ------------------------------------------------------------------------------
# /login used to be an unthrottled password oracle: a single account could try
# ADMIN_PASSWORD as fast as Telegram allowed, forever, with nothing logged. Now
# every failure is counted per user, a burst locks the account out, and the owner
# is told. State is in-memory on purpose — a restart costs an attacker more than
# it costs us, and it keeps brute-force noise out of the database.
LOGIN_MAX_FAILS = 5          # failures allowed inside the window
LOGIN_FAIL_WINDOW = 900      # 15 minutes
LOGIN_LOCKOUT = 3600         # 1 hour lockout once tripped
_login_fails = {}            # user_id -> deque of failure timestamps
_login_locked = {}           # user_id -> unix ts the lockout expires


def _login_lock_remaining(user_id):
    """Seconds left on this user's lockout, or 0."""
    until = _login_locked.get(user_id, 0)
    remaining = int(until - time.time())
    if remaining <= 0:
        _login_locked.pop(user_id, None)
        return 0
    return remaining


def _record_login_failure(user_id):
    """Count a failure. Returns True if this one triggered a lockout."""
    now = time.time()
    fails = _login_fails.setdefault(user_id, deque(maxlen=LOGIN_MAX_FAILS * 2))
    while fails and now - fails[0] > LOGIN_FAIL_WINDOW:
        fails.popleft()
    fails.append(now)
    if len(_login_fails) > 500:      # keep the dict from growing unbounded
        for k in [k for k, v in _login_fails.items()
                  if not v or now - v[-1] > LOGIN_FAIL_WINDOW]:
            _login_fails.pop(k, None)
    if len(fails) >= LOGIN_MAX_FAILS:
        _login_locked[user_id] = now + LOGIN_LOCKOUT
        fails.clear()
        return True
    return False


async def login_command(update, context):
    """Allow users to gain admin access via password. Private chat only."""
    user = update.effective_user
    args = context.args
    uid = getattr(user, "id", 0)

    # The password is plaintext in the message. In a group that publishes it to
    # every member and to the chat history, so refuse and scrub it.
    if not is_private_chat(update):
        try:
            await update.message.delete()
        except Exception:
            pass
        audit("login_in_group", user, "attempted /login in a group", ok=False)
        await notify_owner(
            context,
            "🚨 <b>SECURITY: /login USED IN A GROUP</b>\n"
            f"👤 {user_tag(user)}\n"
            f"📍 {html.escape(safe_text(update.effective_chat.title, 'group'))}\n\n"
            "<i>The message was deleted, but anyone watching the chat may have "
            "seen the password. Rotate ADMIN_PASSWORD.</i>"
        )
        try:
            await context.bot.send_message(
                uid,
                "⚠️ <b>NEVER SEND /login IN A GROUP</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "<i>Your message was deleted, but the password may already be "
                "compromised. Use</i> <code>/login [password]</code> <i>here in "
                "DM only.</i>",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
        return

    if not ADMIN_PASSWORD:
        await update.message.reply_text("❌ <b>LOGIN DISABLED</b>\nNo password configured in settings.", parse_mode=ParseMode.HTML)
        return

    if is_admin(getattr(user, "username", None)):
        await update.message.reply_text(
            "✅ <b>ALREADY LOGGED IN</b>\n\n<i>Type /start.</i>",
            parse_mode=ParseMode.HTML
        )
        return

    locked = _login_lock_remaining(uid)
    if locked:
        reply = (
            "🚫 <b>TOO MANY ATTEMPTS</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<i>Locked for {locked // 60} min {locked % 60} sec.</i>"
        )
        await update.message.reply_text(reply, parse_mode=ParseMode.HTML)
        await mirror_non_admin(context, update, bot_reply=reply,
                               event="/login while locked out")
        return

    if not args:
        await update.message.reply_text("🔑 <b>ADMIN LOGIN</b>\n\nUsage: <code>/login [password]</code>", parse_mode=ParseMode.HTML)
        return

    # compare_digest instead of '==' so the comparison doesn't short-circuit on
    # the first wrong byte.
    if hmac.compare_digest(str(args[0]), str(ADMIN_PASSWORD)):
        _login_fails.pop(uid, None)
        _login_locked.pop(uid, None)

        username = _norm_username(getattr(user, "username", None))
        if not username:
            # Authorisation is keyed on the handle, so a user without one cannot
            # be granted (or later revoked) reliably.
            audit("login_no_username", user, "correct password, no @username", ok=False)
            await update.message.reply_text(
                "⚠️ <b>SET A USERNAME FIRST</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "<i>Admin access is tied to your @username. Set one in Telegram "
                "settings, then run /login again.</i>",
                parse_mode=ParseMode.HTML
            )
            return

        if username not in DB.setdefault("admins", []):
            DB["admins"].append(username)
            DB["admins"] = sorted(set(DB["admins"]))
            save_db()

        audit("login_success", user, "granted admin via password")
        await notify_owner(
            context,
            "🔓 <b>NEW ADMIN VIA /login</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 {user_tag(user)}\n"
            f"🆔 <code>{uid}</code>\n"
            f"🕒 {datetime.now(IST).strftime('%d %b %Y, %H:%M:%S IST')}\n\n"
            "<i>Remove them with 🗑️ Remove Admin if this wasn't expected.</i>"
        )

        await update.message.reply_text(
            "✅ <b>ACCESS GRANTED</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<b>{html.escape(safe_text(user.first_name, 'Admin'))}</b>, you are now authenticated.\n"
            "<i>Use /start to open the admin dashboard.</i>",
            parse_mode=ParseMode.HTML
        )
        return

    # ---- Wrong password ----
    tripped = _record_login_failure(uid)
    audit("login_failed", user, "incorrect password", ok=False)

    if tripped:
        reply = (
            "🚫 <b>TOO MANY FAILED ATTEMPTS</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<i>Locked out for {LOGIN_LOCKOUT // 60} minutes.</i>"
        )
        await notify_owner(
            context,
            "🚨 <b>BRUTE FORCE ON /login</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 {user_tag(user)}\n"
            f"🆔 <code>{uid}</code>\n"
            f"❌ {LOGIN_MAX_FAILS} failed attempts — locked out for "
            f"{LOGIN_LOCKOUT // 60} min.\n\n"
            "<i>Consider rotating ADMIN_PASSWORD.</i>"
        )
    else:
        left = LOGIN_MAX_FAILS - len(_login_fails.get(uid, []))
        reply = (
            "⛔ <b>ACCESS DENIED</b>\n"
            f"<i>Incorrect password. {left} attempt(s) left.</i>"
        )

    await update.message.reply_text(reply, parse_mode=ParseMode.HTML)
    await mirror_non_admin(context, update, bot_reply=reply,
                           event="failed /login attempt")

# ==============================================================================
# 🚧 CATCH-ALL GUARDS (non-admins poking the bot)
# ==============================================================================
def _command_name(message, bot_username=None):
    """Return the bare command name, or '' if it isn't ours / isn't a command."""
    text = (message.text or "").strip()
    if not text.startswith("/"):
        return ""
    token = text.split()[0][1:]
    if "@" in token:
        name, _, target = token.partition("@")
        # /cmd@SomeOtherBot is not our business
        if bot_username and target.lower() != bot_username.lower():
            return ""
        return name.lower()
    return token.lower()

async def guard_unknown_command(update, context):
    """
    Last handler for anything command-shaped that no other handler claimed.
    Non-admins get the access-denied reply (cleaned up in groups); admins get
    a short "no such command" nudge.
    """
    try:
        message = update.effective_message
        user = update.effective_user
        if not message:
            return

        cmd = _command_name(message, getattr(context.bot, "username", None))
        if not cmd:
            return

        if cmd in PUBLIC_COMMANDS:
            # Claimed by a real handler elsewhere; nothing to report here.
            return

        if is_admin(user.username if user else None):
            if is_private_chat(update):
                await message.reply_text(
                    "🤔 <b>Unknown command</b>\n\n"
                    "<i>Send /start and pick an action from the menu.</i>",
                    parse_mode=ParseMode.HTML
                )
            return

        await deny_access(update, context, event=f"tried /{cmd}")
    except Exception as e:
        logger.error(f"Error in guard_unknown_command: {e}")

async def guard_non_admin_dm(update, context):
    """Non-admin sending random text to the bot in DM — reply, rate limited."""
    try:
        user = update.effective_user
        if is_admin(user.username if user else None):
            return

        key = ("dm", user.id if user else 0)
        now = time.time()
        if now - _DENY_COOLDOWN.get(key, 0) < 12:
            # Reply is suppressed to avoid spamming them, but the owner still
            # gets to see everything a non-admin sends.
            await mirror_non_admin(
                context, update, bot_reply=None,
                event="DM to the bot (no reply — cooldown)")
            return
        _DENY_COOLDOWN[key] = now

        await deny_access(update, context, event="DM to the bot")
    except Exception as e:
        logger.error(f"Error in guard_non_admin_dm: {e}")


async def guard_non_admin_media(update, context):
    """
    Non-text DM (photo, document, voice, sticker...) from a non-admin.

    Without this, media from a non-admin hit no handler at all: no reply, and
    nothing reported to the owner.
    """
    try:
        user = update.effective_user
        if is_admin(user.username if user else None):
            return
        await mirror_non_admin(context, update, bot_reply=None,
                              event="sent media to the bot (ignored)")
    except Exception as e:
        logger.error(f"Error in guard_non_admin_media: {e}")

def main():
    keep_alive()
    # Reduced connection pool: 8 → 4 (saves ~20-30MB memory)
    request = HTTPXRequest(connection_pool_size=4, connect_timeout=60.0, read_timeout=60.0)
    app = Application.builder().token(TOKEN).request(request).defaults(Defaults(tzinfo=IST)).post_init(post_init).build()

    # Command Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("login", login_command))  # Added login command
    app.add_handler(CommandHandler("feedback", feedback_handler))
    app.add_handler(CommandHandler("viewfeedback", viewfeedback_handler))  # Admin view feedback
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("refresh", refresh_db_command))  # Live DB refresh
    app.add_handler(CommandHandler("manualrestart", manual_restart_command))  # Safe restart
    app.add_handler(CommandHandler("stats", stats_command))  # Memory stats
    app.add_handler(CommandHandler("auditlog", auditlog_command))  # Security trail
    app.add_handler(CommandHandler("updategroup", updategroup_command))  # Fix group ID issues
    app.add_handler(MessageHandler(filters.Regex("^🔄 Reset System"), reset_command)) # Added button handler

    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("schedule", schedule_command))
    app.add_handler(CommandHandler("export", export_command))
    app.add_handler(CommandHandler("subjects", subjects_command))
    app.add_handler(CommandHandler("attendance", attendance_command))
    app.add_handler(CommandHandler("viewattendance", attendance_command)) # Alias
    app.add_handler(CommandHandler("topic", register_topic_command))  # New topic command
    # Works in groups (to set) and DMs (to read) — no private-only gate.
    app.add_handler(CommandHandler("classtopic", classtopic_command))
    app.add_handler(CommandHandler("verifytopics", verify_topics_command))  # Verify topics
    app.add_handler(CallbackQueryHandler(verify_topics_callback, pattern="^verify_topics$"))
    app.add_handler(ChatMemberHandler(track_chats, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.StatusUpdate.FORUM_TOPIC_CREATED, auto_register_topic)) # Auto-register
    
    app.add_handler(MessageHandler(filters.Regex("^📂 More Options"), handle_navigation))
    app.add_handler(MessageHandler(filters.Regex("^🔙 Back"), handle_navigation))
    app.add_handler(MessageHandler(filters.Regex("^📤 Export"), export_data))
    app.add_handler(MessageHandler(filters.Regex("^📥 Import"), import_request))
    app.add_handler(MessageHandler(filters.Document.MimeType("application/json") & filters.ChatType.PRIVATE, handle_import_file))
    app.add_handler(MessageHandler(filters.Regex("^🗑️ Delete Class"), delete_menu))
    # Order matters: '^del_scope_' must be tried before the broader '^del_'.
    app.add_handler(CallbackQueryHandler(delete_scope_handler, pattern="^del_scope_"))
    app.add_handler(CallbackQueryHandler(delete_nav, pattern="^(del_|kill_)"))
    
    # NEW: Added View All Subjects Handler
    app.add_handler(MessageHandler(filters.Regex("^📚 All Subjects"), view_all_subjects))
    app.add_handler(MessageHandler(filters.Regex("^🛠️ Admin Tools"), admin_command))
    app.add_handler(MessageHandler(filters.Regex("^☁️ Force Save"), force_cloud_save))

    app.add_handler(CommandHandler("cancelled", cancelled_command))
    app.add_handler(MessageHandler(filters.Regex("^☁️ Force Save"), force_cloud_save))
    app.add_handler(MessageHandler(filters.Regex("^📸 AI Auto-Schedule"), prompt_image_upload)) 
    app.add_handler(MessageHandler(filters.Regex("^📊 Attendance"), view_attendance_stats)) 
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, handle_photo))
    app.add_handler(MessageHandler(filters.Regex("^📅 View Schedule"), view_schedule_handler))
    app.add_handler(CallbackQueryHandler(schedule_nav, pattern="^(sch_|schedule_page_)"))
    app.add_handler(CallbackQueryHandler(mark_attendance, pattern="^att_"))
    # Preview: registered before the catch-all so it isn't treated as expired.
    app.add_handler(CommandHandler("preview", preview_command))
    app.add_handler(CallbackQueryHandler(preview_more_callback, pattern="^preview_more$"))
    app.add_handler(CallbackQueryHandler(preview_att_noop, pattern="^previewatt$"))
    app.add_handler(CallbackQueryHandler(verify_topics_callback, pattern="^verify_page_")) # Pagination


    txt_filter = filters.TEXT & ~filters.Regex(MENU_REGEX)

    app.add_handler(ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^➕ Add Subject"), start_add_sub),
            CommandHandler("addsubject", start_add_sub),
        ],
        states={
            SELECT_BATCH: [CallbackQueryHandler(save_batch_for_sub, pattern="^sub_")],
            NEW_SUBJECT_INPUT: [MessageHandler(txt_filter, save_new_sub)]
        },
        fallbacks=[
            CommandHandler("cancel", cancel_wizard),
            MessageHandler(filters.Regex(MENU_REGEX), cancel_wizard),
        ],
        conversation_timeout=300
    ))

    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^✏️ Edit Class"), start_edit)],
        states={
            # One router serves all three browser levels, because back-navigation
            # moves between them in both directions.
            EDIT_SELECT_BATCH: [CallbackQueryHandler(edit_nav, pattern="^edit_")],
            EDIT_SELECT_SUBJECT: [CallbackQueryHandler(edit_nav, pattern="^edit_")],
            EDIT_SELECT_JOB: [CallbackQueryHandler(edit_nav, pattern="^edit_")],
            EDIT_CHOOSE_FIELD: [CallbackQueryHandler(edit_choose_field, pattern="^field_")],
            EDIT_MSG_TYPE: [CallbackQueryHandler(edit_msg_type, pattern="^editmsg_(ai|manual|cancel)$")],
            EDIT_NEW_VALUE: [MessageHandler(txt_filter, edit_save)],
            EDIT_SELECT_SCOPE: [CallbackQueryHandler(edit_scope_handler, pattern="^scope_")]
        },
        fallbacks=[
            CommandHandler("cancel", cancel_wizard),
            MessageHandler(filters.Regex(MENU_REGEX), cancel_wizard),
        ],
        conversation_timeout=300
    ))

    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🧠 Custom AI"), start_gemini_tool)],
        states={GEMINI_PROMPT_INPUT: [MessageHandler(txt_filter, process_gemini_prompt)]},
        fallbacks=[MessageHandler(filters.Regex(MENU_REGEX), cancel_wizard)],
        conversation_timeout=300
    ))

    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🟦 Schedule CSDA$|^🟧 Schedule AICS$"), init_schedule_wizard)],
        states={
            SELECT_SUB_OR_ADD: [CallbackQueryHandler(wizard_pick_sub, pattern="^pick_")],
            SELECT_DAYS: [CallbackQueryHandler(wizard_toggle_days, pattern="^toggle_|^days_done")],
            INPUT_START_DATE: [MessageHandler(txt_filter, wizard_start_date)],
            INPUT_END_DATE: [MessageHandler(txt_filter, wizard_end_date)],
            INPUT_TIME: [MessageHandler(txt_filter, wizard_time)],
            INPUT_END_TIME: [MessageHandler(txt_filter, wizard_end_time)],
            INPUT_LINK: [MessageHandler(txt_filter, wizard_link)],
            SELECT_TOPIC: [CallbackQueryHandler(wizard_topic_selection, pattern="^topic_")],
            SELECT_OFFSET: [CallbackQueryHandler(wizard_offset, pattern="^offset_")],
            CUSTOM_OFFSET_INPUT: [MessageHandler(txt_filter, wizard_custom_offset)],
            MSG_TYPE_CHOICE: [CallbackQueryHandler(wizard_msg_choice, pattern="^msg_")],
            INPUT_MANUAL_MSG: [MessageHandler(txt_filter, wizard_manual_msg)]
        },
        fallbacks=[MessageHandler(filters.Regex(MENU_REGEX), cancel_wizard)],
        conversation_timeout=300
    ))

    # Combined Schedule (CSDA + AICS at once)
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📅 Schedule Classes$"), init_combined_schedule_wizard)],
        states={
            COMBINED_SELECT_SUB: [CallbackQueryHandler(combined_pick_sub, pattern="^cpick_")],
            SELECT_DAYS: [CallbackQueryHandler(wizard_toggle_days, pattern="^toggle_|^days_done")],
            INPUT_START_DATE: [MessageHandler(txt_filter, wizard_start_date)],
            INPUT_END_DATE: [MessageHandler(txt_filter, wizard_end_date)],
            INPUT_TIME: [MessageHandler(txt_filter, wizard_time)],
            INPUT_END_TIME: [MessageHandler(txt_filter, wizard_end_time)],
            INPUT_LINK: [MessageHandler(txt_filter, wizard_link)],
            SELECT_TOPIC: [CallbackQueryHandler(wizard_topic_selection, pattern="^topic_")],
            SELECT_OFFSET: [CallbackQueryHandler(wizard_offset, pattern="^offset_")],
            CUSTOM_OFFSET_INPUT: [MessageHandler(txt_filter, wizard_custom_offset)],
            MSG_TYPE_CHOICE: [CallbackQueryHandler(combined_wizard_msg_choice, pattern="^msg_")],
            INPUT_MANUAL_MSG: [MessageHandler(txt_filter, combined_wizard_manual_msg)]
        },
        fallbacks=[MessageHandler(filters.Regex(MENU_REGEX), cancel_wizard)],
        conversation_timeout=300
    ))

    # Custom Message Scheduler
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📝 Custom Message"), start_custom_msg)],
        states={
            CUSTOM_MSG_BATCH: [CallbackQueryHandler(cmsg_batch_selected, pattern="^cmsg_")],
            CUSTOM_MSG_DAYS: [CallbackQueryHandler(cmsg_toggle_days, pattern="^toggle_|^days_done")],
            CUSTOM_MSG_TIME: [MessageHandler(txt_filter, cmsg_time_input)],
            CUSTOM_MSG_START: [MessageHandler(txt_filter, cmsg_start_date)],
            CUSTOM_MSG_END: [MessageHandler(txt_filter, cmsg_end_date)],
            CUSTOM_MSG_TEXT: [MessageHandler(txt_filter, cmsg_text_input)],
            CUSTOM_MSG_LINK: [
                MessageHandler(txt_filter, cmsg_link_input),
                CallbackQueryHandler(cmsg_link_input, pattern="^cmsg_link_skip")
            ],
            SELECT_TOPIC: [CallbackQueryHandler(cmsg_topic_selection, pattern="^ctopic_")]
        },
        fallbacks=[MessageHandler(filters.Regex(MENU_REGEX), cancel_wizard)],
        conversation_timeout=300
    ))

    # Admin Management Handlers
    app.add_handler(MessageHandler(filters.Regex("^👥 Manage Admins"), handle_navigation))
    app.add_handler(MessageHandler(filters.Regex("^📋 View Admins"), view_admins))
    
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^👤 Add Admin"), start_add_admin)],
        states={ADD_ADMIN_INPUT: [MessageHandler(txt_filter, save_new_admin)]},
        fallbacks=[MessageHandler(filters.Regex(MENU_REGEX), cancel_wizard), CommandHandler("cancel", cancel_wizard)],
        conversation_timeout=300
    ))
    
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🗑️ Remove Admin"), start_remove_admin)],
        states={REMOVE_ADMIN_INPUT: [MessageHandler(txt_filter, remove_admin_save)]},
        fallbacks=[MessageHandler(filters.Regex(MENU_REGEX), cancel_wizard), CommandHandler("cancel", cancel_wizard)],
        conversation_timeout=300
    ))

    # Night Schedule Handler
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("Night Schedule"), start_night_schedule)],
        states={NIGHT_SCHEDULE_TIME: [MessageHandler(txt_filter, save_night_schedule_time)]},
        fallbacks=[MessageHandler(filters.Regex(MENU_REGEX), cancel_wizard), CommandHandler("cancel", cancel_wizard)],
        conversation_timeout=300
    ))

    # Topic Management Handlers
    app.add_handler(MessageHandler(filters.Regex("^💬 Manage Topics"), manage_topics_handler))
    app.add_handler(MessageHandler(filters.Regex("^📋 List Topics"), view_topics))

    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ Add Topic Manual"), start_add_topic)],
        states={
            ADD_TOPIC_NAME: [MessageHandler(txt_filter, save_topic_name)],
            ADD_TOPIC_ID: [MessageHandler(txt_filter, save_topic_id)]
        },
        fallbacks=[MessageHandler(filters.Regex(MENU_REGEX), cancel_wizard), CommandHandler("cancel", cancel_wizard)],
        conversation_timeout=300
    ))

    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🗑️ Remove Topic"), start_remove_topic)],
        states={REMOVE_TOPIC_INPUT: [MessageHandler(txt_filter, remove_topic_save)]},
        fallbacks=[MessageHandler(filters.Regex(MENU_REGEX), cancel_wizard), CommandHandler("cancel", cancel_wizard)],
        conversation_timeout=300
    ))

    # Edit Subject Handler
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("editsubject", start_edit_subject)],
        states={
            EDIT_SUB_SELECT_BATCH: [CallbackQueryHandler(edit_sub_select_batch, pattern="^esub_(batch_|cancel$)")],
            EDIT_SUB_SELECT_SUBJECT: [CallbackQueryHandler(edit_sub_select_subject, pattern="^esub_(pick_\\d+|cancel)$")],
            EDIT_SUB_ACTION: [CallbackQueryHandler(edit_sub_action, pattern="^esub_(rename|delete|cancel)$")],
            EDIT_SUB_NEW_NAME: [MessageHandler(txt_filter, edit_sub_save_rename)]
        },
        fallbacks=[CommandHandler("cancel", cancel_wizard), MessageHandler(filters.Regex(MENU_REGEX), cancel_wizard)],
        conversation_timeout=300
    ))

    # Reset Database Handler
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("resetdatabase", start_reset_db)],
        states={RESET_CONFIRM: [CallbackQueryHandler(confirm_reset_db, pattern="^reset_")]},
        fallbacks=[CommandHandler("cancel", cancel_wizard)],
        conversation_timeout=60
    ))

    # Topic Commands (/topics, /edittopic, /deletetopic)
    app.add_handler(CommandHandler("topics", topics_command))
    
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("edittopic", start_edit_topic)],
        states={
            EDIT_TOPIC_SELECT: [CallbackQueryHandler(edit_topic_select, pattern="^edtopic_")],
            EDIT_TOPIC_NEW_NAME: [MessageHandler(txt_filter, edit_topic_save)]
        },
        fallbacks=[CommandHandler("cancel", cancel_wizard)],
        conversation_timeout=300
    ))
    
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("deletetopic", start_delete_topic)],
        states={DELETE_TOPIC_CONFIRM: [CallbackQueryHandler(delete_topic_confirm, pattern="^deltopic_")]},
        fallbacks=[CommandHandler("cancel", cancel_wizard)],
        conversation_timeout=60
    ))

    # ---- Catch-all guards: MUST stay last so real handlers win ----
    # Any admin-only / unknown command touched by a non-admin gets a denial.
    app.add_handler(MessageHandler(filters.COMMAND, guard_unknown_command))
    # Non-admins DMing the bot random text get the same reply (rate limited).
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, guard_non_admin_dm))
    # Anything else a non-admin DMs (media, stickers, files) is reported to the
    # owner rather than silently dropped.
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & ~filters.TEXT & ~filters.COMMAND & ~filters.StatusUpdate.ALL,
        guard_non_admin_media))

    app.add_handler(CallbackQueryHandler(handle_expired))
    
    # Global error handler
    app.add_error_handler(error_handler)

    print("✅ VASUKI CLOUD BOT ONLINE")
    try:
        # drop_pending_updates=True prevents conflict with previous instances
        app.run_polling(drop_pending_updates=True)
    finally:
        # The writer is a daemon thread, so any pending change would be lost on
        # exit. Flush synchronously on the way out.
        if supabase:
            logger.info("💾 Flushing database before shutdown...")
            flush_db_sync()

if __name__ == "__main__":
    main()