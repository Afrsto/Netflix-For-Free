import os
import json
import time
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

try:
    import asyncpg
    HAS_ASYNCPG = True
except ImportError:
    HAS_ASYNCPG = False

from netflix_checker import check_cookie_file


COOKIES_FOLDER        = Path("cookies")
SCRIPT_TIMEOUT        = 30
CONFIG_FILE           = Path("config.json")
USER_LOG_FILE         = Path("users.txt")
CLEANUP_DELAY_SECONDS = 60

EGYPT_TZ = ZoneInfo("Africa/Cairo")


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("NetflixBot")

# #region agent log
def _agent_debug_log(location: str, message: str, data: dict, hypothesis_id: str) -> None:
    try:
        import json as _json
        _payload = {"sessionId": "6fa734", "location": location, "message": message, "data": data, "timestamp": int(time.time() * 1000), "hypothesisId": hypothesis_id}
        with open(Path(__file__).resolve().parent / "debug-6fa734.log", "a", encoding="utf-8") as _f:
            _f.write(_json.dumps(_payload) + "\n")
    except Exception:
        pass
# #endregion


DISCORD_BOT_TOKEN = os.environ.get("DISCORD_TOKEN", "").strip()
GITHUB_TOKEN      = os.environ.get("GITHUB_TOKEN", "").strip()

if not DISCORD_BOT_TOKEN:
    raise ValueError("❌ Missing DISCORD_TOKEN environment variable")

ALLOWED_GUILD_IDS: list[int] = []
for _key, _val in os.environ.items():
    if _key.startswith("GUILD_ID_") and _val and _val.strip().isdigit():
        ALLOWED_GUILD_IDS.append(int(_val.strip()))

_legacy_guild = os.environ.get("GUILD_ID", "").strip()
if _legacy_guild.isdigit() and int(_legacy_guild) not in ALLOWED_GUILD_IDS:
    ALLOWED_GUILD_IDS.append(int(_legacy_guild))

if ALLOWED_GUILD_IDS:
    log.info(f"✅ Guild restriction active: {ALLOWED_GUILD_IDS}")
else:
    log.info("🌐 No GUILD_ID set – bot will operate in ALL servers it is invited to")

_default_ch = os.environ.get("DEFAULT_CHANNEL_ID", "").strip()
DEFAULT_CHANNEL_ID: int | None = int(_default_ch) if _default_ch.isdigit() else None

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()


def _parse_github_blob_url(url: str) -> tuple[str | None, str | None]:
    if not url:
        return None, None
    p = urlparse(url)
    if p.netloc != "github.com":
        return None, None
    parts = p.path.strip("/").split("/")
    if len(parts) < 2:
        return None, None
    repo = f"{parts[0]}/{parts[1]}"
    if "blob" in parts:
        bi = parts.index("blob")
        if bi + 2 < len(parts):
            return repo, "/".join(parts[bi + 2:])
    return repo, None


def _parse_github_tree_url(url: str) -> tuple[str | None, str | None, str]:
    if not url:
        return None, None, "main"
    p = urlparse(url)
    if p.netloc != "github.com":
        return None, None, "main"
    parts = p.path.strip("/").split("/")
    if len(parts) < 2:
        return None, None, "main"
    repo   = f"{parts[0]}/{parts[1]}"
    branch = "main"
    path   = ""
    if "tree" in parts:
        ti = parts.index("tree")
        if ti + 1 < len(parts):
            branch = parts[ti + 1]
        if ti + 2 < len(parts):
            path = "/".join(parts[ti + 2:])
    return repo, path, branch


REMOTE_LOG_URL  = os.environ.get("REMOTE_LOG_URL", "").strip() or None
CHANNEL_LOG_URL = os.environ.get("CHANNEL_LOG_URL", "").strip() or None
BAN_USERS_URL   = os.environ.get(
    "BAN_USERS_URL",
    "https://github.com/Afrsto/bot-users/blob/main/ban-users.txt",
).strip() or None
BAN_SERVERS_URL = os.environ.get(
    "BAN_SERVERS_URL",
    "https://github.com/Afrsto/bot-users/blob/main/ban-servers.txt",
).strip() or None
ADMIN_USERS_URL = os.environ.get(
    "ADMIN_USERS_URL",
    "https://github.com/Afrsto/bot-users/blob/main/admin-users.txt",
).strip() or None
COOKIES_REPO_URL = (
    os.environ.get("COOKIES_REPO_URL", "").strip()
    or os.environ.get("COOKIES_REPO_UR", "").strip()
    or None
)

GITHUB_REPO, GITHUB_FILE_PATH               = _parse_github_blob_url(REMOTE_LOG_URL)
CHANNEL_LOG_GITHUB_REPO, CHANNEL_LOG_GITHUB_PATH = _parse_github_blob_url(CHANNEL_LOG_URL)
BAN_USERS_GITHUB_REPO, BAN_USERS_GITHUB_PATH     = _parse_github_blob_url(BAN_USERS_URL)
BAN_SERVERS_GITHUB_REPO, BAN_SERVERS_GITHUB_PATH = _parse_github_blob_url(BAN_SERVERS_URL)
ADMIN_USERS_GITHUB_REPO, ADMIN_USERS_GITHUB_PATH = _parse_github_blob_url(ADMIN_USERS_URL)
COOKIES_GITHUB_REPO, COOKIES_GITHUB_PATH, COOKIES_GITHUB_BRANCH = _parse_github_tree_url(COOKIES_REPO_URL)

if not GITHUB_REPO:
    log.warning("⚠️ REMOTE_LOG_URL not set or invalid – GitHub user logging disabled")
if not BAN_USERS_GITHUB_REPO:
    log.warning("⚠️ BAN_USERS_URL not set – ban system disabled")
if not BAN_SERVERS_GITHUB_REPO:
    log.warning("⚠️ BAN_SERVERS_URL not set – server ban system disabled")
if not ADMIN_USERS_GITHUB_REPO:
    log.warning("⚠️ ADMIN_USERS_URL not set – custom admin system disabled")
if COOKIES_GITHUB_REPO:
    log.info(f"✅ Cookies → GitHub {COOKIES_GITHUB_REPO}/{COOKIES_GITHUB_PATH} [{COOKIES_GITHUB_BRANCH}]")
else:
    log.warning("⚠️ COOKIES_REPO_URL not set – using local cookies folder")


def _gh_client():
    if not GITHUB_TOKEN:
        return None
    return Github(GITHUB_TOKEN)


def _get_repo(repo_name: str | None):
    if not repo_name:
        return None
    gh = _gh_client()
    if not gh:
        return None
    try:
        return gh.get_repo(repo_name)
    except GithubException as exc:
        log.error(f"❌ Cannot access GitHub repo {repo_name}: {exc}")
        return None


def _read_github_file(repo_name: str | None, file_path: str | None) -> tuple[str, str | None]:
    if not repo_name or not file_path:
        return "", None
    repo = _get_repo(repo_name)
    if not repo:
        return "", None
    try:
        contents = repo.get_contents(file_path)
        raw      = b64decode(contents.content).decode("utf-8")
        return raw, contents.sha
    except GithubException as exc:
        if exc.status == 404:
            return "", None
        log.error(f"❌ Failed to read {repo_name}/{file_path}: {exc}")
        return "", None


def _write_github_file(
    repo_name: str | None,
    file_path: str | None,
    content: str,
    commit_msg: str,
    max_retries: int = 3,
) -> bool:
    if not repo_name or not file_path:
        return False
    repo = _get_repo(repo_name)
    if not repo:
        return False

    for attempt in range(1, max_retries + 1):
        try:
            try:
                contents    = repo.get_contents(file_path)
                current_sha = contents.sha
            except GithubException as exc:
                if exc.status == 404:
                    current_sha = None
                else:
                    raise

            if current_sha:
                repo.update_file(file_path, commit_msg, content, current_sha, branch="main")
            else:
                repo.create_file(file_path, commit_msg, content, branch="main")

            log.info(f"✅ GitHub write OK ({repo_name}/{file_path}) attempt {attempt}")
            return True

        except GithubException as exc:
            status = exc.status
            msg    = exc.data.get("message", "") if isinstance(exc.data, dict) else str(exc.data)
            if status in (409, 500, 502, 503) and attempt < max_retries:
                wait = 1 if status == 409 else 2
                log.warning(f"⚠️ GitHub write {status} – retry {attempt}/{max_retries} in {wait}s")
                time.sleep(wait)
                continue
            log.error(f"❌ GitHub write failed after {attempt} attempt(s): {status} – {msg}")
            return False
        except Exception as exc:
            log.error(f"❌ Unexpected GitHub write error (attempt {attempt}): {exc}")
            if attempt < max_retries:
                time.sleep(1)
                continue
            return False

    return False


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


def get_locale_info(locale_str: str) -> tuple[str, str, str]:
    locale  = str(locale_str)
    country = LOCALE_TO_COUNTRY.get(locale) or LOCALE_TO_COUNTRY.get(locale.split("-")[0], "Unknown")
    tz_key  = LOCALE_TO_TZ.get(locale) or LOCALE_TO_TZ.get(locale.split("-")[0])
    if tz_key:
        try:
            tz             = ZoneInfo(tz_key)
            local_time_str = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            local_time_str = "N/A"
            tz_key         = "Unknown"
    else:
        local_time_str = "N/A"
        tz_key         = "Unknown"
    return country, local_time_str, tz_key


_admin_registry: dict[int, dict] = {}


def load_admins_from_github() -> dict[int, dict]:
    raw, _ = _read_github_file(ADMIN_USERS_GITHUB_REPO, ADMIN_USERS_GITHUB_PATH)
    registry: dict[int, dict] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            parts = {kv.split("=", 1)[0].strip(): kv.split("=", 1)[1].strip()
                     for kv in line.split("|") if "=" in kv}
            uid = int(parts.get("user_id", "0"))
            if uid:
                registry[uid] = {
                    "username": parts.get("username", str(uid)),
                    "added_by": parts.get("added_by", "system"),
                    "added_at": parts.get("added_at", "unknown"),
                }
        except Exception:
            continue
    log.info(f"👮 Loaded {len(registry)} admin(s) from GitHub")
    return registry


def _serialize_admins(registry: dict[int, dict]) -> str:
    lines = ["# Admin users – managed automatically by the bot", ""]
    for uid, info in registry.items():
        lines.append(
            f"user_id={uid} | username={info['username']} "
            f"| added_by={info['added_by']} | added_at={info['added_at']}"
        )
    return "\n".join(lines) + "\n"


def save_admins_to_github(registry: dict[int, dict]) -> bool:
    content = _serialize_admins(registry)
    now_str = datetime.now(EGYPT_TZ).strftime("%Y-%m-%d %H:%M")
    return _write_github_file(
        ADMIN_USERS_GITHUB_REPO,
        ADMIN_USERS_GITHUB_PATH,
        content,
        f"👮 Update admin list [{now_str} EGY]",
    )


BOT_OWNER_ID:   int = 994817247061225633
BOT_COADMIN_ID: int = 1138625081942233273

_PRIVILEGED_IDS: frozenset[int] = frozenset({BOT_OWNER_ID, BOT_COADMIN_ID})


def is_admin(user_id: int) -> bool:
    return user_id in _PRIVILEGED_IDS or user_id in _admin_registry


def is_owner(user_id: int) -> bool:
    return user_id == BOT_OWNER_ID


_banned_user_ids:   set[int]      = set()
_ban_attempt_counts: dict[int, int] = {}


def load_banned_users_from_github() -> set[int]:
    if not BAN_USERS_GITHUB_REPO or not BAN_USERS_GITHUB_PATH:
        log.warning("⚠️ BAN_USERS_URL not configured – ban list disabled")
        return set()
    raw, _ = _read_github_file(BAN_USERS_GITHUB_REPO, BAN_USERS_GITHUB_PATH)
    banned: set[int] = set()
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            banned.add(int(line.split("|")[0].strip()))
        except (ValueError, IndexError):
            continue
    log.info(f"🚫 Loaded {len(banned)} banned user(s)")
    return banned


def _write_ban_list(lines: list[str]) -> bool:
    content = "\n".join(lines) + ("\n" if lines else "")
    now_str = datetime.now(EGYPT_TZ).strftime("%Y-%m-%d %H:%M")
    return _write_github_file(
        BAN_USERS_GITHUB_REPO,
        BAN_USERS_GITHUB_PATH,
        content,
        f"🚫 Update ban list [{now_str} EGY]",
    )


def add_ban_to_github(user_id: int, username: str) -> bool:
    if not BAN_USERS_GITHUB_REPO or not BAN_USERS_GITHUB_PATH:
        return False
    raw, _ = _read_github_file(BAN_USERS_GITHUB_REPO, BAN_USERS_GITHUB_PATH)
    lines  = [ln for ln in raw.splitlines() if ln.strip()]
    for ln in lines:
        if ln.startswith("#"):
            continue
        try:
            if int(ln.split("|")[0].strip()) == user_id:
                log.info(f"ℹ️ User {user_id} already in ban list")
                return True
        except (ValueError, IndexError):
            continue
    now_str = datetime.now(EGYPT_TZ).strftime("%Y-%m-%d %H:%M:%S")
    lines.append(f"{user_id} | username={username} | banned_at={now_str} | attempts=0")
    return _write_ban_list(lines)


def remove_ban_from_github(user_id: int) -> bool:
    if not BAN_USERS_GITHUB_REPO or not BAN_USERS_GITHUB_PATH:
        return False
    raw, _ = _read_github_file(BAN_USERS_GITHUB_REPO, BAN_USERS_GITHUB_PATH)
    lines  = [ln for ln in raw.splitlines() if ln.strip()]
    new_lines, removed = [], False
    for ln in lines:
        if ln.startswith("#"):
            new_lines.append(ln)
            continue
        try:
            if int(ln.split("|")[0].strip()) == user_id:
                removed = True
                continue
        except (ValueError, IndexError):
            pass
        new_lines.append(ln)
    if not removed:
        return True
    return _write_ban_list(new_lines)


def update_ban_attempts_on_github(user_id: int, attempts: int) -> None:
    if not BAN_USERS_GITHUB_REPO or not BAN_USERS_GITHUB_PATH:
        return
    raw, _ = _read_github_file(BAN_USERS_GITHUB_REPO, BAN_USERS_GITHUB_PATH)
    lines, updated = raw.splitlines(), False
    new_lines = []
    for ln in lines:
        stripped = ln.strip()
        if not stripped or stripped.startswith("#"):
            new_lines.append(ln)
            continue
        try:
            if int(stripped.split("|")[0].strip()) == user_id:
                parts     = [p.strip() for p in stripped.split("|")]
                new_parts = [f"attempts={attempts}" if p.startswith("attempts=") else p for p in parts]
                new_lines.append(" | ".join(new_parts))
                updated = True
                continue
        except (ValueError, IndexError):
            pass
        new_lines.append(ln)
    if updated:
        _write_ban_list(new_lines)


def is_user_banned(user_id: int) -> bool:
    return user_id in _banned_user_ids


_banned_guild_ids: set[int] = set()


def load_banned_servers_from_github() -> set[int]:
    if not BAN_SERVERS_GITHUB_REPO or not BAN_SERVERS_GITHUB_PATH:
        log.warning("⚠️ BAN_SERVERS_URL not configured – server ban list disabled")
        return set()
    raw, _ = _read_github_file(BAN_SERVERS_GITHUB_REPO, BAN_SERVERS_GITHUB_PATH)
    banned: set[int] = set()
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            banned.add(int(line.split("|")[0].strip()))
        except (ValueError, IndexError):
            continue
    log.info(f"🚫 Loaded {len(banned)} banned server(s)")
    return banned


def _write_server_ban_list(lines: list[str]) -> bool:
    content = "\n".join(lines) + ("\n" if lines else "")
    now_str = datetime.now(EGYPT_TZ).strftime("%Y-%m-%d %H:%M")
    return _write_github_file(
        BAN_SERVERS_GITHUB_REPO,
        BAN_SERVERS_GITHUB_PATH,
        content,
        f"🚫 Update server ban list [{now_str} EGY]",
    )


def add_server_ban_to_github(guild_id: int, guild_name: str, reason: str) -> bool:
    if not BAN_SERVERS_GITHUB_REPO or not BAN_SERVERS_GITHUB_PATH:
        return False
    raw, _ = _read_github_file(BAN_SERVERS_GITHUB_REPO, BAN_SERVERS_GITHUB_PATH)
    lines  = [ln for ln in raw.splitlines() if ln.strip()]
    for ln in lines:
        if ln.startswith("#"):
            continue
        try:
            if int(ln.split("|")[0].strip()) == guild_id:
                log.info(f"ℹ️ Guild {guild_id} already in server ban list")
                return True
        except (ValueError, IndexError):
            continue
    now_str = datetime.now(EGYPT_TZ).strftime("%Y-%m-%d %H:%M:%S")
    safe_reason = reason.replace("|", "-")
    lines.append(
        f"{guild_id} | guild_name={guild_name} | reason={safe_reason} | banned_at={now_str}"
    )
    return _write_server_ban_list(lines)


def remove_server_ban_from_github(guild_id: int) -> bool:
    if not BAN_SERVERS_GITHUB_REPO or not BAN_SERVERS_GITHUB_PATH:
        return False
    raw, _ = _read_github_file(BAN_SERVERS_GITHUB_REPO, BAN_SERVERS_GITHUB_PATH)
    lines  = [ln for ln in raw.splitlines() if ln.strip()]
    new_lines, removed = [], False
    for ln in lines:
        if ln.startswith("#"):
            new_lines.append(ln)
            continue
        try:
            if int(ln.split("|")[0].strip()) == guild_id:
                removed = True
                continue
        except (ValueError, IndexError):
            pass
        new_lines.append(ln)
    if not removed:
        return True
    return _write_server_ban_list(new_lines)


def is_server_banned(guild_id: int) -> bool:
    return guild_id in _banned_guild_ids


def record_ban_attempt(user_id: int) -> int:
    _ban_attempt_counts[user_id] = _ban_attempt_counts.get(user_id, 0) + 1
    count = _ban_attempt_counts[user_id]
    try:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, update_ban_attempts_on_github, user_id, count)
    except RuntimeError:
        pass
    return count


def update_users_txt_on_github(new_line: str) -> bool:
    if not GITHUB_REPO or not GITHUB_FILE_PATH:
        log.debug("GitHub logging disabled")
        return False
    raw, _ = _read_github_file(GITHUB_REPO, GITHUB_FILE_PATH)
    lines  = raw.splitlines(keepends=True)
    if len(lines) >= 500:
        lines = lines[-499:]
        log.info("🗂️ users.txt trimmed to 500 lines")
    content = "".join(lines) + new_line
    now_str = datetime.now(EGYPT_TZ).strftime("%Y-%m-%d %H:%M")
    return _write_github_file(
        GITHUB_REPO,
        GITHUB_FILE_PATH,
        content,
        f"📝 Log entry [{now_str} EGY]",
    )


COOLDOWN_HOURS = 24


def check_user_cooldown(user_id: int) -> tuple[bool, float]:
    """
    Check if the user is within the 24-hour cooldown period.

    Returns:
        (is_on_cooldown: bool, remaining_hours: float)
        If not on cooldown → (False, 0.0)
        If on cooldown    → (True,  hours_remaining)

    Only ✅ Success entries count; failed/error attempts are ignored.
    Admins are always exempt.
    """
    if is_admin(user_id):
        return False, 0.0

    if not GITHUB_REPO or not GITHUB_FILE_PATH:
        return False, 0.0

    raw, _ = _read_github_file(GITHUB_REPO, GITHUB_FILE_PATH)
    if not raw:
        return False, 0.0

    user_id_str     = str(user_id)
    last_success_dt: datetime | None = None

    for line in raw.splitlines():
        # Only care about successful generations for this user
        if f"🆔 ID: {user_id_str}" not in line:
            continue
        if "📊 Status: ✅ Success" not in line:
            continue
        # Parse timestamp: "[2026-05-26 19:52:07 EGY]"
        try:
            ts_part = line.split("]")[0].lstrip("[").strip()
            # Remove trailing timezone label (e.g. " EGY")
            ts_clean = " ".join(ts_part.split()[:2])
            entry_dt = datetime.strptime(ts_clean, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=EGYPT_TZ
            )
            if last_success_dt is None or entry_dt > last_success_dt:
                last_success_dt = entry_dt
        except Exception:
            continue

    if last_success_dt is None:
        return False, 0.0

    now_egypt      = datetime.now(EGYPT_TZ)
    elapsed_hours  = (now_egypt - last_success_dt).total_seconds() / 3600.0

    if elapsed_hours < COOLDOWN_HOURS:
        remaining = COOLDOWN_HOURS - elapsed_hours
        return True, remaining

    return False, 0.0


def save_channel_link_to_github(guild_id: int, guild_name: str, channel_id: int, channel_name: str) -> None:
    if not CHANNEL_LOG_GITHUB_REPO or not CHANNEL_LOG_GITHUB_PATH:
        return
    raw, _ = _read_github_file(CHANNEL_LOG_GITHUB_REPO, CHANNEL_LOG_GITHUB_PATH)
    now_str   = datetime.now(EGYPT_TZ).strftime("%Y-%m-%d %H:%M:%S")
    new_entry = (
        f"CHANNEL_LINK | guild_id={guild_id} | guild_name={guild_name} | "
        f"channel_id={channel_id} | channel_name={channel_name} | set_at={now_str}\n"
    )
    lines = [ln for ln in raw.splitlines(keepends=True)
             if not (ln.startswith("CHANNEL_LINK") and f"guild_id={guild_id}" in ln)]
    lines.append(new_entry)
    _write_github_file(
        CHANNEL_LOG_GITHUB_REPO,
        CHANNEL_LOG_GITHUB_PATH,
        "".join(lines),
        f"📌 Update channel link: guild {guild_id} → channel {channel_id}",
    )


def load_channel_links_from_github() -> dict[str, int]:
    if not CHANNEL_LOG_GITHUB_REPO or not CHANNEL_LOG_GITHUB_PATH:
        return {}
    raw, _ = _read_github_file(CHANNEL_LOG_GITHUB_REPO, CHANNEL_LOG_GITHUB_PATH)
    result: dict[str, int] = {}
    for line in raw.splitlines():
        if not line.startswith("CHANNEL_LINK"):
            continue
        try:
            parts = {kv.split("=", 1)[0].strip(): kv.split("=", 1)[1].strip()
                     for kv in line.split("|")[1:] if "=" in kv}
            result[parts["guild_id"]] = int(parts["channel_id"])
        except Exception:
            continue
    log.info(f"📥 Loaded {len(result)} channel link(s) from GitHub logs.txt")
    return result


_used_cookie_files:        list[Path] = []
_used_github_cookie_names: list[str]  = []


def pick_cookie_file(txt_files: list[Path]) -> Path:
    global _used_cookie_files
    _used_cookie_files = [f for f in _used_cookie_files if f in txt_files]
    remaining = [f for f in txt_files if f not in _used_cookie_files]
    if not remaining:
        log.info("🔄 All cookie files used – resetting rotation")
        _used_cookie_files.clear()
        remaining = list(txt_files)
    chosen = random.choice(remaining)
    _used_cookie_files.append(chosen)
    return chosen


def pick_github_cookie_rotation(filenames: list[str]) -> str:
    global _used_github_cookie_names
    _used_github_cookie_names = [f for f in _used_github_cookie_names if f in filenames]
    remaining = [f for f in filenames if f not in _used_github_cookie_names]
    if not remaining:
        log.info("🔄 All GitHub cookie files used – resetting rotation")
        _used_github_cookie_names.clear()
        remaining = list(filenames)
    chosen = random.choice(remaining)
    _used_github_cookie_names.append(chosen)
    return chosen


_cookies_repo_cache = None


def _get_cookies_repo():
    """Cached lookup of the cookies repo. Only caches on success, so a
    transient GitHub failure doesn't get "stuck" — the next call still
    retries the lookup instead of returning None forever."""
    global _cookies_repo_cache
    if _cookies_repo_cache is not None:
        return _cookies_repo_cache
    repo = _get_repo(COOKIES_GITHUB_REPO)
    if repo is not None:
        _cookies_repo_cache = repo
    return repo


def _fetch_github_cookie_list_in_path(folder_path: str) -> list[str]:
    repo = _get_cookies_repo()
    if not repo:
        return []
    try:
        contents = repo.get_contents(folder_path, ref=COOKIES_GITHUB_BRANCH)
        return [c.name for c in contents if c.type == "file" and c.name.endswith(".txt")]
    except GithubException as exc:
        log.error(f"❌ Failed to list GitHub path {folder_path}: {exc}")
        return []


def _fetch_github_cookie_content_in_path(folder_path: str, filename: str) -> str | None:
    repo = _get_cookies_repo()
    if not repo:
        return None
    try:
        file_path   = f"{folder_path.rstrip('/')}/{filename}"
        content_obj = repo.get_contents(file_path, ref=COOKIES_GITHUB_BRANCH)
        return b64decode(content_obj.content).decode("utf-8")
    except GithubException as exc:
        log.error(f"❌ Failed to download {folder_path}/{filename}: {exc}")
        return None


QUALITY_FOLDER_MAP: dict[str, str] = {
    "hd":  "Basic",
    "fhd": "Standard",
    "uhd": "Premium",
}


async def log_user_activity(
    interaction: discord.Interaction,
    condition: str,
    result: str,
    used_txt_files: list[str] | None = None,
    language: str | None = None,
    quality: str | None = None,
    device: str | None = None,
) -> None:
    now_egypt    = datetime.now(EGYPT_TZ).strftime("%Y-%m-%d %H:%M:%S")
    user         = interaction.user
    guild        = interaction.guild
    locale_str   = str(interaction.locale) if interaction.locale else "en"
    country_name, local_time, local_tz = get_locale_info(locale_str)

    member_since = login_date_server = roles_str = "N/A"
    if guild:
        try:
            member = await guild.fetch_member(user.id)
            if member.joined_at:
                member_since      = member.joined_at.strftime("%Y-%m-%d")
                login_date_server = member.joined_at.strftime("%Y-%m-%d %H:%M:%S")
            roles_str = ", ".join(r.name for r in member.roles[1:]) or "None"
        except Exception as exc:
            log.warning(f"⚠️ Could not fetch member {user.id}: {exc}")

    channel_name   = getattr(interaction.channel, "name", "N/A")
    txt_files_str  = ", ".join(used_txt_files) if used_txt_files else "N/A"
    lang_label     = {"ar": "Arabic 🇸🇦", "en": "English 🇬🇧"}.get(language or "", language or "N/A")
    quality_folder = QUALITY_FOLDER_MAP.get(quality or "", "N/A")
    quality_label  = {"hd": "HD 720p 📺", "fhd": "Full HD 1080p 🎬", "uhd": "Ultra HD 4K 💎"}.get(quality or "", "N/A")
    device_label   = {"pc": "PC", "phone": "Phone", "tv": "TV"}.get(device or "", "N/A")

    line = (
        f"[{now_egypt} EGY] "
        f"👤 User: {user} (Display: {user.display_name}) | "
        f"🆔 ID: {user.id} | "
        f"🗓️  Account Created: {user.created_at.strftime('%Y-%m-%d')} | "
        f"📅 Joined Server: {login_date_server} | "
        f"🏠 Server: {guild.name if guild else 'DM'} (ID: {guild.id if guild else 'N/A'}) | "
        f"💬 Channel: #{channel_name} | "
        f"🎭 Roles: [{roles_str}] | "
        f"🌐 Language: {lang_label} | "
        f"🎞️ Quality: {quality_label} (folder: {quality_folder}) | "
        f"📱 Device: {device_label} | "
        f"📄 Files Used: [{txt_files_str}] | "
        f"📊 Status: {condition} | "
        f"🔎 Result: {result}\n"
    )

    try:
        with open(USER_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as exc:
        log.error(f"❌ Failed to write local log: {exc}")

    await asyncio.to_thread(update_users_txt_on_github, line)


TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "lang_prompt":            "🌐 **Please select your language:**\n🌐 **الرجاء اختيار اللغة:**",
        "lang_selected":          "✅ Language selected: **English**",
        "confirm_prompt":         "🎬 **Please choose the streaming quality**\n",
        "device_prompt":          "📱 **Choose how you want to register: PC, Phone, or TV**",
        "pc_label":               "PC",
        "phone_label":            "Phone",
        "tv_label":               "TV",
        "progress":               "⏳ **Generating your Netflix link… please wait.**",
        "no_cookies_folder":      "❌ Cookies folder not found. Please contact the administrator.",
        "no_cookie_files":        "❌ No accounts available right now. Please try again later.",
        "timeout":                "⌛ Validation took too long. Please try again later.",
        "unexpected_error":       "⚠️ An unexpected error occurred. Please try again.",
        "cookie_invalid":         "❌ The selected session is invalid or expired. Please try again.",
        "success_title":          "✅ 🎬 Netflix Login Link Ready!",
        "success_desc":           "🔗 Click the link below to log in automatically:\n\n{link}",
        "footer":                 "⚠️ This link is for personal use only – do not share it.",
        "hd_label":               "HD 720p",
        "fhd_label":              "Full HD 1080p",
        "uhd_label":              "Ultra HD 4K",
        "cancelled":              "🚫 Process cancelled.",
        "not_for_you":            "🚫 You cannot interact with this menu.",
        "timeout_msg":            "⏰ Request timed out due to inactivity.",
        "wrong_channel_no_config": "⚠️ No channel configured. Admins must run `/channel` first.",
        "wrong_channel_with_config": "❌ This command can only be used in {channel}.",
        "wrong_guild":            "❌ This bot is not available in this server.",
        "not_admin":              "❌ You do not have permission to use this command.",
        "cooldown":               "⏳ You already generated a link recently.\n\n⌛ Please wait **{hours}h {minutes}m** before creating another one.",
        "retry_prompt":           "❌ The attempt failed. Please try again.\n\n❌ فشلت المحاولة. يرجى المحاولة مرة أخرى.\n\n🔄 **Try Again | حاول مرة أخرى**",
        "retry_button":           "🔄 Try Again | حاول مرة أخرى",
        "setup_desc": (
            "Welcome! 👋 Use the `/create` command to generate a Netflix login link.\n\n"
            "**📋 How to use:**\n"
            "1️⃣  Type `/create` in this channel.\n"
            "2️⃣  Select your preferred language.\n"
            "3️⃣  Choose your streaming quality: **HD 720p**, **Full HD 1080p**, or **Ultra HD 4K**.\n"
            "4️⃣  Choose your device: **PC**, **Phone**, or **TV**.\n"
            "5️⃣  Wait a few seconds for your personal link.\n\n"
            "*⚠️ Note: Links are single-use. Messages auto-delete after 1 minute for privacy.*"
        ),
    },
    "ar": {
        "lang_prompt":            "🌐 **Please select your language:**\n🌐 **الرجاء اختيار اللغة:**",
        "lang_selected":          "\u200f✅ تم اختيار اللغة: **العربية**",
        "confirm_prompt":         "\u200f🎬 **يرجى اختيار جودة العرض**\n",
        "device_prompt":          "\u200f📱 **اختر طريقة التسجيل: الكمبيوتر، الهاتف، أو التلفاز**",
        "pc_label":               "PC",
        "phone_label":            "Phone",
        "tv_label":               "TV",
        "progress":               "\u200f⏳ **جاري إنشاء الرابط الخاص بك… يرجى الانتظار.**",
        "no_cookies_folder":      "\u200f❌ مجلد ملفات تعريف الارتباط غير موجود. يرجى الاتصال بالمسؤول.",
        "no_cookie_files":        "\u200f❌ لا توجد حسابات متاحة حالياً. حاول لاحقاً.",
        "timeout":                "\u200f⌛ استغرق التحقق وقتاً طويلاً. يرجى المحاولة مرة أخرى.",
        "unexpected_error":       "\u200f⚠️ حدث خطأ غير متوقع.",
        "cookie_invalid":         "\u200f❌ الحساب المختار غير صالح أو منتهي الصلاحية. حاول مجدداً.",
        "success_title":          "\u200f✅ 🎬 رابط تسجيل الدخول إلى نتفليكس جاهز!",
        "success_desc":           "\u200f🔗 انقر على الرابط أدناه لتسجيل الدخول تلقائياً:\n\n{link}",
        "footer":                 "\u200f⚠️ هذا الرابط للاستخدام الشخصي فقط – يُمنع مشاركته.",
        "hd_label":               "HD 720p",
        "fhd_label":              "Full HD 1080p",
        "uhd_label":              "Ultra HD 4K",
        "cancelled":              "\u200f🚫 تم إلغاء العملية.",
        "not_for_you":            "\u200f🚫 لا يمكنك التفاعل مع هذه القائمة.",
        "timeout_msg":            "\u200f⏰ انتهت مهلة الطلب بسبب عدم التفاعل.",
        "wrong_channel_no_config": "\u200f⚠️ لم يتم إعداد القناة. يجب على المسؤول استخدام `/channel` أولاً.",
        "wrong_channel_with_config": "\u200f❌ لا يمكن استخدام هذا الأمر إلا في {channel}.",
        "wrong_guild":            "\u200f❌ هذا البوت غير متاح في هذا السيرفر.",
        "not_admin":              "\u200f❌ ليس لديك صلاحية استخدام هذا الأمر.",
        "cooldown":               "\u200f⏳ لقد حصلت على رابط مؤخراً.\n\n\u200f⌛ انتظر **{hours} ساعة و{minutes} دقيقة** قبل إنشاء رابط جديد.",
        "retry_prompt":           "❌ The attempt failed. Please try again.\n\n❌ فشلت المحاولة. يرجى المحاولة مرة أخرى.\n\n🔄 **Try Again | حاول مرة أخرى**",
        "retry_button":           "🔄 Try Again | حاول مرة أخرى",
        "setup_desc": (
            "مرحباً! 👋 استخدم أمر `/create` لإنشاء رابط تسجيل دخول لـ نتفليكس.\n\n"
            "**📋 طريقة الاستخدام:**\n"
            "1️⃣  اكتب `/create` في هذه القناة.\n"
            "2️⃣  اختر لغتك المفضلة.\n"
            "3️⃣  اختر جودة العرض: **HD 720p** أو **Full HD 1080p** أو **Ultra HD 4K**.\n"
            "4️⃣  اختر جهازك: **PC** أو **Phone** أو **TV**.\n"
            "5️⃣  انتظر بضع ثوانٍ للحصول على رابطك الشخصي.\n\n"
            "\u200f*⚠️ ملاحظة: الروابط للاستخدام مرة واحدة. يتم حذف الرسائل تلقائياً بعد دقيقة.*"
        ),
    },
}


def get_user_lang(interaction: discord.Interaction) -> str:
    return "ar" if str(interaction.locale).startswith("ar") else "en"


_setup_message_ids: dict[int, dict[str, int]] = {}
_SETUP_TRACKER_FILE = Path("setup_messages.json")


def _load_setup_tracker() -> None:
    global _setup_message_ids
    if _SETUP_TRACKER_FILE.exists():
        try:
            with open(_SETUP_TRACKER_FILE) as f:
                raw = json.load(f)
            _setup_message_ids = {int(k): v for k, v in raw.items()}
        except Exception as exc:
            log.warning(f"⚠️ Could not load setup tracker: {exc}")


def _save_setup_tracker() -> None:
    try:
        with open(_SETUP_TRACKER_FILE, "w") as f:
            json.dump({str(k): v for k, v in _setup_message_ids.items()}, f, indent=2)
    except Exception as exc:
        log.warning(f"⚠️ Could not save setup tracker: {exc}")


NETFLIX_RED = discord.Color.from_rgb(229, 9, 20)
FOOTER_TEXT = "⚡ X2 Salah Utility  •  Netflix Bot 🎬"
NETFLIX_LOGO = "https://upload.wikimedia.org/wikipedia/commons/0/08/Netflix_2015_logo.svg"


def _build_welcome_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎬 Netflix Link Generator  |  مولد روابط نتفليكس",
        color=NETFLIX_RED,
        timestamp=datetime.now(EGYPT_TZ),
    )
    embed.set_thumbnail(url=NETFLIX_LOGO)

    embed.add_field(
        name="🇬🇧 Welcome | مرحباً 🇸🇦",
        value=(
            "Welcome to the **Netflix Link Generator**! 👋\n"
            "This bot generates a personal Netflix login link on demand.\n\n"
            "مرحباً بك في **مولد روابط نتفليكس**! 👋\n"
            "يقوم هذا البوت بإنشاء رابط تسجيل دخول شخصي لنتفليكس عند الطلب."
        ),
        inline=False,
    )

    embed.add_field(
        name="📋 How to use | طريقة الاستخدام",
        value=(
            "**🇬🇧 English Steps:**\n"
            "1️⃣  Type `/create` in this channel\n"
            "2️⃣  Select your language\n"
            "3️⃣  Choose streaming quality (**HD**, **FHD**, or **4K**)\n"
            "4️⃣  Choose your device (**PC**, **Phone**, or **TV**)\n"
            "5️⃣  Receive your personal login link\n\n"
            "**🇸🇦 الخطوات بالعربي:**\n"
            "1️⃣  اكتب `/create` في هذه القناة\n"
            "2️⃣  اختر لغتك\n"
            "3️⃣  اختر جودة العرض (**HD** أو **FHD** أو **4K**)\n"
            "4️⃣  اختر جهازك (**PC** أو **Phone** أو **TV**)\n"
            "5️⃣  احصل على رابط تسجيل الدخول الشخصي"
        ),
        inline=False,
    )

    embed.add_field(
        name="🛠️ Available Commands | الأوامر المتاحة",
        value=(
            "`/create` – Generate a Netflix login link | إنشاء رابط نتفليكس\n"
            "`/ban` – Block a user (Admin) | حظر مستخدم (مسؤول)\n"
            "`/unban` – Unblock a user (Admin) | رفع الحظر (مسؤول)\n"
            "`/banserver` – Ban a server (Admin) | حظر سيرفر (مسؤول)\n"
            "`/unbanserver` – Unban a server (Admin) | رفع حظر سيرفر (مسؤول)\n"
            "`/channel` – Set bot channel | تعيين قناة البوت\n"
            "`/admin` – Manage bot admins (Admin) | إدارة المشرفين (مسؤول)"
        ),
        inline=False,
    )

    embed.set_footer(text=FOOTER_TEXT)
    return embed


def _build_rules_embed() -> discord.Embed:
    embed = discord.Embed(
        title="📜 Rules & Guidelines  |  القواعد والإرشادات",
        color=discord.Color.from_rgb(230, 126, 34),
        timestamp=datetime.now(EGYPT_TZ),
    )

    embed.add_field(
        name="🇬🇧 Rules (English)",
        value=(
            "🚫 **Rule 1:** Any suspicious activity will result in a permanent ban.\n\n"
            "✅ **Rule 2:** Links are for personal use only – do **not** share them with others.\n\n"
            "🔄 **Rule 3:** Messages auto-delete after **1 minute** for privacy."
        ),
        inline=False,
    )

    embed.add_field(
        name="🇸🇦 القواعد (العربية)",
        value=(
            "🚫 **القاعدة 1:** أي نشاط مشبوه يؤدي إلى حظر دائم.\n\n"
            "✅ **القاعدة 2:** الروابط للاستخدام الشخصي فقط – يُمنع مشاركتها مع الآخرين.\n\n"
            "🔄 **القاعدة 3:** يتم حذف الرسائل تلقائياً بعد **دقيقة واحدة** لحماية الخصوصية."
        ),
        inline=False,
    )

    embed.set_footer(text=FOOTER_TEXT)
    return embed



def _count_txt_files_in_folder(quality_folder: str) -> int:
    """Count .txt files for a quality tier (local or GitHub)."""
    if COOKIES_GITHUB_REPO and COOKIES_GITHUB_PATH is not None:
        base_path = (COOKIES_GITHUB_PATH.rstrip("/") + "/" + quality_folder) if COOKIES_GITHUB_PATH else quality_folder
        try:
            names = _fetch_github_cookie_list_in_path(base_path)
            return len(names)
        except Exception:
            pass
    # Fallback to local
    local_dir = COOKIES_FOLDER / quality_folder
    if not local_dir.exists():
        return 0
    return len(list(local_dir.glob("*.txt")))


async def _build_stats_embed() -> discord.Embed:
    # The three counts are independent, so fetch them concurrently instead
    # of one after another — and run them in a background thread since
    # _count_txt_files_in_folder hits the GitHub API synchronously, which
    # would otherwise block the bot's entire event loop while it waits.
    premium_count, standard_count, basic_count = await asyncio.gather(
        asyncio.to_thread(_count_txt_files_in_folder, "Premium"),
        asyncio.to_thread(_count_txt_files_in_folder, "Standard"),
        asyncio.to_thread(_count_txt_files_in_folder, "Basic"),
    )

    embed = discord.Embed(
        title="📊 Account Stock  |  المخزون الحالي",
        color=discord.Color.from_rgb(46, 204, 113),
        timestamp=datetime.now(EGYPT_TZ),
    )
    embed.add_field(
        name="💎 Ultra HD 4K — Premium",
        value=f"**{premium_count}** account(s) available",
        inline=True,
    )
    embed.add_field(
        name="🎬 Full HD 1080p — Standard",
        value=f"**{standard_count}** account(s) available",
        inline=True,
    )
    embed.add_field(
        name="📺 HD 720p — Basic",
        value=f"**{basic_count}** account(s) available",
        inline=True,
    )
    embed.set_footer(text=FOOTER_TEXT)
    return embed


async def send_or_update_setup_messages(channel: discord.TextChannel, guild_id: int) -> None:
    stored = _setup_message_ids.get(guild_id, {})
    welcome_embed = _build_welcome_embed()
    rules_embed   = _build_rules_embed()

    welcome_msg_id = stored.get("welcome")
    welcome_msg: discord.Message | None = None

    if welcome_msg_id:
        try:
            welcome_msg = await channel.fetch_message(welcome_msg_id)
            await welcome_msg.edit(embed=welcome_embed)
            log.info(f"✏️ Updated existing welcome message {welcome_msg_id}")
        except discord.NotFound:
            welcome_msg = None
        except Exception as exc:
            log.warning(f"⚠️ Could not update welcome message: {exc}")
            welcome_msg = None

    if welcome_msg is None:
        try:
            welcome_msg = await channel.send(embed=welcome_embed)
            await welcome_msg.pin()
            log.info(f"📌 Pinned welcome message {welcome_msg.id} in #{channel.name}")
        except discord.Forbidden:
            log.warning(f"⚠️ No permission to send/pin in #{channel.name}")
            return
        except Exception as exc:
            log.error(f"❌ Failed to send welcome message: {exc}")
            return

    rules_msg_id = stored.get("rules")
    rules_msg: discord.Message | None = None

    if rules_msg_id:
        try:
            rules_msg = await channel.fetch_message(rules_msg_id)
            await rules_msg.edit(embed=rules_embed)
            log.info(f"✏️ Updated existing rules message {rules_msg_id}")
        except discord.NotFound:
            rules_msg = None
        except Exception as exc:
            log.warning(f"⚠️ Could not update rules message: {exc}")
            rules_msg = None

    if rules_msg is None:
        try:
            rules_msg = await channel.send(embed=rules_embed)
            await rules_msg.pin()
            log.info(f"📌 Pinned rules message {rules_msg.id} in #{channel.name}")
        except Exception as exc:
            log.error(f"❌ Failed to send rules message: {exc}")

    # ── Stats message ────────────────────────────────────────────────────
    stats_embed  = await _build_stats_embed()
    stats_msg_id = stored.get("stats")
    stats_msg: discord.Message | None = None

    if stats_msg_id:
        try:
            stats_msg = await channel.fetch_message(stats_msg_id)
            await stats_msg.edit(embed=stats_embed)
            log.info(f"✏️ Updated existing stats message {stats_msg_id}")
        except discord.NotFound:
            stats_msg = None
        except Exception as exc:
            log.warning(f"⚠️ Could not update stats message: {exc}")
            stats_msg = None

    if stats_msg is None:
        try:
            stats_msg = await channel.send(embed=stats_embed)
            log.info(f"📊 Sent stats message {stats_msg.id} in #{channel.name}")
        except Exception as exc:
            log.error(f"❌ Failed to send stats message: {exc}")

    _setup_message_ids[guild_id] = {
        "welcome": welcome_msg.id if welcome_msg else None,
        "rules":   rules_msg.id   if rules_msg   else None,
        "stats":   stats_msg.id   if stats_msg   else None,
    }
    _save_setup_tracker()


class Config:
    def __init__(self) -> None:
        self.guilds:             dict[str, int] = {}
        self.allowed_channel_id: int | None     = None
        self._db_pool = None

    async def init_db(self) -> None:
        if DATABASE_URL and HAS_ASYNCPG:
            try:
                dsn            = DATABASE_URL.replace("postgres://", "postgresql://", 1)
                self._db_pool  = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
                async with self._db_pool.acquire() as conn:
                    await conn.execute("""
                        CREATE TABLE IF NOT EXISTS guild_config (
                            guild_id   TEXT  PRIMARY KEY,
                            channel_id BIGINT NOT NULL
                        )
                    """)
                    rows = await conn.fetch("SELECT guild_id, channel_id FROM guild_config")
                    for row in rows:
                        self.guilds[row["guild_id"]] = int(row["channel_id"])
                log.info(f"✅ PostgreSQL loaded – {len(self.guilds)} guild(s)")
            except Exception as exc:
                log.error(f"❌ PostgreSQL init failed: {exc} – using file fallback")
                self._db_pool = None
                self._load_from_file()
        else:
            log.warning("⚠️ PostgreSQL unavailable – using file/env/GitHub fallback")
            self._load_from_file()

        loop         = asyncio.get_event_loop()
        github_links = await loop.run_in_executor(None, load_channel_links_from_github)
        for gid, cid in github_links.items():
            if gid not in self.guilds:
                self.guilds[gid] = cid
                log.info(f"🔄 Restored from GitHub logs.txt: guild {gid} → channel {cid}")

    async def _save_to_db(self, guild_id: str, channel_id: int) -> None:
        if not self._db_pool:
            return
        try:
            async with self._db_pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO guild_config (guild_id, channel_id) VALUES ($1, $2)
                    ON CONFLICT (guild_id) DO UPDATE SET channel_id = EXCLUDED.channel_id
                """, guild_id, channel_id)
        except Exception as exc:
            log.error(f"❌ PostgreSQL save failed: {exc}")

    def _load_from_file(self) -> None:
        if not CONFIG_FILE.exists():
            return
        try:
            with open(CONFIG_FILE) as f:
                data = json.load(f)
            if isinstance(data.get("guilds"), dict):
                self.guilds = {str(k): int(v) for k, v in data["guilds"].items()}
            elif data.get("allowed_channel_id"):
                self.allowed_channel_id = int(data["allowed_channel_id"])
        except Exception as exc:
            log.error(f"❌ Failed to read config.json: {exc}")

    def _save_to_file(self) -> None:
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump({"guilds": self.guilds}, f, indent=2)
        except Exception as exc:
            log.warning(f"⚠️ Could not save config.json: {exc}")

    def get_channel_for_guild(self, guild_id: int) -> int | None:
        guild_key = str(guild_id)
        if guild_key in self.guilds:
            return self.guilds[guild_key]
        if self.allowed_channel_id:
            return self.allowed_channel_id
        return DEFAULT_CHANNEL_ID

    async def set_allowed_channel(
        self,
        guild_id: int,
        channel_id: int,
        guild_name: str = "Unknown",
        channel_name: str = "Unknown",
    ) -> None:
        # #region agent log
        _t0 = time.monotonic()
        _agent_debug_log("bot.py:set_allowed_channel:entry", "set_allowed_channel started", {"guild_id": guild_id, "channel_id": channel_id}, "A")
        # #endregion
        guild_key             = str(guild_id)
        self.guilds[guild_key] = channel_id
        self.allowed_channel_id = channel_id
        await self._save_to_db(guild_key, channel_id)
        self._save_to_file()
        # #region agent log
        _agent_debug_log("bot.py:set_allowed_channel:pre_github", "local save done, starting GitHub write", {"elapsed_ms": round((time.monotonic() - _t0) * 1000)}, "A")
        # #endregion
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, save_channel_link_to_github,
            guild_id, guild_name, channel_id, channel_name,
        )
        # #region agent log
        _agent_debug_log("bot.py:set_allowed_channel:post_github", "GitHub write finished", {"elapsed_ms": round((time.monotonic() - _t0) * 1000)}, "A")
        # #endregion
        log.info(f"✅ Channel set: guild {guild_id} → channel {channel_id}")


config = Config()


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


def is_allowed_channel(interaction: discord.Interaction) -> bool:
    guild_id = interaction.guild.id if interaction.guild else None
    if guild_id is None:
        return False
    channel_id = config.get_channel_for_guild(guild_id)
    if channel_id is None:
        return False
    return interaction.channel_id == channel_id


def _guild_allowed(guild_id: int | None) -> bool:
    if not guild_id:
        return False
    if not ALLOWED_GUILD_IDS:
        return True
    return guild_id in ALLOWED_GUILD_IDS


async def global_interaction_check(interaction: discord.Interaction) -> bool:
    if is_user_banned(interaction.user.id):
        attempts = record_ban_attempt(interaction.user.id)
        lang     = get_user_lang(interaction)
        msg = (
            f"🚫 You have been banned from using this bot. (Attempt #{attempts})"
            if lang == "en"
            else f"\u200f🚫 تم حظرك من استخدام هذا البوت. (المحاولة رقم #{attempts})"
        )
        cmd_name = interaction.command.name if interaction.command else "?"
        log.warning(f"🚫 Banned user {interaction.user} tried /{cmd_name} – attempt #{attempts}")
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return False

    guild_id = interaction.guild.id if interaction.guild else None
    if guild_id and is_server_banned(guild_id):
        lang = get_user_lang(interaction)
        msg  = (
            "🚫 This server has been banned from using this bot due to a violation of the bot rules."
            if lang == "en"
            else "\u200f🚫 تم حظر هذا السيرفر من استخدام البوت بسبب انتهاك قواعد الاستخدام."
        )
        cmd_name = interaction.command.name if interaction.command else "?"
        log.warning(f"🚫 Banned guild {guild_id} tried /{cmd_name} via {interaction.user}")
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return False

    if not _guild_allowed(guild_id):
        lang = get_user_lang(interaction)
        msg  = TRANSLATIONS[lang]["wrong_guild"]
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return False

    return True



async def _delete_failed_cookie(quality_folder: str, filename: str) -> None:
    """Delete a cookie file that produced a failed result."""
    # Try GitHub first
    if COOKIES_GITHUB_REPO and COOKIES_GITHUB_PATH is not None:
        base_path = (COOKIES_GITHUB_PATH.rstrip("/") + "/" + quality_folder) if COOKIES_GITHUB_PATH else quality_folder
        file_path = f"{base_path.rstrip('/')}/{filename}"
        try:
            repo = _get_cookies_repo()
            if repo:
                content_obj = repo.get_contents(file_path, ref=COOKIES_GITHUB_BRANCH)
                repo.delete_file(
                    file_path,
                    f"🗑️ Remove failed cookie [{filename}]",
                    content_obj.sha,
                    branch=COOKIES_GITHUB_BRANCH,
                )
                log.info(f"🗑️ Deleted failed GitHub cookie: {file_path}")
                return
        except Exception as exc:
            log.warning(f"⚠️ Could not delete GitHub cookie {file_path}: {exc}")

    # Fallback: delete from local folder
    local_path = COOKIES_FOLDER / quality_folder / filename
    if local_path.exists():
        try:
            local_path.unlink()
            log.info(f"🗑️ Deleted failed local cookie: {local_path}")
        except Exception as exc:
            log.warning(f"⚠️ Could not delete local cookie {local_path}: {exc}")


async def _refresh_stats_message(guild_id: int) -> None:
    """Rebuild and push the stats embed for a guild's configured channel."""
    channel_id = config.get_channel_for_guild(guild_id)
    if not channel_id:
        return
    channel = bot.get_channel(channel_id)
    if not channel:
        return
    stored       = _setup_message_ids.get(guild_id, {})
    stats_msg_id = stored.get("stats")
    stats_embed  = await _build_stats_embed()
    if stats_msg_id:
        try:
            stats_msg = await channel.fetch_message(stats_msg_id)
            await stats_msg.edit(embed=stats_embed)
            log.info(f"📊 Refreshed stats message for guild {guild_id}")
            return
        except discord.NotFound:
            pass
        except Exception as exc:
            log.warning(f"⚠️ Could not refresh stats message: {exc}")
    # If message is gone, send a new one and track it
    try:
        new_msg = await channel.send(embed=stats_embed)
        _setup_message_ids.setdefault(guild_id, {})["stats"] = new_msg.id
        _save_setup_tracker()
        log.info(f"📊 Re-sent stats message for guild {guild_id}")
    except Exception as exc:
        log.error(f"❌ Failed to re-send stats message: {exc}")


async def _generate_and_send_link(
    interaction: discord.Interaction,
    language: str,
    quality_key: str,
    device: str,
    lang_message: discord.Message | None = None,
    confirm_message: discord.Message | None = None,
) -> None:
    lang           = language
    t              = TRANSLATIONS[lang]
    quality_folder = QUALITY_FOLDER_MAP[quality_key]

    chosen_file_name: str | None = None
    cookie_content:   str | None = None
    tmp_path:         str | None = None

    if COOKIES_GITHUB_REPO and COOKIES_GITHUB_PATH is not None:
        base_path    = (COOKIES_GITHUB_PATH.rstrip("/") + "/" + quality_folder) if COOKIES_GITHUB_PATH else quality_folder
        github_names = await asyncio.to_thread(_fetch_github_cookie_list_in_path, base_path)
        if github_names:
            chosen_file_name = await asyncio.to_thread(pick_github_cookie_rotation, github_names)
            cookie_content   = await asyncio.to_thread(
                _fetch_github_cookie_content_in_path, base_path, chosen_file_name
            )
            if cookie_content is None:
                chosen_file_name = None

    if cookie_content is None:
        local_dir = COOKIES_FOLDER / quality_folder
        if not local_dir.exists():
            local_dir = COOKIES_FOLDER
        if not local_dir.exists():
            await interaction.edit_original_response(content=t["no_cookies_folder"])
            await log_user_activity(interaction, "Error", "Cookies folder missing", language=lang, device=device)
            return
        txt_files = list(local_dir.glob("*.txt"))
        if not txt_files:
            await interaction.edit_original_response(content=t["no_cookie_files"])
            await log_user_activity(interaction, "Error", "No cookie files", language=lang, device=device)
            return
        chosen_path      = pick_cookie_file(txt_files)
        chosen_file_name = chosen_path.name
        try:
            cookie_content = chosen_path.read_text(encoding="utf-8")
        except Exception as exc:
            log.error(f"❌ Failed to read local cookie: {exc}")
            await interaction.edit_original_response(content=t["unexpected_error"])
            return

    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as tmp:
            tmp.write(cookie_content)
            tmp_path = tmp.name
    except Exception as exc:
        log.error(f"❌ Failed to write temp cookie file: {exc}")
        await interaction.edit_original_response(content=t["unexpected_error"])
        return

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(check_cookie_file, tmp_path, device),
            timeout=SCRIPT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        await interaction.edit_original_response(content=t["timeout"])
        await log_user_activity(
            interaction, "Timeout", "Validation timeout",
            used_txt_files=[chosen_file_name], language=lang, quality=quality_key, device=device,
        )
        return
    except Exception as exc:
        log.error(f"❌ Checker error: {exc}")
        await interaction.edit_original_response(content=t["unexpected_error"])
        await log_user_activity(
            interaction, "Error", f"Exception: {str(exc)[:80]}",
            used_txt_files=[chosen_file_name], language=lang, quality=quality_key, device=device,
        )
        return
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    channel         = interaction.channel
    command_message = None
    try:
        async for msg in channel.history(limit=10):
            if (msg.author == interaction.client.user
                    and msg.interaction_metadata
                    and msg.interaction_metadata.id == interaction.id):
                command_message = msg
                break
    except Exception:
        pass

    if result:
        embed = discord.Embed(
            title=t["success_title"],
            description=t["success_desc"].format(link=result),
            color=NETFLIX_RED,
            timestamp=datetime.now(),
        )
        embed.set_thumbnail(url=NETFLIX_LOGO)
        embed.set_footer(text=t["footer"] + "  •  X2 Salah Utility 🎬")
        await interaction.edit_original_response(content=None, embed=embed)

        original_response = await interaction.original_response()
        asyncio.create_task(cleanup_messages(
            channel=channel,
            command_message=command_message,
            original_response=original_response,
            followup_message=None,
            lang_message=lang_message,
            confirm_message=confirm_message,
            delay_seconds=CLEANUP_DELAY_SECONDS,
        ))
        await log_user_activity(
            interaction, "✅ Success", "Link generated",
            used_txt_files=[chosen_file_name], language=lang, quality=quality_key, device=device,
        )
        # Refresh the stock counter shown in the channel
        if interaction.guild:
            asyncio.create_task(_refresh_stats_message(interaction.guild.id))
    else:
        retry_view = RetryView(interaction, lang)
        await interaction.edit_original_response(
            content=TRANSLATIONS["en"]["retry_prompt"],
            view=retry_view,
        )

        # Same 1-minute cleanup as the success path. Previously nothing was
        # scheduled here, so the language pick, quality/device pick, and the
        # Retry prompt itself (plus, if the user tapped Retry, the orphaned
        # "progress" stub left behind once a fresh message chain spawned)
        # all stayed in the channel forever instead of self-deleting like
        # every other step in the flow. Message references are captured by
        # ID, so this still cleans them up correctly even if their content
        # gets edited again (e.g. by a Retry click) before the delay fires.
        retry_message = await interaction.original_response()
        asyncio.create_task(cleanup_messages(
            channel=channel,
            command_message=command_message,
            original_response=retry_message,
            followup_message=None,
            lang_message=lang_message,
            confirm_message=None,
            delay_seconds=CLEANUP_DELAY_SECONDS,
        ))

        await log_user_activity(
            interaction, "❌ Failed", "Cookie invalid",
            used_txt_files=[chosen_file_name], language=lang, quality=quality_key, device=device,
        )
        # Delete the failed cookie file and refresh the stock counter
        if chosen_file_name and quality_key:
            quality_folder = QUALITY_FOLDER_MAP.get(quality_key, "")
            if quality_folder:
                asyncio.create_task(_delete_failed_cookie(quality_folder, chosen_file_name))
        if interaction.guild:
            asyncio.create_task(_refresh_stats_message(interaction.guild.id))


class RetryView(discord.ui.View):
    """Shown to the user after a failed attempt so they can retry without re-running /create."""

    def __init__(self, original_interaction: discord.Interaction, language: str) -> None:
        # Match CLEANUP_DELAY_SECONDS: the message this view is attached to
        # now gets deleted on that same schedule, so the button shouldn't be
        # considered "live" any longer than the message actually exists.
        super().__init__(timeout=CLEANUP_DELAY_SECONDS)
        self.original_interaction = original_interaction
        self.language = language

    @discord.ui.button(label="🔄 Try Again | حاول مرة أخرى", style=discord.ButtonStyle.danger)
    async def retry_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.original_interaction.user.id:
            await interaction.response.send_message(
                TRANSLATIONS[self.language]["not_for_you"], ephemeral=True
            )
            return
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=TRANSLATIONS[self.language]["progress"], view=None
        )
        # Re-run the full generation flow via a fresh ConfirmView-like call
        view = LanguageSelectView(interaction)
        await interaction.followup.send(
            TRANSLATIONS["en"]["lang_prompt"], view=view, ephemeral=True
        )
        self.stop()

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        try:
            await self.original_interaction.edit_original_response(
                content=TRANSLATIONS[self.language]["timeout_msg"], view=None
            )
        except Exception:
            pass


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
            await interaction.response.send_message(
                TRANSLATIONS[get_user_lang(interaction)]["not_for_you"], ephemeral=True
            )
            return
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content=TRANSLATIONS[lang]["lang_selected"], view=self)
        lang_message  = await interaction.original_response()
        confirm_view  = ConfirmView(self.original_interaction.user, self.original_interaction, lang)
        confirm_message = await interaction.followup.send(
            TRANSLATIONS[lang]["confirm_prompt"], view=confirm_view, ephemeral=True
        )
        confirm_view.lang_message    = lang_message
        confirm_view.confirm_message = confirm_message
        self.stop()

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        try:
            await self.original_interaction.edit_original_response(
                content=TRANSLATIONS["en"]["timeout_msg"], view=None
            )
        except Exception:
            pass


class ConfirmView(discord.ui.View):
    def __init__(
        self,
        original_user: discord.User | discord.Member,
        original_interaction: discord.Interaction,
        language: str,
    ) -> None:
        super().__init__(timeout=60)
        self.original_user        = original_user
        self.original_interaction = original_interaction
        self.language             = language
        self.lang_message:    discord.Message | None = None
        self.confirm_message: discord.Message | None = None
        self.quality:         str | None             = None

        for key, label, style, emoji in [
            ("hd",  TRANSLATIONS[language]["hd_label"],  discord.ButtonStyle.primary, "📺"),
            ("fhd", TRANSLATIONS[language]["fhd_label"], discord.ButtonStyle.success,  "🎬"),
            ("uhd", TRANSLATIONS[language]["uhd_label"], discord.ButtonStyle.danger,   "💎"),
        ]:
            btn          = discord.ui.Button(label=label, style=style, emoji=emoji)
            btn.callback = self._make_quality_callback(key)
            self.add_item(btn)

    def _make_quality_callback(self, quality_key: str):
        async def callback(interaction: discord.Interaction) -> None:
            if interaction.user.id != self.original_user.id:
                await interaction.response.send_message(
                    TRANSLATIONS[self.language]["not_for_you"], ephemeral=True
                )
                return
            self.quality = quality_key
            for child in self.children:
                child.disabled = True
            device_view = DeviceSelectView(
                self.original_user,
                self.original_interaction,
                self.language,
                quality_key,
                self.lang_message,
                self.confirm_message,
            )
            await interaction.response.edit_message(
                content=TRANSLATIONS[self.language]["device_prompt"], view=device_view
            )
            self.stop()
        return callback

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        try:
            await self.original_interaction.edit_original_response(
                content=TRANSLATIONS[self.language]["timeout_msg"], view=None
            )
        except Exception:
            pass


class DeviceSelectView(discord.ui.View):
    def __init__(
        self,
        original_user: discord.User | discord.Member,
        original_interaction: discord.Interaction,
        language: str,
        quality: str,
        lang_message: discord.Message | None = None,
        confirm_message: discord.Message | None = None,
    ) -> None:
        super().__init__(timeout=60)
        self.original_user        = original_user
        self.original_interaction = original_interaction
        self.language             = language
        self.quality              = quality
        self.lang_message         = lang_message
        self.confirm_message      = confirm_message

        for key, label, style, emoji in [
            ("pc",    TRANSLATIONS[language]["pc_label"],    discord.ButtonStyle.primary,  "🖥️"),
            ("phone", TRANSLATIONS[language]["phone_label"], discord.ButtonStyle.success,  "📱"),
            ("tv",    TRANSLATIONS[language]["tv_label"],    discord.ButtonStyle.secondary, "📺"),
        ]:
            btn          = discord.ui.Button(label=label, style=style, emoji=emoji)
            btn.callback = self._make_device_callback(key)
            self.add_item(btn)

    def _make_device_callback(self, device_key: str):
        async def callback(interaction: discord.Interaction) -> None:
            if interaction.user.id != self.original_user.id:
                await interaction.response.send_message(
                    TRANSLATIONS[self.language]["not_for_you"], ephemeral=True
                )
                return
            for child in self.children:
                child.disabled = True
            await interaction.response.edit_message(
                content=TRANSLATIONS[self.language]["progress"], view=None
            )
            await _generate_and_send_link(
                interaction,
                self.language,
                self.quality,
                device_key,
                self.lang_message,
                self.confirm_message,
            )
            self.stop()
        return callback

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        try:
            await self.original_interaction.edit_original_response(
                content=TRANSLATIONS[self.language]["timeout_msg"], view=None
            )
        except Exception:
            pass


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


@bot.tree.command(name="create", description="🎬 Generate a Netflix login link (PC, Phone, or TV)")
async def create(interaction: discord.Interaction) -> None:
    user_lang = get_user_lang(interaction)

    if not is_allowed_channel(interaction):
        guild_id   = interaction.guild.id if interaction.guild else None
        channel_id = config.get_channel_for_guild(guild_id) if guild_id else None
        if channel_id is None:
            await interaction.response.send_message(
                TRANSLATIONS[user_lang]["wrong_channel_no_config"], ephemeral=True
            )
        else:
            allowed_channel = bot.get_channel(channel_id)
            mention = allowed_channel.mention if allowed_channel else "the designated channel"
            await interaction.response.send_message(
                TRANSLATIONS[user_lang]["wrong_channel_with_config"].format(channel=mention), ephemeral=True
            )
        return

    # ── 24-hour cooldown check ──────────────────────────────────────────────
    on_cooldown, remaining_hours = await asyncio.to_thread(
        check_user_cooldown, interaction.user.id
    )
    if on_cooldown:
        total_minutes  = int(remaining_hours * 60)
        hours_left     = total_minutes // 60
        minutes_left   = total_minutes % 60
        msg = TRANSLATIONS[user_lang]["cooldown"].format(
            hours=hours_left, minutes=minutes_left
        )
        await interaction.response.send_message(msg, ephemeral=True)
        log.info(
            f"⏳ Cooldown: {interaction.user} (ID: {interaction.user.id}) "
            f"blocked – {hours_left}h {minutes_left}m remaining"
        )
        return
    # ───────────────────────────────────────────────────────────────────────

    view = LanguageSelectView(interaction)
    await interaction.response.send_message(TRANSLATIONS["en"]["lang_prompt"], view=view, ephemeral=True)


@bot.tree.command(
    name="channel",
    description="📌 Set the text channel where the bot will work",
)
@app_commands.describe(channel="The text channel to designate as the bot's working channel")
async def set_channel(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
    # #region agent log
    _cmd_t0 = time.monotonic()
    _agent_debug_log("bot.py:set_channel:entry", "set_channel invoked", {"interaction_id": interaction.id, "response_is_done": interaction.response.is_done()}, "B")
    # #endregion
    lang = get_user_lang(interaction)

    await interaction.response.defer(ephemeral=True)
    # #region agent log
    _agent_debug_log("bot.py:set_channel:post_defer", "interaction deferred", {"interaction_id": interaction.id, "elapsed_ms": round((time.monotonic() - _cmd_t0) * 1000), "response_is_done": interaction.response.is_done()}, "A")
    # #endregion

    guild_id   = interaction.guild.id
    guild_name = interaction.guild.name if interaction.guild else "Unknown"
    await config.set_allowed_channel(guild_id, channel.id, guild_name=guild_name, channel_name=channel.name)

    msg = (
        f"✅ Bot will now **only** respond in {channel.mention}."
        if lang == "en"
        else f"\u200f✅ البوت سيعمل الآن **فقط** في {channel.mention}."
    )
    # #region agent log
    _elapsed_ms = round((time.monotonic() - _cmd_t0) * 1000)
    _agent_debug_log("bot.py:set_channel:pre_followup", "about to followup.send", {"interaction_id": interaction.id, "elapsed_ms": _elapsed_ms, "response_is_done": interaction.response.is_done()}, "A")
    # #endregion
    try:
        await interaction.followup.send(msg, ephemeral=True)
        # #region agent log
        _agent_debug_log("bot.py:set_channel:post_followup", "followup.send succeeded", {"interaction_id": interaction.id, "elapsed_ms": round((time.monotonic() - _cmd_t0) * 1000)}, "A")
        # #endregion
    except discord.NotFound as exc:
        # #region agent log
        _agent_debug_log("bot.py:set_channel:followup_failed", "followup.send NotFound", {"interaction_id": interaction.id, "elapsed_ms": round((time.monotonic() - _cmd_t0) * 1000), "error": str(exc), "response_is_done": interaction.response.is_done()}, "A")
        # #endregion
        raise

    await send_or_update_setup_messages(channel, guild_id)

    log.info(f"📌 /channel set by {interaction.user} in guild {guild_id} → #{channel.name}")


@bot.tree.command(name="ban", description="🚫 Block a user from using the bot by their Discord ID (Admin only)")
@app_commands.describe(user_id="The Discord user ID to ban")
async def ban_user(interaction: discord.Interaction, user_id: str) -> None:
    lang = get_user_lang(interaction)
    if not is_admin(interaction.user.id) and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(TRANSLATIONS[lang]["not_admin"], ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    try:
        uid = int(user_id.strip())
    except ValueError:
        await interaction.followup.send("❌ Invalid user ID – must be a numeric Discord ID.", ephemeral=True)
        return

    if is_user_banned(uid):
        await interaction.followup.send(f"⚠️ User `{uid}` is already banned.", ephemeral=True)
        return

    username = str(uid)
    try:
        target   = await bot.fetch_user(uid)
        username = str(target)
    except Exception:
        pass

    _banned_user_ids.add(uid)
    _ban_attempt_counts.setdefault(uid, 0)
    success = await asyncio.to_thread(add_ban_to_github, uid, username)

    if success:
        log.info(f"🚫 Admin {interaction.user} banned {username} (ID: {uid})")
        msg = (
            f"✅ User `{username}` (ID: `{uid}`) has been **banned**."
            if lang == "en"
            else f"\u200f✅ تم حظر المستخدم `{username}` (ID: `{uid}`)."
        )
    else:
        msg = (
            f"⚠️ User `{uid}` banned locally but **GitHub push failed**."
            if lang == "en"
            else f"\u200f⚠️ تم الحظر محليًا لكن **فشل الرفع إلى GitHub**."
        )
    await interaction.followup.send(msg, ephemeral=True)


@bot.tree.command(name="unban", description="✅ Remove a bot ban for a user by their Discord ID (Admin only)")
@app_commands.describe(user_id="The Discord user ID to unban")
async def unban_user(interaction: discord.Interaction, user_id: str) -> None:
    lang = get_user_lang(interaction)
    if not is_admin(interaction.user.id) and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(TRANSLATIONS[lang]["not_admin"], ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    try:
        uid = int(user_id.strip())
    except ValueError:
        await interaction.followup.send("❌ Invalid user ID – must be a numeric Discord ID.", ephemeral=True)
        return

    if not is_user_banned(uid):
        await interaction.followup.send(f"⚠️ User `{uid}` is not currently banned.", ephemeral=True)
        return

    _banned_user_ids.discard(uid)
    attempts = _ban_attempt_counts.pop(uid, 0)
    success  = await asyncio.to_thread(remove_ban_from_github, uid)

    if success:
        log.info(f"✅ Admin {interaction.user} unbanned user {uid} (had {attempts} attempt(s))")
        msg = (
            f"✅ User `{uid}` has been **unbanned**. They had **{attempts}** blocked attempt(s)."
            if lang == "en"
            else f"\u200f✅ تم رفع الحظر عن المستخدم `{uid}`. كان لديه **{attempts}** محاولة محظورة."
        )
    else:
        msg = (
            f"⚠️ User `{uid}` unbanned locally but **GitHub push failed**."
            if lang == "en"
            else f"\u200f⚠️ تم رفع الحظر محليًا لكن **فشل الرفع إلى GitHub**."
        )
    await interaction.followup.send(msg, ephemeral=True)


@bot.tree.command(
    name="banserver",
    description="🚫 Ban a server from using the bot due to a bot-rule violation (Admin only)",
)
@app_commands.describe(
    guild_id="The Discord server (guild) ID to ban",
    reason="Reason for the server ban (e.g. bot rule violation)",
)
async def ban_server(
    interaction: discord.Interaction,
    guild_id: str,
    reason: str = "Bot rule violation",
) -> None:
    lang = get_user_lang(interaction)
    if not is_admin(interaction.user.id):
        await interaction.response.send_message(TRANSLATIONS[lang]["not_admin"], ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    try:
        gid = int(guild_id.strip())
    except ValueError:
        await interaction.followup.send(
            "❌ Invalid server ID – must be a numeric Discord guild ID.", ephemeral=True
        )
        return

    if is_server_banned(gid):
        await interaction.followup.send(f"⚠️ Server `{gid}` is already banned.", ephemeral=True)
        return

    guild_name = str(gid)
    target_guild = bot.get_guild(gid)
    if target_guild:
        guild_name = target_guild.name

    _banned_guild_ids.add(gid)
    success = await asyncio.to_thread(add_server_ban_to_github, gid, guild_name, reason)

    if success:
        log.info(f"🚫 Admin {interaction.user} banned server '{guild_name}' (ID: {gid}) – reason: {reason}")
        msg = (
            f"✅ Server `{guild_name}` (ID: `{gid}`) has been **banned**.\n📋 Reason: {reason}"
            if lang == "en"
            else f"\u200f✅ تم حظر السيرفر `{guild_name}` (ID: `{gid}`).\n📋 السبب: {reason}"
        )
    else:
        msg = (
            f"⚠️ Server `{gid}` banned locally but **GitHub push failed**."
            if lang == "en"
            else f"\u200f⚠️ تم الحظر محليًا لكن **فشل الرفع إلى GitHub**."
        )
    await interaction.followup.send(msg, ephemeral=True)


@bot.tree.command(
    name="unbanserver",
    description="✅ Remove a bot ban from a server by its guild ID (Admin only)",
)
@app_commands.describe(guild_id="The Discord server (guild) ID to unban")
async def unban_server(interaction: discord.Interaction, guild_id: str) -> None:
    lang = get_user_lang(interaction)
    if not is_admin(interaction.user.id):
        await interaction.response.send_message(TRANSLATIONS[lang]["not_admin"], ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    try:
        gid = int(guild_id.strip())
    except ValueError:
        await interaction.followup.send(
            "❌ Invalid server ID – must be a numeric Discord guild ID.", ephemeral=True
        )
        return

    if not is_server_banned(gid):
        await interaction.followup.send(
            f"⚠️ Server `{gid}` is not currently banned.", ephemeral=True
        )
        return

    _banned_guild_ids.discard(gid)
    success = await asyncio.to_thread(remove_server_ban_from_github, gid)

    if success:
        log.info(f"✅ Admin {interaction.user} unbanned server {gid}")
        msg = (
            f"✅ Server `{gid}` has been **unbanned**."
            if lang == "en"
            else f"\u200f✅ تم رفع الحظر عن السيرفر `{gid}`."
        )
    else:
        msg = (
            f"⚠️ Server `{gid}` unbanned locally but **GitHub push failed**."
            if lang == "en"
            else f"\u200f⚠️ تم رفع الحظر محليًا لكن **فشل الرفع إلى GitHub**."
        )
    await interaction.followup.send(msg, ephemeral=True)


admin_group = app_commands.Group(
    name="admin",
    description="👮 Manage bot admins",
)


@admin_group.command(name="add", description="👮 Add a user to the bot admin list")
@app_commands.describe(user_id="Discord user ID to grant admin access")
async def admin_add(interaction: discord.Interaction, user_id: str) -> None:
    lang = get_user_lang(interaction)

    if not is_owner(interaction.user.id):
        await interaction.response.send_message(TRANSLATIONS[lang]["not_admin"], ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    try:
        uid = int(user_id.strip())
    except ValueError:
        await interaction.followup.send("❌ Invalid user ID – must be numeric.", ephemeral=True)
        return

    if uid in _admin_registry:
        await interaction.followup.send(f"⚠️ User `{uid}` is already an admin.", ephemeral=True)
        return

    username = str(uid)
    try:
        target   = await bot.fetch_user(uid)
        username = str(target)
    except Exception:
        pass

    now_str = datetime.now(EGYPT_TZ).strftime("%Y-%m-%d %H:%M:%S")
    _admin_registry[uid] = {
        "username": username,
        "added_by": str(interaction.user),
        "added_at": now_str,
    }
    success = await asyncio.to_thread(save_admins_to_github, _admin_registry)

    log.info(f"👮 Owner {interaction.user} added admin {username} (ID: {uid})")
    msg = (
        f"✅ `{username}` (ID: `{uid}`) has been added as a **bot admin**."
        if lang == "en"
        else f"\u200f✅ تمت إضافة `{username}` (ID: `{uid}`) كـ **مسؤول بوت**."
    ) if success else (
        f"✅ `{uid}` added locally but **GitHub push failed**."
        if lang == "en"
        else f"\u200f✅ تمت الإضافة محليًا لكن **فشل الرفع إلى GitHub**."
    )
    await interaction.followup.send(msg, ephemeral=True)


@admin_group.command(name="remove", description="👮 Remove a user from the bot admin list")
@app_commands.describe(user_id="Discord user ID to revoke admin access")
async def admin_remove(interaction: discord.Interaction, user_id: str) -> None:
    lang    = get_user_lang(interaction)
    invoker = interaction.user.id

    if invoker not in _PRIVILEGED_IDS:
        await interaction.response.send_message(TRANSLATIONS[lang]["not_admin"], ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    try:
        uid = int(user_id.strip())
    except ValueError:
        await interaction.followup.send("❌ Invalid user ID – must be numeric.", ephemeral=True)
        return

    if uid == BOT_OWNER_ID and invoker != BOT_OWNER_ID:
        await interaction.followup.send("❌ You cannot remove the bot owner.", ephemeral=True)
        return

    if uid not in _admin_registry:
        await interaction.followup.send(f"⚠️ User `{uid}` is not in the admin list.", ephemeral=True)
        return

    removed_info = _admin_registry.pop(uid)
    success      = await asyncio.to_thread(save_admins_to_github, _admin_registry)

    log.info(f"👮 {interaction.user} removed admin {removed_info['username']} (ID: {uid})")
    msg = (
        f"✅ `{removed_info['username']}` (ID: `{uid}`) has been **removed** from admins."
        if lang == "en"
        else f"\u200f✅ تمت إزالة `{removed_info['username']}` (ID: `{uid}`) من المسؤولين."
    ) if success else (
        f"✅ `{uid}` removed locally but **GitHub push failed**."
        if lang == "en"
        else f"\u200f✅ تمت الإزالة محليًا لكن **فشل الرفع إلى GitHub**."
    )
    await interaction.followup.send(msg, ephemeral=True)


@admin_group.command(name="list", description="👮 List all current bot admins")
async def admin_list(interaction: discord.Interaction) -> None:
    lang = get_user_lang(interaction)

    if interaction.user.id not in _PRIVILEGED_IDS:
        await interaction.response.send_message(TRANSLATIONS[lang]["not_admin"], ephemeral=True)
        return

    embed = discord.Embed(
        title="👮 Bot Admin List  |  قائمة المسؤولين",
        color=discord.Color.gold(),
        timestamp=datetime.now(EGYPT_TZ),
    )

    embed.add_field(
        name=f"👑 X2 Salah (ID: {BOT_OWNER_ID})",
        value="Role: **Bot Owner** – permanent full access",
        inline=False,
    )
    embed.add_field(
        name=f"⭐ HASHO_Z (ID: {BOT_COADMIN_ID})",
        value="Role: **Co-Admin** – can remove admins (except owner)",
        inline=False,
    )

    if _admin_registry:
        embed.add_field(name="─────────────", value="**Additional Admins:**", inline=False)
        for uid, info in _admin_registry.items():
            embed.add_field(
                name=f"{info['username']} (ID: {uid})",
                value=f"Added by: `{info['added_by']}`\nDate: `{info['added_at']}`",
                inline=False,
            )
    else:
        embed.add_field(name="─────────────", value="*No additional admins.*", inline=False)

    embed.set_footer(text=FOOTER_TEXT)
    await interaction.response.send_message(embed=embed, ephemeral=True)


bot.tree.add_command(admin_group)


@bot.event
async def on_ready() -> None:
    log.info("━" * 60)
    log.info(f"🤖 Logged in as : {bot.user}  (ID: {bot.user.id})")
    if ALLOWED_GUILD_IDS:
        log.info(f"🏠 Guild restriction : {ALLOWED_GUILD_IDS}")
    else:
        log.info("🌐 Guild restriction : NONE (global bot)")

    if COOKIES_GITHUB_REPO:
        log.info(f"🍪 Cookie source : GitHub → {COOKIES_GITHUB_REPO}/{COOKIES_GITHUB_PATH} [{COOKIES_GITHUB_BRANCH}]")
    else:
        log.info(f"🍪 Cookie source : Local → {COOKIES_FOLDER.resolve()}")

    await config.init_db()

    global _banned_user_ids
    _banned_user_ids = await asyncio.to_thread(load_banned_users_from_github)
    log.info(f"🚫 Ban list: {len(_banned_user_ids)} banned user(s)")

    global _banned_guild_ids
    _banned_guild_ids = await asyncio.to_thread(load_banned_servers_from_github)
    log.info(f"🚫 Server ban list: {len(_banned_guild_ids)} banned server(s)")

    global _admin_registry
    _admin_registry = await asyncio.to_thread(load_admins_from_github)
    log.info(f"👮 Admin list: {len(_admin_registry)} admin(s)")

    _load_setup_tracker()

    if ALLOWED_GUILD_IDS:
        for gid in ALLOWED_GUILD_IDS:
            ch = config.get_channel_for_guild(gid)
            if ch:
                log.info(f"📌 Guild {gid} → channel {ch}")
            else:
                log.warning(f"⚠️ Guild {gid} → no channel configured (run /channel)")

    if GITHUB_REPO and GITHUB_FILE_PATH:
        log.info(f"📡 GitHub log: {GITHUB_REPO}/{GITHUB_FILE_PATH}")
    log.info("━" * 60)

    bot.tree.interaction_check = global_interaction_check

    if ALLOWED_GUILD_IDS:
        for guild_id in ALLOWED_GUILD_IDS:
            try:
                synced = await bot.tree.sync(guild=discord.Object(id=guild_id))
                log.info(f"✅ Synced {len(synced)} command(s) to guild {guild_id}")
            except Exception as exc:
                log.error(f"❌ Failed to sync commands to guild {guild_id}: {exc}")
    else:
        try:
            synced = await bot.tree.sync()
            log.info(f"✅ Synced {len(synced)} command(s) globally")
        except Exception as exc:
            log.error(f"❌ Failed to sync global commands: {exc}")


if __name__ == "__main__":
    COOKIES_FOLDER.mkdir(exist_ok=True)
    bot.run(DISCORD_BOT_TOKEN)
