# bot.py
import os
import json
import random
import asyncio
import logging
import tempfile
from pathlib import Path
from base64 import b64decode
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlparse

import discord
from discord import app_commands
from discord.ext import commands
from github import Github, GithubException

# NEW: PostgreSQL support for persistent config on Railway
try:
    import asyncpg
    HAS_ASYNCPG = True
except ImportError:
    HAS_ASYNCPG = False

from netflix_checker import check_cookie_file

# ╔══════════════════════════════════════════════════════════════╗
# ║                      CONFIGURATION                          ║
# ╚══════════════════════════════════════════════════════════════╝

COOKIES_FOLDER         = Path("cookies")   # local fallback (used if GitHub fetch fails)
SCRIPT_TIMEOUT         = 30
CONFIG_FILE            = Path("config.json")
USER_LOG_FILE          = Path("users.txt")     # local fallback (also pushed to GitHub)
CLEANUP_DELAY_SECONDS  = 60

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_TOKEN")
GITHUB_TOKEN      = os.environ.get("GITHUB_TOKEN")
REMOTE_LOG_URL    = os.environ.get("REMOTE_LOG_URL")

# GitHub URL for saving/loading guild→channel link configs (survives bot updates)
# This is the logs.txt file used to persist /channel command mappings
#   https://github.com/Afrsto/bot-users/blob/main/logs.txt
CHANNEL_LOG_URL = "https://github.com/Afrsto/bot-users/blob/main/logs.txt"

# Parse CHANNEL_LOG_URL into repo + file path
CHANNEL_LOG_GITHUB_REPO: str | None = None
CHANNEL_LOG_GITHUB_PATH: str | None = None
_clp = urlparse(CHANNEL_LOG_URL)
if _clp.netloc == "github.com":
    _cl_parts = _clp.path.strip("/").split("/")
    # format: /owner/repo/blob/branch/path/to/file
    if len(_cl_parts) >= 2:
        CHANNEL_LOG_GITHUB_REPO = f"{_cl_parts[0]}/{_cl_parts[1]}"
    if "blob" in _cl_parts:
        _bi = _cl_parts.index("blob")
        if _bi + 2 < len(_cl_parts):
            CHANNEL_LOG_GITHUB_PATH = "/".join(_cl_parts[_bi + 2:])

# NEW: GitHub URL for the cookies folder
# Set COOKIES_REPO_URL in Railway env vars, e.g.:
#   https://github.com/Afrsto/bot-users/tree/main/cookies
COOKIES_REPO_URL = os.environ.get(
    "COOKIES_REPO_URL",
    "https://github.com/Afrsto/bot-users/tree/main/cookies"   # default hardcoded
)

# NEW: Parse cookies GitHub repo + path from COOKIES_REPO_URL
COOKIES_GITHUB_REPO: str | None = None
COOKIES_GITHUB_PATH: str | None = None
COOKIES_GITHUB_BRANCH: str = "main"

if COOKIES_REPO_URL:
    _cp = urlparse(COOKIES_REPO_URL)
    if _cp.netloc == "github.com":
        _parts = _cp.path.strip("/").split("/")
        # format: /owner/repo/tree/branch/path/to/folder
        if len(_parts) >= 2:
            COOKIES_GITHUB_REPO = f"{_parts[0]}/{_parts[1]}"
        if "tree" in _parts:
            _ti = _parts.index("tree")
            if _ti + 1 < len(_parts):
                COOKIES_GITHUB_BRANCH = _parts[_ti + 1]
            if _ti + 2 < len(_parts):
                COOKIES_GITHUB_PATH = "/".join(_parts[_ti + 2:])
            else:
                COOKIES_GITHUB_PATH = ""   # root of repo

if COOKIES_GITHUB_REPO:
    logging.info(f"✅ Cookies source: GitHub {COOKIES_GITHUB_REPO}/{COOKIES_GITHUB_PATH} [{COOKIES_GITHUB_BRANCH}]")
else:
    logging.warning("⚠️ Could not parse COOKIES_REPO_URL – falling back to local cookies folder")

# NEW: Support multiple guilds via GUILD_ID_1, GUILD_ID_2, GUILD_ID_3, ...
ALLOWED_GUILD_IDS: list[int] = []
for key, value in os.environ.items():
    if key.startswith("GUILD_ID_") and value and value.strip().isdigit():
        ALLOWED_GUILD_IDS.append(int(value.strip()))

# UPDATED: Also support legacy GUILD_ID for backwards compatibility
_legacy_guild = os.environ.get("GUILD_ID", "").strip()
if _legacy_guild.isdigit() and int(_legacy_guild) not in ALLOWED_GUILD_IDS:
    ALLOWED_GUILD_IDS.append(int(_legacy_guild))

if not ALLOWED_GUILD_IDS:
    raise ValueError("❌ No valid GUILD_ID_1 / GUILD_ID_2 / ... environment variables found")

if not DISCORD_BOT_TOKEN:
    raise ValueError("❌ Missing DISCORD_TOKEN environment variable")

# NEW: Default channel ID fallback for Railway (avoids needing /channel after restart)
_default_ch = os.environ.get("DEFAULT_CHANNEL_ID", "").strip()
DEFAULT_CHANNEL_ID: int | None = int(_default_ch) if _default_ch.isdigit() else None
if DEFAULT_CHANNEL_ID:
    logging.info(f"✅ DEFAULT_CHANNEL_ID loaded from env: {DEFAULT_CHANNEL_ID}")
else:
    logging.warning("⚠️ DEFAULT_CHANNEL_ID not set – /channel command required after restart")

# NEW: PostgreSQL URL (Railway injects this automatically as DATABASE_URL)
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if DATABASE_URL:
    logging.info("✅ DATABASE_URL found – persistent PostgreSQL config enabled")
else:
    logging.warning("⚠️ DATABASE_URL not set – config will NOT persist across Railway restarts")

# ────────────────────────────────────────────────────────────────
# Parse GitHub repo and file path from REMOTE_LOG_URL_D
#REMOTE_LOG_URL = Example URL: https://github.com/Afrsto/bot-users/blob/main/users.txt
# ────────────────────────────────────────────────────────────────
GITHUB_REPO = None
GITHUB_FILE_PATH = None

if REMOTE_LOG_URL:
    parsed = urlparse(REMOTE_LOG_URL)
    if parsed.netloc == "github.com":
        path_parts = parsed.path.strip("/").split("/")
        if len(path_parts) >= 2:
            owner_repo = f"{path_parts[0]}/{path_parts[1]}"
            if "blob" in path_parts:
                idx = path_parts.index("blob")
                if idx + 2 < len(path_parts):
                    GITHUB_FILE_PATH = "/".join(path_parts[idx+2:])
                    GITHUB_REPO = owner_repo
    if not GITHUB_REPO:
        logging.warning("⚠️ Could not parse REMOTE_LOG_URL_D, GitHub logging disabled.")
else:
    logging.warning("⚠️ REMOTE_LOG_URL_D not set, GitHub logging disabled.")

# ────────────────────────────────────────────────────────────────
# Locale → Country name + timezone mapping
# ────────────────────────────────────────────────────────────────
LOCALE_TO_COUNTRY: dict[str, str] = {
    "ar": "Arab Region", "ar-AE": "UAE", "ar-BH": "Bahrain",
    "ar-DZ": "Algeria", "ar-EG": "Egypt", "ar-IQ": "Iraq",
    "ar-JO": "Jordan", "ar-KW": "Kuwait", "ar-LB": "Lebanon",
    "ar-LY": "Libya", "ar-MA": "Morocco", "ar-OM": "Oman",
    "ar-QA": "Qatar", "ar-SA": "Saudi Arabia", "ar-SD": "Sudan",
    "ar-SY": "Syria", "ar-TN": "Tunisia", "ar-YE": "Yemen",
    "en": "Unknown", "en-US": "USA", "en-GB": "UK",
    "en-AU": "Australia", "en-CA": "Canada", "en-IN": "India",
    "en-NZ": "New Zealand", "en-ZA": "South Africa",
    "fr": "France", "fr-BE": "Belgium", "fr-CA": "Canada",
    "fr-CH": "Switzerland", "fr-FR": "France",
    "de": "Germany", "de-AT": "Austria", "de-CH": "Switzerland", "de-DE": "Germany",
    "es": "Spain", "es-ES": "Spain", "es-MX": "Mexico", "es-AR": "Argentina",
    "tr": "Turkey", "tr-TR": "Turkey",
    "ru": "Russia", "ru-RU": "Russia",
    "zh": "China", "zh-CN": "China", "zh-TW": "Taiwan",
    "ja": "Japan", "ja-JP": "Japan",
    "ko": "South Korea", "ko-KR": "South Korea",
    "pt": "Portugal", "pt-BR": "Brazil", "pt-PT": "Portugal",
    "it": "Italy", "it-IT": "Italy",
    "nl": "Netherlands", "nl-NL": "Netherlands", "nl-BE": "Belgium",
    "pl": "Poland", "pl-PL": "Poland",
    "sv": "Sweden", "sv-SE": "Sweden",
    "no": "Norway", "nb": "Norway", "nb-NO": "Norway",
    "da": "Denmark", "da-DK": "Denmark",
    "fi": "Finland", "fi-FI": "Finland",
    "cs": "Czech Republic", "cs-CZ": "Czech Republic",
    "ro": "Romania", "ro-RO": "Romania",
    "hu": "Hungary", "hu-HU": "Hungary",
    "el": "Greece", "el-GR": "Greece",
    "he": "Israel", "he-IL": "Israel",
    "fa": "Iran", "fa-IR": "Iran",
    "hi": "India", "hi-IN": "India",
    "id": "Indonesia", "id-ID": "Indonesia",
    "ms": "Malaysia", "ms-MY": "Malaysia",
    "th": "Thailand", "th-TH": "Thailand",
    "vi": "Vietnam", "vi-VN": "Vietnam",
    "uk": "Ukraine", "uk-UA": "Ukraine",
}

LOCALE_TO_TZ: dict[str, str] = {
    "ar-AE": "Asia/Dubai",        "ar-BH": "Asia/Bahrain",
    "ar-DZ": "Africa/Algiers",    "ar-EG": "Africa/Cairo",
    "ar-IQ": "Asia/Baghdad",      "ar-JO": "Asia/Amman",
    "ar-KW": "Asia/Kuwait",       "ar-LB": "Asia/Beirut",
    "ar-LY": "Africa/Tripoli",    "ar-MA": "Africa/Casablanca",
    "ar-OM": "Asia/Muscat",       "ar-QA": "Asia/Qatar",
    "ar-SA": "Asia/Riyadh",       "ar-SD": "Africa/Khartoum",
    "ar-SY": "Asia/Damascus",     "ar-TN": "Africa/Tunis",
    "ar-YE": "Asia/Aden",
    "en-US": "America/New_York",  "en-GB": "Europe/London",
    "en-AU": "Australia/Sydney",  "en-CA": "America/Toronto",
    "en-IN": "Asia/Kolkata",      "en-NZ": "Pacific/Auckland",
    "en-ZA": "Africa/Johannesburg",
    "fr-FR": "Europe/Paris",      "fr-BE": "Europe/Brussels",
    "fr-CH": "Europe/Zurich",     "fr-CA": "America/Toronto",
    "de-DE": "Europe/Berlin",     "de-AT": "Europe/Vienna",
    "de-CH": "Europe/Zurich",
    "es-ES": "Europe/Madrid",     "es-MX": "America/Mexico_City",
    "es-AR": "America/Argentina/Buenos_Aires",
    "tr-TR": "Europe/Istanbul",
    "ru-RU": "Europe/Moscow",
    "zh-CN": "Asia/Shanghai",     "zh-TW": "Asia/Taipei",
    "ja-JP": "Asia/Tokyo",
    "ko-KR": "Asia/Seoul",
    "pt-BR": "America/Sao_Paulo", "pt-PT": "Europe/Lisbon",
    "it-IT": "Europe/Rome",
    "nl-NL": "Europe/Amsterdam",  "nl-BE": "Europe/Brussels",
    "pl-PL": "Europe/Warsaw",
    "sv-SE": "Europe/Stockholm",
    "nb-NO": "Europe/Oslo",
    "da-DK": "Europe/Copenhagen",
    "fi-FI": "Europe/Helsinki",
    "cs-CZ": "Europe/Prague",
    "ro-RO": "Europe/Bucharest",
    "hu-HU": "Europe/Budapest",
    "el-GR": "Europe/Athens",
    "he-IL": "Asia/Jerusalem",
    "fa-IR": "Asia/Tehran",
    "hi-IN": "Asia/Kolkata",
    "id-ID": "Asia/Jakarta",
    "ms-MY": "Asia/Kuala_Lumpur",
    "th-TH": "Asia/Bangkok",
    "vi-VN": "Asia/Ho_Chi_Minh",
    "uk-UA": "Europe/Kiev",
}

EGYPT_TZ = ZoneInfo("Africa/Cairo")


def get_locale_info(locale_str: str) -> tuple[str, str, str]:
    """
    Returns (country_name, local_time_str, tz_name) for the given Discord locale.
    Falls back gracefully if locale is unknown.
    """
    locale = str(locale_str)
    # Try full locale first, then language prefix
    country = LOCALE_TO_COUNTRY.get(locale) or LOCALE_TO_COUNTRY.get(locale.split("-")[0], "Unknown")
    tz_key  = LOCALE_TO_TZ.get(locale) or LOCALE_TO_TZ.get(locale.split("-")[0])
    if tz_key:
        try:
            tz       = ZoneInfo(tz_key)
            local_dt = datetime.now(tz)
            local_time_str = local_dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            local_time_str = "N/A"
            tz_key = "Unknown"
    else:
        local_time_str = "N/A"
        tz_key = "Unknown"
    return country, local_time_str, tz_key


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("NetflixBot")

# ╔══════════════════════════════════════════════════════════════╗
# ║              COOKIE FILE ROTATION TRACKER                   ║
# ╚══════════════════════════════════════════════════════════════╝
_used_cookie_files: list[Path] = []


def pick_cookie_file(txt_files: list[Path]) -> Path:
    """
    Pick a random .txt cookie file with round‑robin rotation.
    Once all files have been used, the list resets so files can be
    selected again – this works perfectly even when only one file exists.
    """
    global _used_cookie_files

    # Remove entries that no longer exist on disk
    _used_cookie_files = [f for f in _used_cookie_files if f in txt_files]

    remaining = [f for f in txt_files if f not in _used_cookie_files]

    if not remaining:
        log.info("🔄 All cookie files have been used. Resetting rotation.")
        _used_cookie_files.clear()
        remaining = list(txt_files)

    chosen = random.choice(remaining)
    _used_cookie_files.append(chosen)
    log.info(f"📂 Picked cookie file: {chosen.name}  "
             f"({len(_used_cookie_files)}/{len(txt_files)} used in this rotation)")
    return chosen


# ╔══════════════════════════════════════════════════════════════╗
# ║                   GITHUB HELPER FUNCTIONS                   ║
# ╚══════════════════════════════════════════════════════════════╝
_github_file_sha: str | None = None


def get_github_repo():
    """Return the GitHub repository object, or None if token or repo config is missing."""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return None
    g = Github(GITHUB_TOKEN)
    return g.get_repo(GITHUB_REPO)


def update_users_txt_on_github(new_line: str) -> None:
    """Append a line to the remote users.txt file defined by REMOTE_LOG_URL_D."""
    if not GITHUB_REPO or not GITHUB_FILE_PATH:
        log.debug("GitHub logging disabled – missing repo or file path.")
        return

    repo = get_github_repo()
    if not repo:
        log.warning("GitHub repo not available – log not pushed.")
        return

    global _github_file_sha
    try:
        try:
            contents = repo.get_contents(GITHUB_FILE_PATH)
            current_content = b64decode(contents.content).decode("utf-8")
            _github_file_sha = contents.sha
        except GithubException as e:
            if e.status == 404:
                current_content = ""
                _github_file_sha = None
                log.info(f"📄 {GITHUB_FILE_PATH} does not exist – will create it.")
            else:
                log.error(f"❌ GitHub get_contents error: {e}")
                return

        new_content = current_content + new_line

        if _github_file_sha:
            repo.update_file(
                path=GITHUB_FILE_PATH,
                message="📝 Add log entry from Netflix bot",
                content=new_content,
                sha=_github_file_sha,
                branch="main",
            )
        else:
            repo.create_file(
                path=GITHUB_FILE_PATH,
                message="🆕 Create users.txt with initial log",
                content=new_content,
                branch="main",
            )
        log.info(f"✅ GitHub commit successful → {new_line.strip()[:80]}...")
    except GithubException as e:
        log.error(f"❌ GitHub commit failed: {e.status} – {e.data.get('message', '')}")


# ╔══════════════════════════════════════════════════════════════╗
# ║         GITHUB CHANNEL-LINK LOG  (logs.txt)                 ║
# ║  Persists guild→channel mappings so /channel survives       ║
# ║  bot updates without needing to be re-run.                  ║
# ╚══════════════════════════════════════════════════════════════╝

_channel_log_sha: str | None = None


def _get_channel_log_repo():
    """Return the GitHub repo object for the channel log, or None."""
    if not GITHUB_TOKEN or not CHANNEL_LOG_GITHUB_REPO:
        return None
    g = Github(GITHUB_TOKEN)
    return g.get_repo(CHANNEL_LOG_GITHUB_REPO)


def save_channel_link_to_github(guild_id: int, guild_name: str, channel_id: int, channel_name: str) -> None:
    """
    Append a CHANNEL_LINK entry to logs.txt on GitHub.
    Format (one per line):
        CHANNEL_LINK | guild_id=... | guild_name=... | channel_id=... | channel_name=... | set_at=...
    Older entries for the same guild are replaced so the file stays clean.
    """
    if not CHANNEL_LOG_GITHUB_REPO or not CHANNEL_LOG_GITHUB_PATH:
        log.warning("⚠️ Channel-log GitHub target not configured – skip save.")
        return

    repo = _get_channel_log_repo()
    if not repo:
        log.warning("⚠️ Channel-log GitHub repo unavailable – skip save.")
        return

    global _channel_log_sha
    now_str = datetime.now(EGYPT_TZ).strftime("%Y-%m-%d %H:%M:%S")
    new_entry = (
        f"CHANNEL_LINK | guild_id={guild_id} | guild_name={guild_name} | "
        f"channel_id={channel_id} | channel_name={channel_name} | set_at={now_str}\n"
    )

    try:
        try:
            contents = repo.get_contents(CHANNEL_LOG_GITHUB_PATH)
            raw = b64decode(contents.content).decode("utf-8")
            _channel_log_sha = contents.sha
        except GithubException as e:
            if e.status == 404:
                raw = ""
                _channel_log_sha = None
            else:
                log.error(f"❌ channel-log get_contents error: {e}")
                return

        # Remove any previous entry for this guild so there are no duplicates
        lines = [ln for ln in raw.splitlines(keepends=True)
                 if not (ln.startswith("CHANNEL_LINK") and f"guild_id={guild_id}" in ln)]
        lines.append(new_entry)
        new_content = "".join(lines)

        if _channel_log_sha:
            repo.update_file(
                path=CHANNEL_LOG_GITHUB_PATH,
                message=f"📌 Update channel link: guild {guild_id} → channel {channel_id}",
                content=new_content,
                sha=_channel_log_sha,
                branch="main",
            )
        else:
            repo.create_file(
                path=CHANNEL_LOG_GITHUB_PATH,
                message=f"🆕 Create logs.txt with channel link: guild {guild_id}",
                content=new_content,
                branch="main",
            )
        log.info(f"✅ Channel link saved to GitHub logs.txt: guild {guild_id} → channel {channel_id}")
    except GithubException as e:
        log.error(f"❌ Failed to save channel link to GitHub: {e.status} – {e.data.get('message', '')}")


def load_channel_links_from_github() -> dict[str, int]:
    """
    Read logs.txt from GitHub and return a dict of {str(guild_id): channel_id}
    for every CHANNEL_LINK line found. Returns empty dict on any error.
    """
    if not CHANNEL_LOG_GITHUB_REPO or not CHANNEL_LOG_GITHUB_PATH:
        return {}

    repo = _get_channel_log_repo()
    if not repo:
        return {}

    try:
        contents = repo.get_contents(CHANNEL_LOG_GITHUB_PATH)
        raw = b64decode(contents.content).decode("utf-8")
    except GithubException as e:
        if e.status == 404:
            log.info("ℹ️ logs.txt not found on GitHub – no channel links to restore.")
        else:
            log.error(f"❌ Failed to read logs.txt from GitHub: {e}")
        return {}

    result: dict[str, int] = {}
    for line in raw.splitlines():
        if not line.startswith("CHANNEL_LINK"):
            continue
        try:
            parts = {kv.split("=", 1)[0].strip(): kv.split("=", 1)[1].strip()
                     for kv in line.split("|")[1:] if "=" in kv}
            gid = parts["guild_id"]
            cid = int(parts["channel_id"])
            result[gid] = cid
        except Exception:
            continue  # malformed line – skip silently

    log.info(f"📥 Loaded {len(result)} channel link(s) from GitHub logs.txt: {result}")
    return result


# ╔══════════════════════════════════════════════════════════════╗
# ║           GITHUB COOKIES FETCHER  (NEW)                     ║
# ║  Fetches .txt cookie files from the GitHub cookies folder   ║
# ║  so Railway doesn't need a local cookies/ directory.        ║
# ╚══════════════════════════════════════════════════════════════╝

# In-memory cache: filename → raw text content
_github_cookie_cache: dict[str, str] = {}


def _get_cookies_repo():
    """Return the GitHub repo object used for cookie files."""
    if not GITHUB_TOKEN or not COOKIES_GITHUB_REPO:
        return None
    try:
        g = Github(GITHUB_TOKEN)
        return g.get_repo(COOKIES_GITHUB_REPO)
    except GithubException as e:
        log.error(f"❌ Cannot access cookies repo {COOKIES_GITHUB_REPO}: {e}")
        return None


def fetch_github_cookie_list() -> list[str]:
    """
    NEW: Return a list of .txt filenames found in the GitHub cookies folder.
    Uses PyGitHub to list the folder contents.
    Falls back to [] on any error.
    """
    repo = _get_cookies_repo()
    if not repo:
        return []
    try:
        folder_path = COOKIES_GITHUB_PATH or ""
        contents = repo.get_contents(folder_path, ref=COOKIES_GITHUB_BRANCH)
        txt_files = [
            c.name for c in contents
            if c.type == "file" and c.name.endswith(".txt")
        ]
        log.info(f"📂 GitHub cookies folder has {len(txt_files)} .txt file(s): {txt_files}")
        return txt_files
    except GithubException as e:
        log.error(f"❌ Failed to list GitHub cookies folder: {e}")
        return []


def fetch_github_cookie_content(filename: str) -> str | None:
    """
    NEW: Download and return the raw text content of a single cookie file
    from the GitHub cookies folder.
    Returns None on failure.
    """
    repo = _get_cookies_repo()
    if not repo:
        return None
    try:
        folder_path = COOKIES_GITHUB_PATH or ""
        file_path   = f"{folder_path}/{filename}".lstrip("/")
        content_obj = repo.get_contents(file_path, ref=COOKIES_GITHUB_BRANCH)
        raw = b64decode(content_obj.content).decode("utf-8")
        log.info(f"✅ Downloaded cookie file from GitHub: {filename} ({len(raw)} bytes)")
        return raw
    except GithubException as e:
        log.error(f"❌ Failed to download cookie file {filename}: {e}")
        return None


def pick_github_cookie_file(filenames: list[str]) -> str:
    """
    NEW: Round-robin cookie selection over GitHub filenames (strings, not Paths).
    Mirrors the logic of pick_cookie_file() but works with filename strings.
    """
    global _used_cookie_files
    # Reuse the same _used_cookie_files list but store filenames as fake Paths
    # Use a separate global for GitHub names to keep things clean.
    return random.choice(filenames)   # simple random for GitHub files


# NEW: separate rotation tracker for GitHub cookie names
_used_github_cookie_names: list[str] = []


def pick_github_cookie_rotation(filenames: list[str]) -> str:
    """Round-robin selection over GitHub cookie filenames."""
    global _used_github_cookie_names

    # Remove filenames that no longer exist
    _used_github_cookie_names = [f for f in _used_github_cookie_names if f in filenames]

    remaining = [f for f in filenames if f not in _used_github_cookie_names]
    if not remaining:
        log.info("🔄 All GitHub cookie files used – resetting rotation.")
        _used_github_cookie_names.clear()
        remaining = list(filenames)

    chosen = random.choice(remaining)
    _used_github_cookie_names.append(chosen)
    log.info(f"🎯 GitHub cookie picked: {chosen}  "
             f"({len(_used_github_cookie_names)}/{len(filenames)} used this rotation)")
    return chosen
# ╚══════════════════════════════════════════════════════════════╝
async def log_user_activity(
    interaction: discord.Interaction,
    condition: str,
    result: str,
    used_txt_files: list[str] | None = None,
    language: str | None = None,
) -> None:
    """Append a structured log entry locally and push to GitHub."""
    now_egypt = datetime.now(EGYPT_TZ).strftime("%Y-%m-%d %H:%M:%S")
    user      = interaction.user
    guild     = interaction.guild

    # Rich user information
    username      = str(user)
    display_name  = user.display_name
    user_id       = user.id
    account_since = user.created_at.strftime("%Y-%m-%d")
    server_name   = guild.name if guild else "DM"
    server_id     = guild.id   if guild else "N/A"

    # ── Locale → country + local time ─────────────────────────────────
    locale_str = str(interaction.locale) if interaction.locale else "en"
    country_name, local_time, local_tz = get_locale_info(locale_str)

    # ── Fetch fresh member data to avoid cache issues ─────────────────
    member_since        = "N/A"
    login_date_server   = "N/A"   # date the account joined this server
    roles_str           = "N/A"

    if guild:
        try:
            # Force fetch the member from Discord API (bypass cache)
            member = await guild.fetch_member(user.id)
            if member.joined_at:
                member_since      = member.joined_at.strftime("%Y-%m-%d")
                login_date_server = member.joined_at.strftime("%Y-%m-%d %H:%M:%S")
            # Get roles (skip @everyone)
            if member.roles:
                roles_str = ", ".join(r.name for r in member.roles[1:]) or "None"
        except discord.NotFound:
            log.warning(f"⚠️ Member {user.id} not found in guild {guild.id}")
        except Exception as e:
            log.warning(f"⚠️ Failed to fetch member {user.id}: {e}")

    channel_name = (
        interaction.channel.name
        if interaction.channel and hasattr(interaction.channel, "name")
        else "N/A"
    )

    txt_files_str = ", ".join(used_txt_files) if used_txt_files else "N/A"
    lang_label    = {"ar": "Arabic 🇸🇦", "en": "English 🇬🇧"}.get(language, language) if language else "N/A"

    line = (
        f"[{now_egypt} EGY] "
        f"👤 User: {username} (Display: {display_name}) | "
        f"🆔 ID: {user_id} | "
        f"🗓️  Account Created: {account_since} | "
        f"📅 Joined Server: {login_date_server} | "
        f"🏠 Server: {server_name} (ID: {server_id}) | "
        f"💬 Channel: #{channel_name} | "
        f"🎭 Roles: [{roles_str}] | "
        f"🌐 Language: {lang_label} | "
        f"📄 Files Used: [{txt_files_str}] | "
        f"📊 Status: {condition} | "
        f"🔎 Result: {result}\n"
    )

    # 1. Write locally
    try:
        with open(USER_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
        log.info(f"📝 Logged activity for {username} locally.")
    except Exception as e:
        log.error(f"❌ Failed to write local log: {e}")

    # 2. Push to GitHub (persistent storage)
    update_users_txt_on_github(line)


# ╔══════════════════════════════════════════════════════════════╗
# ║                       TRANSLATIONS                          ║
# ╚══════════════════════════════════════════════════════════════╝
TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "lang_prompt":            "🌐 **Please select your language:**\n🌐 **الرجاء اختيار اللغة:**",
        "lang_selected":          "✅ Language selected: **English**",
        "confirm_prompt":         "🎬 **Do you want to generate a Netflix login link?**\n",
        "progress":               "⏳ **Generating your Netflix link… please wait.**",
        "no_cookies_folder":      "❌ Cookies folder not found. Please contact the administrator.",
        "no_cookie_files":        "❌ No accounts available in the database right now. Please try again later.",
        "timeout":                "⌛ The validation process took too long. Please try again later.",
        "unexpected_error":       "⚠️ An unexpected error occurred. Please try again.",
        "cookie_invalid":         "❌ The selected session is invalid or expired. Please try again.",
        "success_title":          "✅ 🎬 Netflix Login Link Ready!",
        "success_desc":           "🔗 Click the link below to log in automatically:\n\n{link}",
        "footer":                 "⚠️ This link is for personal use only – do not share it.",
        "tv_instruction":         " **TV Activation:** Visit **netflix.com/tv9** and enter the code shown on your screen.",
        "yes_label":              "✅  Yes, generate link",
        "no_label":               "❌  No, cancel",
        "cancelled":              "🚫 Process cancelled.",
        "not_for_you":            "🚫 You cannot interact with this menu.",
        "timeout_msg":            "⏰ Request timed out due to inactivity.",
        "wrong_channel_no_config": "⚠️ No channel configured. Admins must run `/channel` first.",
        "wrong_channel_with_config": "❌ This command can only be used in {channel}.",
        "wrong_guild":            "❌ This bot is restricted to a specific server.",
        "setup_desc": (
            "Welcome! 👋 Use the `/create` command to generate a Netflix PC login link.\n\n"
            "**📋 How to use:**\n"
            "1️⃣  Type `/create` in this channel.\n"
            "2️⃣  Select your preferred language.\n"
            "3️⃣  Confirm the generation.\n"
            "4️⃣  Wait a few seconds for your personal link.\n"
            "5️⃣  To log in on TV, visit **netflix.com/tv9** and enter the code shown on your screen.\n"
            "6️⃣  ⚠️ These links are for **PC and TV only** — they do **not** work on mobile phones.\n\n"
            "*⚠️ Note: Links are single-use. Messages auto-delete after 1 minute for privacy.*"
        ),
    },
    "ar": {
        "lang_prompt":            "🌐 **Please select your language:**\n🌐 **الرجاء اختيار اللغة:**",
        "lang_selected":          "\u200f✅ تم اختيار اللغة: **العربية**",
        "confirm_prompt":         "\u200f🎬 **هل تريد إنشاء رابط تسجيل دخول لـ نتفليكس؟**\n",
        "progress":               "\u200f⏳ **جاري إنشاء الرابط الخاص بك… يرجى الانتظار.**",
        "no_cookies_folder":      "\u200f❌ مجلد ملفات تعريف الارتباط غير موجود. يرجى الاتصال بالمسؤول.",
        "no_cookie_files":        "\u200f❌ لا توجد حسابات متاحة حالياً في قاعدة البيانات. حاول لاحقاً.",
        "timeout":                "\u200f⌛ استغرق التحقق وقتاً طويلاً. يرجى المحاولة مرة أخرى لاحقاً.",
        "unexpected_error":       "\u200f⚠️ حدث خطأ غير متوقع أثناء معالجة الطلب.",
        "cookie_invalid":         "\u200f❌ الحساب المختار غير صالح أو منتهي الصلاحية. حاول مجدداً.",
        "success_title":          "\u200f✅ 🎬 رابط تسجيل الدخول إلى نتفليكس جاهز!",
        "success_desc":           "\u200f🔗 انقر على الرابط أدناه لتسجيل الدخول تلقائياً:\n\n{link}",
        "footer":                 "\u200f⚠️ هذا الرابط للاستخدام الشخصي فقط – يُمنع مشاركته.",
        "tv_instruction":         "\u200f **تفعيل التلفاز:** قم بزيارة **netflix.com/tv9** وأدخل الرمز المعروض على شاشتك.",
        "yes_label":              "✅  نعم، أنشئ الرابط",
        "no_label":               "❌  لا، إلغاء",
        "cancelled":              "\u200f🚫 تم إلغاء العملية.",
        "not_for_you":            "\u200f🚫 لا يمكنك التفاعل مع هذه القائمة.",
        "timeout_msg":            "\u200f⏰ انتهت مهلة الطلب بسبب عدم التفاعل.",
        "wrong_channel_no_config": "\u200f⚠️ لم يتم إعداد القناة. يجب على المسؤول استخدام أمر `/channel` أولاً.",
        "wrong_channel_with_config": "\u200f❌ لا يمكن استخدام هذا الأمر إلا في {channel}.",
        "wrong_guild":            "\u200f❌ هذا البوت مخصص للعمل في سيرفر محدد فقط.",
        "setup_desc": (
            "مرحباً! 👋 استخدم أمر `/create` لإنشاء رابط تسجيل دخول لـ نتفليكس.\n\n"
            "**📋 طريقة الاستخدام:**\n"
            "1️⃣  اكتب `/create` في هذه القناة.\n"
            "2️⃣  اختر لغتك المفضلة.\n"
            "3️⃣  قم بتأكيد الإنشاء.\n"
            "4️⃣  انتظر بضع ثوانٍ للحصول على رابطك الشخصي.\n"
            "\u200f5️⃣  لتسجيل الدخول على التلفاز، قم بزيارة **netflix.com/tv9** وأدخل الرمز المعروض على شاشتك.\n"
            "\u200f6️⃣  ⚠️ هذه الروابط مخصصة لـ **الكمبيوتر والتلفاز فقط** — لا تعمل على **الهاتف المحمول**.\n\n"
            "\u200f*⚠️ ملاحظة: الروابط للاستخدام مرة واحدة. يتم حذف الرسائل تلقائياً بعد دقيقة للخصوصية.*"
        ),
    },
}


def get_user_lang(interaction: discord.Interaction) -> str:
    """Detect Arabic locale, otherwise default to English."""
    return "ar" if str(interaction.locale).startswith("ar") else "en"


# ╔══════════════════════════════════════════════════════════════╗
# ║                      CONFIG MANAGER                         ║
# ║  FIXED: PostgreSQL-backed persistence (survives Railway      ║
# ║  restarts) + DEFAULT_CHANNEL_ID env fallback                ║
# ╚══════════════════════════════════════════════════════════════╝
class Config:
    def __init__(self) -> None:
        # In-memory cache: str(guild_id) → channel_id
        self.guilds: dict[str, int] = {}
        # Legacy single-channel attr kept for compatibility
        self.allowed_channel_id: int | None = None
        # PostgreSQL connection pool (set during bot startup)
        self._db_pool = None

    # ── PostgreSQL helpers ─────────────────────────────────────────

    async def init_db(self) -> None:
        """
        Called once in on_ready().
        Creates the guild_config table if it doesn't exist,
        then loads all rows into the in-memory cache.
        Fallback chain:
          1. PostgreSQL  (Railway DATABASE_URL)
          2. config.json (local file)
          3. GitHub logs.txt (CHANNEL_LOG_URL – survives bot updates)
          4. DEFAULT_CHANNEL_ID env var
        """
        if not DATABASE_URL or not HAS_ASYNCPG:
            log.warning("⚠️ PostgreSQL unavailable – using file/env/GitHub fallback")
            self._load_from_file()
        else:
            try:
                # Railway's DATABASE_URL starts with postgres:// but asyncpg needs postgresql://
                dsn = DATABASE_URL.replace("postgres://", "postgresql://", 1)
                self._db_pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)

                async with self._db_pool.acquire() as conn:
                    await conn.execute("""
                        CREATE TABLE IF NOT EXISTS guild_config (
                            guild_id  TEXT PRIMARY KEY,
                            channel_id BIGINT NOT NULL
                        )
                    """)
                    rows = await conn.fetch("SELECT guild_id, channel_id FROM guild_config")
                    for row in rows:
                        self.guilds[row["guild_id"]] = int(row["channel_id"])

                log.info(f"✅ PostgreSQL config loaded – {len(self.guilds)} guild(s): {self.guilds}")
            except Exception as e:
                log.error(f"❌ PostgreSQL init failed: {e} – falling back to file/GitHub")
                self._db_pool = None
                self._load_from_file()

        # ── GitHub logs.txt fallback ───────────────────────────────────
        # Fill in any guilds still missing from the in-memory cache.
        # This is the key layer that survives bot updates even when
        # PostgreSQL and config.json are both unavailable.
        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        github_links = await loop.run_in_executor(None, load_channel_links_from_github)
        restored = 0
        for gid, cid in github_links.items():
            if gid not in self.guilds:
                self.guilds[gid] = cid
                restored += 1
                log.info(f"🔄 Restored from GitHub logs.txt: guild {gid} → channel {cid}")
        if restored:
            log.info(f"✅ {restored} guild channel link(s) restored from GitHub logs.txt")

    async def _save_to_db(self, guild_id: str, channel_id: int) -> None:
        """NEW: Upsert a guild→channel mapping into PostgreSQL."""
        if not self._db_pool:
            return
        try:
            async with self._db_pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO guild_config (guild_id, channel_id)
                    VALUES ($1, $2)
                    ON CONFLICT (guild_id) DO UPDATE SET channel_id = EXCLUDED.channel_id
                """, guild_id, channel_id)
            log.info(f"✅ Saved to PostgreSQL: guild {guild_id} → channel {channel_id}")
        except Exception as e:
            log.error(f"❌ PostgreSQL save failed: {e}")

    # ── File fallback (used when no DB available) ──────────────────

    def _load_from_file(self) -> None:
        """
        FIXED: Load from config.json as a fallback.
        Handles both new per-guild and old flat formats.
        Does NOT crash if file is missing (Railway ephemeral storage).
        """
        if not CONFIG_FILE.exists():
            log.warning("⚠️ config.json not found – only DEFAULT_CHANNEL_ID env will be used")
            return
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
            if "guilds" in data and isinstance(data["guilds"], dict):
                self.guilds = {str(k): int(v) for k, v in data["guilds"].items()}
                log.info(f"✅ config.json loaded – guilds: {self.guilds}")
            elif "allowed_channel_id" in data and data["allowed_channel_id"]:
                self.allowed_channel_id = int(data["allowed_channel_id"])
                log.warning("⚠️ Old config.json format – using legacy allowed_channel_id")
        except Exception as e:
            log.error(f"❌ Failed to read config.json: {e}")

    def _save_to_file(self) -> None:
        """Save current guilds dict to config.json (best-effort on Railway)."""
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump({"guilds": self.guilds}, f, indent=2)
        except Exception as e:
            log.warning(f"⚠️ Could not save config.json (ephemeral storage?): {e}")

    # ── Public API ─────────────────────────────────────────────────

    def get_channel_for_guild(self, guild_id: int) -> int | None:
        """
        Return the configured channel for the given guild.
        Priority:
          1) In-memory cache (loaded from PostgreSQL or file at startup)
          2) Legacy flat allowed_channel_id (migration)
          3) DEFAULT_CHANNEL_ID environment variable (always reliable on Railway)
        """
        guild_key = str(guild_id)
        if guild_key in self.guilds:
            return self.guilds[guild_key]
        if self.allowed_channel_id:
            return self.allowed_channel_id
        # NEW: env-based fallback — always works even after Railway restart
        return DEFAULT_CHANNEL_ID

    async def set_allowed_channel(
        self,
        guild_id: int,
        channel_id: int,
        guild_name: str = "Unknown",
        channel_name: str = "Unknown",
    ) -> None:
        """
        FIXED: Now async — saves to PostgreSQL first (persistent),
        then file (best-effort), then updates in-memory cache.
        Also writes to GitHub logs.txt so the mapping survives bot updates.
        """
        guild_key = str(guild_id)
        self.guilds[guild_key] = channel_id
        self.allowed_channel_id = channel_id  # legacy compat

        # 1. Save to PostgreSQL (survives Railway restarts)
        await self._save_to_db(guild_key, channel_id)

        # 2. Save to file (best-effort, may not survive restart on Railway)
        self._save_to_file()

        # 3. Save to GitHub logs.txt (survives bot updates – the new fallback layer)
        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            save_channel_link_to_github,
            guild_id, guild_name, channel_id, channel_name,
        )

        log.info(f"✅ Channel set: guild {guild_id} → channel {channel_id}")


config = Config()

# ╔══════════════════════════════════════════════════════════════╗
# ║                        BOT SETUP                            ║
# ╚══════════════════════════════════════════════════════════════╝
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


# UPDATED: per-guild channel check with DEFAULT_CHANNEL_ID fallback
def is_allowed_channel(interaction: discord.Interaction) -> bool:
    guild_id = interaction.guild.id if interaction.guild else None
    if guild_id is None:
        return False
    channel_id = config.get_channel_for_guild(guild_id)
    if channel_id is None:
        return False
    return interaction.channel_id == channel_id


# ╔══════════════════════════════════════════════════════════════╗
# ║              GLOBAL INTERACTION CHECK (guild guard)         ║
# ║  UPDATED: checks against ALLOWED_GUILD_IDS list             ║
# ╚══════════════════════════════════════════════════════════════╝
async def global_interaction_check(interaction: discord.Interaction) -> bool:
    # UPDATED: multi-guild check
    if interaction.guild is None or interaction.guild.id not in ALLOWED_GUILD_IDS:
        lang = get_user_lang(interaction)
        msg = TRANSLATIONS[lang]["wrong_guild"]
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return False
    return True


# ╔══════════════════════════════════════════════════════════════╗
# ║               LANGUAGE SELECTION VIEW                       ║
# ╚══════════════════════════════════════════════════════════════╝
class LanguageSelectView(discord.ui.View):
    def __init__(self, original_interaction: discord.Interaction) -> None:
        super().__init__(timeout=60)
        self.original_interaction = original_interaction

    @discord.ui.button(label="English", style=discord.ButtonStyle.primary, emoji="🇬🇧")
    async def english_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._set_language(interaction, "en")

    @discord.ui.button(label="العربية", style=discord.ButtonStyle.primary, emoji="🇸🇦")
    async def arabic_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._set_language(interaction, "ar")

    async def _set_language(self, interaction: discord.Interaction, lang: str) -> None:
        if interaction.user.id != self.original_interaction.user.id:
            user_lang = get_user_lang(interaction)
            await interaction.response.send_message(TRANSLATIONS[user_lang]["not_for_you"], ephemeral=True)
            return

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content=TRANSLATIONS[lang]["lang_selected"], view=self)

        # Capture the lang-selection message so it can be deleted later
        lang_message = await interaction.original_response()

        confirm_view = ConfirmView(self.original_interaction.user, self.original_interaction, lang)
        confirm_message = await interaction.followup.send(
            TRANSLATIONS[lang]["confirm_prompt"], view=confirm_view, ephemeral=True
        )

        # Pass references into ConfirmView so generate_link can clean them up
        confirm_view.lang_message    = lang_message
        confirm_view.confirm_message = confirm_message
        self.stop()

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        try:
            await self.original_interaction.edit_original_response(content=TRANSLATIONS["en"]["timeout_msg"], view=None)
        except Exception:
            pass


# ╔══════════════════════════════════════════════════════════════╗
# ║                   CONFIRMATION VIEW (Yes / No)              ║
# ╚══════════════════════════════════════════════════════════════╝
class ConfirmView(discord.ui.View):
    def __init__(self, original_user: discord.User | discord.Member, original_interaction: discord.Interaction, language: str) -> None:
        super().__init__(timeout=60)
        self.original_user = original_user
        self.original_interaction = original_interaction
        self.language = language
        self.lang_message: discord.Message | None = None
        self.confirm_message: discord.Message | None = None

        yes_btn = discord.ui.Button(label=TRANSLATIONS[language]["yes_label"], style=discord.ButtonStyle.green, emoji="🎬")
        yes_btn.callback = self.yes_callback

        no_btn = discord.ui.Button(label=TRANSLATIONS[language]["no_label"], style=discord.ButtonStyle.red, emoji="🚫")
        no_btn.callback = self.no_callback

        self.add_item(yes_btn)
        self.add_item(no_btn)

    async def yes_callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.original_user.id:
            await interaction.response.send_message(TRANSLATIONS[self.language]["not_for_you"], ephemeral=True)
            return

        await interaction.response.edit_message(content=TRANSLATIONS[self.language]["progress"], view=None)
        await self.generate_link(interaction)
        self.stop()

    async def no_callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.original_user.id:
            await interaction.response.send_message(TRANSLATIONS[self.language]["not_for_you"], ephemeral=True)
            return

        await interaction.response.edit_message(content=TRANSLATIONS[self.language]["cancelled"], view=None)
        await log_user_activity(interaction, "Cancelled", "User clicked No", language=self.language)
        self.stop()

    async def generate_link(self, interaction: discord.Interaction) -> None:
        lang = self.language
        t = TRANSLATIONS[lang]

        # ── NEW: Try GitHub cookies first, fall back to local folder ──────
        chosen_file_name: str | None = None
        cookie_content:   str | None = None
        tmp_path:         str | None = None   # temp file path passed to checker

        if COOKIES_GITHUB_REPO and COOKIES_GITHUB_PATH is not None:
            # Fetch list of .txt files from GitHub (blocking → run in thread)
            github_names = await asyncio.to_thread(fetch_github_cookie_list)

            if not github_names:
                log.warning("⚠️ No .txt files in GitHub cookies folder – trying local fallback")
            else:
                chosen_file_name = await asyncio.to_thread(
                    pick_github_cookie_rotation, github_names
                )
                cookie_content = await asyncio.to_thread(
                    fetch_github_cookie_content, chosen_file_name
                )
                if cookie_content is None:
                    log.warning(f"⚠️ Could not download {chosen_file_name} – trying local fallback")
                    chosen_file_name = None

        # ── LOCAL FALLBACK: use local cookies/ folder if GitHub failed ────
        if cookie_content is None:
            if not COOKIES_FOLDER.exists():
                await interaction.edit_original_response(content=t["no_cookies_folder"])
                await log_user_activity(interaction, "Error", "Cookies folder missing", language=self.language)
                return

            txt_files = list(COOKIES_FOLDER.glob("*.txt"))
            if not txt_files:
                await interaction.edit_original_response(content=t["no_cookie_files"])
                await log_user_activity(interaction, "Error", "No cookie files found", language=self.language)
                return

            chosen_path = pick_cookie_file(txt_files)
            chosen_file_name = chosen_path.name
            log.info(f"📂 Using local cookie file: {chosen_file_name}")

            # Write to temp file for checker
            try:
                cookie_content = chosen_path.read_text(encoding="utf-8")
            except Exception as e:
                log.error(f"❌ Failed to read local cookie file: {e}")
                await interaction.edit_original_response(content=t["unexpected_error"])
                return

        # ── Write content to a temp file so check_cookie_file can read it ─
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(cookie_content)
                tmp_path = tmp.name
        except Exception as e:
            log.error(f"❌ Failed to write temp cookie file: {e}")
            await interaction.edit_original_response(content=t["unexpected_error"])
            return

        log.info(f"🎯 {interaction.user} → checking cookie: {chosen_file_name}")

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(check_cookie_file, tmp_path),
                timeout=SCRIPT_TIMEOUT
            )
        except asyncio.TimeoutError:
            await interaction.edit_original_response(content=t["timeout"])
            await log_user_activity(interaction, "Timeout", "Cookie validation timeout", used_txt_files=[chosen_file_name], language=self.language)
            return
        except Exception as e:
            log.error(f"❌ Checker error: {e}")
            await interaction.edit_original_response(content=t["unexpected_error"])
            await log_user_activity(interaction, "Error", f"Exception: {str(e)[:80]}", used_txt_files=[chosen_file_name], language=self.language)
            return
        finally:
            # ALWAYS clean up the temp file
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

        if result:
            embed = discord.Embed(
                title=t["success_title"],
                description=t["success_desc"].format(link=result),
                color=discord.Color.from_rgb(229, 9, 20),
                timestamp=datetime.now(),
            )
            embed.set_thumbnail(url="https://upload.wikimedia.org/wikipedia/commons/0/08/Netflix_2015_logo.svg")
            embed.set_footer(text=t["footer"] + "  •  ⚡ 994817247061225633 Utility  •  Netflix Bot 🎬")

            await interaction.edit_original_response(content=None, embed=embed)
            tv_message = await interaction.followup.send(t["tv_instruction"], ephemeral=True)

            # Cleanup logic
            channel = interaction.channel
            command_message = None
            try:
                async for msg in channel.history(limit=10):
                    if (msg.author == interaction.client.user and msg.interaction_metadata and msg.interaction_metadata.id == interaction.id):
                        command_message = msg
                        break
            except Exception as e:
                log.warning(f"⚠️ Could not fetch command message: {e}")

            original_response = await interaction.original_response()
            asyncio.create_task(cleanup_messages(
                channel=channel,
                command_message=command_message,
                original_response=original_response,
                followup_message=tv_message,
                lang_message=self.lang_message,
                confirm_message=self.confirm_message,
                delay_seconds=CLEANUP_DELAY_SECONDS,
            ))

            log.info(f"🔗 Link sent to {interaction.user} – cleanup in {CLEANUP_DELAY_SECONDS}s")
            await log_user_activity(interaction, "✅ Success", "Link generated", used_txt_files=[chosen_file_name], language=self.language)
        else:
            await interaction.edit_original_response(content=t["cookie_invalid"])
            await log_user_activity(interaction, "❌ Failed", "Cookie invalid or expired", used_txt_files=[chosen_file_name], language=self.language)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        try:
            await self.original_interaction.edit_original_response(content=TRANSLATIONS[self.language]["timeout_msg"], view=None)
        except Exception:
            pass


# ╔══════════════════════════════════════════════════════════════╗
# ║                      CLEANUP TASK                           ║
# ╚══════════════════════════════════════════════════════════════╝
async def cleanup_messages(
    channel: discord.TextChannel,
    command_message: discord.Message | None,
    original_response: discord.WebhookMessage,
    followup_message: discord.Message | None,
    delay_seconds: int,
    lang_message: discord.Message | None = None,
    confirm_message: discord.Message | None = None,
) -> None:
    await asyncio.sleep(delay_seconds)
    for msg in (command_message, original_response, followup_message, lang_message, confirm_message):
        if msg is not None:
            try:
                await msg.delete()
            except Exception:
                pass
    log.info("🧹 Cleanup complete.")


# ╔══════════════════════════════════════════════════════════════╗
# ║               /channel COMMAND  (Admin only)                ║
# ║  UPDATED: stores channel per guild ID                       ║
# ╚══════════════════════════════════════════════════════════════╝
@bot.tree.command(name="channel", description="📌 Set the text channel where the bot will work (Admin only)")
@app_commands.default_permissions(administrator=True)
async def set_channel(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
    if not interaction.user.guild_permissions.administrator:
        lang = get_user_lang(interaction)
        msg = "❌ You need administrator permissions." if lang == "en" else "❌ تحتاج إلى صلاحيات المسؤول."
        await interaction.response.send_message(msg, ephemeral=True)
        return

    # UPDATED: store channel per guild (now async → saves to PostgreSQL + GitHub logs.txt)
    guild_id = interaction.guild.id
    guild_name = interaction.guild.name if interaction.guild else "Unknown"
    await config.set_allowed_channel(guild_id, channel.id, guild_name=guild_name, channel_name=channel.name)

    lang = get_user_lang(interaction)
    success_msg = f"✅ Bot will now **only** respond in {channel.mention}." if lang == "en" else f"✅ البوت سيعمل الآن **فقط** في {channel.mention}."
    await interaction.response.send_message(success_msg, ephemeral=True)

    # Bilingual pinned setup embed
    embed = discord.Embed(
        title="🎬 Netflix Link Generator  |  مولد روابط نتفليكس",
        description=f"🇬🇧 **English:**\n{TRANSLATIONS['en']['setup_desc']}\n\n🇸🇦 **العربية:**\n{TRANSLATIONS['ar']['setup_desc']}",
        color=discord.Color.from_rgb(229, 9, 20),
        timestamp=datetime.now(),
    )
    embed.set_footer(text="⚡ 994817247061225633 Utility  •  Netflix Bot 🎬")
    try:
        setup_msg = await channel.send(embed=embed)
        await setup_msg.pin()
        log.info(f"📌 Pinned setup message in #{channel.name} (ID: {channel.id})")
    except discord.Forbidden:
        log.warning(f"⚠️ Missing permissions to send/pin in #{channel.name}")
    except Exception as e:
        log.error(f"❌ Failed to send setup message: {e}")


# ╔══════════════════════════════════════════════════════════════╗
# ║                /create COMMAND                              ║
# ║  UPDATED: pre-checks guild + channel before proceeding      ║
# ╚══════════════════════════════════════════════════════════════╝
@bot.tree.command(name="create", description="🎬 Generate a Netflix PC login link from a random cookie file")
async def create(interaction: discord.Interaction) -> None:
    user_lang = get_user_lang(interaction)

    # PRE-CHECK 1: guild must be allowed (NEW)
    if interaction.guild is None or interaction.guild.id not in ALLOWED_GUILD_IDS:
        await interaction.response.send_message(TRANSLATIONS[user_lang]["wrong_guild"], ephemeral=True)
        return

    # PRE-CHECK 2: channel must be configured (UPDATED: uses per-guild lookup)
    if not is_allowed_channel(interaction):
        guild_id = interaction.guild.id
        channel_id = config.get_channel_for_guild(guild_id)
        if channel_id is None:
            await interaction.response.send_message(TRANSLATIONS[user_lang]["wrong_channel_no_config"], ephemeral=True)
        else:
            allowed_channel = bot.get_channel(channel_id)
            mention = allowed_channel.mention if allowed_channel else "the designated channel"
            await interaction.response.send_message(TRANSLATIONS[user_lang]["wrong_channel_with_config"].format(channel=mention), ephemeral=True)
        return

    view = LanguageSelectView(interaction)
    await interaction.response.send_message(TRANSLATIONS["en"]["lang_prompt"], view=view, ephemeral=True)


# ╔══════════════════════════════════════════════════════════════╗
# ║                       BOT EVENTS                            ║
# ║  UPDATED: multi-guild logging and sync                      ║
# ╚══════════════════════════════════════════════════════════════╝
@bot.event
async def on_ready() -> None:
    log.info("━" * 60)
    log.info(f"🤖 Logged in as : {bot.user}  (ID: {bot.user.id})")
    # UPDATED: log all allowed guilds
    log.info(f"🏠 Allowed guilds: {ALLOWED_GUILD_IDS}")
    # NEW: log cookie source
    if COOKIES_GITHUB_REPO:
        log.info(f"🍪 Cookie source : GitHub → {COOKIES_GITHUB_REPO}/{COOKIES_GITHUB_PATH} [{COOKIES_GITHUB_BRANCH}]")
    else:
        log.info(f"🍪 Cookie source : Local → {COOKIES_FOLDER.resolve()}")

    # NEW: Initialize persistent config (PostgreSQL or file fallback)
    await config.init_db()

    # NEW: log channel config state on startup
    for gid in ALLOWED_GUILD_IDS:
        ch = config.get_channel_for_guild(gid)
        if ch:
            log.info(f"📌 Guild {gid} → channel {ch} (configured)")
        else:
            log.warning(f"⚠️ Guild {gid} → no channel configured (set DEFAULT_CHANNEL_ID or run /channel)")
    if GITHUB_REPO and GITHUB_FILE_PATH:
        log.info(f"📡 GitHub log target: {GITHUB_REPO}/{GITHUB_FILE_PATH}")
    else:
        log.warning("⚠️ GitHub logging is DISABLED – REMOTE_LOG_URL not set or invalid")
    log.info("━" * 60)

    bot.tree.interaction_check = global_interaction_check

    # UPDATED: sync commands to ALL allowed guilds
    for guild_id in ALLOWED_GUILD_IDS:
        guild_obj = discord.Object(id=guild_id)
        try:
            synced = await bot.tree.sync(guild=guild_obj)
            log.info(f"✅ Synced {len(synced)} slash command(s) to guild {guild_id}")
        except Exception as e:
            log.error(f"❌ Failed to sync commands to guild {guild_id}: {e}")


# ╔══════════════════════════════════════════════════════════════╗
# ║                        ENTRY POINT                          ║
# ╚══════════════════════════════════════════════════════════════╝
if __name__ == "__main__":
    COOKIES_FOLDER.mkdir(exist_ok=True)
    bot.run(DISCORD_BOT_TOKEN)
