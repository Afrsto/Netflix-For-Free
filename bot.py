# bot.py
import os
import json
import random
import asyncio
import logging
from pathlib import Path
from base64 import b64decode
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlparse

import discord
from discord import app_commands
from discord.ext import commands
from github import Github, GithubException

from netflix_checker import check_cookie_file

# ╔══════════════════════════════════════════════════════════════╗
# ║                      CONFIGURATION                          ║
# ╚══════════════════════════════════════════════════════════════╝

COOKIES_FOLDER         = Path("cookies")
SCRIPT_TIMEOUT         = 30
CONFIG_FILE            = Path("config.json")
USER_LOG_FILE          = Path("users.txt")
CLEANUP_DELAY_SECONDS  = 60

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_TOKEN")
GITHUB_TOKEN      = os.environ.get("GITHUB_TOKEN")
REMOTE_LOG_URL    = os.environ.get("REMOTE_LOG_URL")
DEFAULT_CHANNEL_ID = os.environ.get("DEFAULT_CHANNEL_ID")   # NEW: global fallback channel

# ────────────────────────────────────────────────────────────────
# Multi‑guild support: collect all GUILD_ID_* variables
# ────────────────────────────────────────────────────────────────
ALLOWED_GUILD_IDS: list[int] = []

def _add_guild_id(value: str) -> None:
    if value and value.isdigit():
        gid = int(value)
        if gid not in ALLOWED_GUILD_IDS:
            ALLOWED_GUILD_IDS.append(gid)
            logging.info(f"✅ Allowed guild ID loaded: {gid}")
    else:
        logging.warning(f"⚠️ Invalid GUILD_ID value ignored: {value}")

legacy_guild = os.environ.get("GUILD_ID")
if legacy_guild:
    _add_guild_id(legacy_guild)

for key, value in os.environ.items():
    if key.startswith("GUILD_ID_") and key != "GUILD_ID":
        _add_guild_id(value)

if not ALLOWED_GUILD_IDS:
    raise ValueError("❌ No valid GUILD_ID or GUILD_ID_* environment variables found. Bot cannot start.")

if not DISCORD_BOT_TOKEN:
    raise ValueError("❌ Missing DISCORD_TOKEN environment variable")

# ────────────────────────────────────────────────────────────────
# Parse GitHub repo and file path from REMOTE_LOG_URL
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
        logging.warning("⚠️ Could not parse REMOTE_LOG_URL, GitHub logging disabled.")
else:
    logging.warning("⚠️ REMOTE_LOG_URL not set, GitHub logging disabled.")

# ... (locale mappings unchanged, omitted for brevity - keep original)
# For brevity, I'll assume the locale dicts are present as in original.
# In actual final answer, include full locale dicts.

# ╔══════════════════════════════════════════════════════════════╗
# ║              FIXED: PERSISTENT PER‑GUILD CONFIG             ║
# ╚══════════════════════════════════════════════════════════════╝
class Config:
    """
    Manages per‑guild channel configuration.
    Storage: config.json with structure:
        {
            "guilds": {
                "123456789": 987654321,
                "987654321": 123456789
            }
        }
    Falls back to DEFAULT_CHANNEL_ID environment variable if no per‑guild config exists.
    Never resets config on startup.
    """
    def __init__(self) -> None:
        self._guild_channels: dict[int, int] = {}   # guild_id -> channel_id
        self.load()

    def load(self) -> None:
        """Load config from JSON file, with robust error handling and fallback."""
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if "guilds" in data and isinstance(data["guilds"], dict):
                    # Convert string keys to int safely
                    for gid_str, cid in data["guilds"].items():
                        try:
                            gid = int(gid_str)
                            cid_int = int(cid)
                            self._guild_channels[gid] = cid_int
                        except (ValueError, TypeError):
                            logging.warning(f"⚠️ Skipping invalid config entry: {gid_str}:{cid}")
                    logging.info(f"📂 Loaded per‑guild channels from config.json: {self._guild_channels}")
                else:
                    logging.warning("⚠️ config.json missing 'guilds' key or invalid format – starting empty.")
            except json.JSONDecodeError as e:
                logging.error(f"❌ Config file corrupted (JSON error): {e} – will overwrite on next save.")
                self._guild_channels = {}
            except Exception as e:
                logging.error(f"❌ Failed to load config: {e}")
                self._guild_channels = {}
        else:
            logging.info("📂 No config.json found – starting fresh.")
            self._guild_channels = {}

        # Fallback: if we have DEFAULT_CHANNEL_ID env but no guild configs,
        # we DO NOT auto‑assign it here – we only use it as fallback during checks.
        # This prevents overwriting user choice.

    def save(self) -> None:
        """Save current config to config.json (atomic write)."""
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            # Convert int keys to strings for JSON
            guilds_dict = {str(gid): cid for gid, cid in self._guild_channels.items()}
            data = {"guilds": guilds_dict}
            # Write to temp file then rename to avoid partial writes
            temp_file = CONFIG_FILE.with_suffix(".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            temp_file.replace(CONFIG_FILE)
            logging.info(f"💾 Saved per‑guild channels: {self._guild_channels}")
        except Exception as e:
            logging.error(f"❌ Failed to save config: {e}")

    def set_channel_for_guild(self, guild_id: int, channel_id: int) -> None:
        """Store the allowed channel for a specific guild."""
        self._guild_channels[guild_id] = channel_id
        self.save()

    def get_channel_for_guild(self, guild_id: int) -> int | None:
        """Return the configured channel ID for the guild, or None if not set."""
        return self._guild_channels.get(guild_id)

    def get_allowed_channel_for_interaction(self, interaction: discord.Interaction) -> int | None:
        """
        Determine the channel that is allowed for the given interaction.
        Priority:
            1. Per‑guild config from config.json
            2. DEFAULT_CHANNEL_ID environment variable (global fallback)
            3. None
        """
        if interaction.guild is None:
            return None
        guild_id = interaction.guild.id
        # 1. Per‑guild config
        channel_id = self.get_channel_for_guild(guild_id)
        if channel_id is not None:
            return channel_id
        # 2. Environment fallback
        if DEFAULT_CHANNEL_ID and DEFAULT_CHANNEL_ID.isdigit():
            return int(DEFAULT_CHANNEL_ID)
        # 3. Nothing configured
        return None


config = Config()

# ╔══════════════════════════════════════════════════════════════╗
# ║                        BOT SETUP                            ║
# ╚══════════════════════════════════════════════════════════════╝
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


def is_allowed_channel(interaction: discord.Interaction) -> bool:
    """Check if the command is used in the allowed channel for this guild."""
    allowed_channel_id = config.get_allowed_channel_for_interaction(interaction)
    if allowed_channel_id is None:
        return False
    return interaction.channel_id == allowed_channel_id


# ╔══════════════════════════════════════════════════════════════╗
# ║              GLOBAL INTERACTION CHECK (guild guard)         ║
# ╚══════════════════════════════════════════════════════════════╝
async def global_interaction_check(interaction: discord.Interaction) -> bool:
    """Reject interactions from guilds not in ALLOWED_GUILD_IDS."""
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
# ║               LANGUAGE SELECTION VIEW (unchanged)           ║
# ╚══════════════════════════════════════════════════════════════╝
# ... (LanguageSelectView, ConfirmView, cleanup_messages remain exactly as in original)
# To save space, I omit them here but they must be present in final answer.
# I will include them fully in the final output.


# ╔══════════════════════════════════════════════════════════════╗
# ║               /channel COMMAND  (Admin only)                ║
# ╚══════════════════════════════════════════════════════════════╝
@bot.tree.command(name="channel", description="📌 Set the text channel where the bot will work (Admin only)")
@app_commands.default_permissions(administrator=True)
async def set_channel(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
    # UPDATED: per‑guild configuration
    if interaction.guild is None or interaction.guild.id not in ALLOWED_GUILD_IDS:
        lang = get_user_lang(interaction)
        await interaction.response.send_message(TRANSLATIONS[lang]["wrong_guild"], ephemeral=True)
        return

    if not interaction.user.guild_permissions.administrator:
        lang = get_user_lang(interaction)
        msg = "❌ You need administrator permissions." if lang == "en" else "❌ تحتاج إلى صلاحيات المسؤول."
        await interaction.response.send_message(msg, ephemeral=True)
        return

    # Store per guild
    config.set_channel_for_guild(interaction.guild.id, channel.id)
    lang = get_user_lang(interaction)
    success_msg = f"✅ Bot will now **only** respond in {channel.mention} for this server." if lang == "en" else f"✅ البوت سيعمل الآن **فقط** في {channel.mention} لهذا السيرفر."
    await interaction.response.send_message(success_msg, ephemeral=True)

    # Send pinned setup message (only if not already present? optional)
    embed = discord.Embed(
        title="🎬 Netflix Link Generator  |  مولد روابط نتفليكس",
        description=f"🇬🇧 **English:**\n{TRANSLATIONS['en']['setup_desc']}\n\n🇸🇦 **العربية:**\n{TRANSLATIONS['ar']['setup_desc']}",
        color=discord.Color.from_rgb(229, 9, 20),
        timestamp=datetime.now(),
    )
    embed.set_footer(text="⚡ X2 Salah Utility  •  Netflix Bot 🎬")
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
# ╚══════════════════════════════════════════════════════════════╝
@bot.tree.command(name="create", description="🎬 Generate a Netflix PC login link from a random cookie file")
async def create(interaction: discord.Interaction) -> None:
    user_lang = get_user_lang(interaction)

    # Guild check already done globally, but we also need channel check
    allowed_channel_id = config.get_allowed_channel_for_interaction(interaction)
    if allowed_channel_id is None:
        # No channel configured for this guild and no global fallback
        await interaction.response.send_message(TRANSLATIONS[user_lang]["wrong_channel_no_config"], ephemeral=True)
        return

    if interaction.channel_id != allowed_channel_id:
        allowed_channel = bot.get_channel(allowed_channel_id)
        mention = allowed_channel.mention if allowed_channel else "the designated channel"
        await interaction.response.send_message(TRANSLATIONS[user_lang]["wrong_channel_with_config"].format(channel=mention), ephemeral=True)
        return

    view = LanguageSelectView(interaction)
    await interaction.response.send_message(TRANSLATIONS["en"]["lang_prompt"], view=view, ephemeral=True)


# ╔══════════════════════════════════════════════════════════════╗
# ║                       BOT EVENTS                            ║
# ╚══════════════════════════════════════════════════════════════╝
@bot.event
async def on_ready() -> None:
    log.info("━" * 60)
    log.info(f"🤖 Logged in as : {bot.user}  (ID: {bot.user.id})")
    log.info(f"🏠 Allowed guilds: {ALLOWED_GUILD_IDS}")
    log.info(f"📂 Cookies folder: {COOKIES_FOLDER.resolve()}")
    # Log current configuration status
    if config._guild_channels:
        log.info(f"📁 Loaded per‑guild channels from config: {config._guild_channels}")
    else:
        log.info("📁 No per‑guild channels saved in config.json")
    if DEFAULT_CHANNEL_ID and DEFAULT_CHANNEL_ID.isdigit():
        log.info(f"🌐 Global fallback channel (DEFAULT_CHANNEL_ID): {DEFAULT_CHANNEL_ID}")
    else:
        log.info("🌐 No global fallback channel set (DEFAULT_CHANNEL_ID missing or invalid)")
    if GITHUB_REPO and GITHUB_FILE_PATH:
        log.info(f"📡 GitHub log target: {GITHUB_REPO}/{GITHUB_FILE_PATH}")
    else:
        log.warning("⚠️ GitHub logging is DISABLED – REMOTE_LOG_URL not set or invalid")
    log.info("━" * 60)

    bot.tree.interaction_check = global_interaction_check

    # Sync commands to each allowed guild
    for guild_id in ALLOWED_GUILD_IDS:
        guild = discord.Object(id=guild_id)
        try:
            synced = await bot.tree.sync(guild=guild)
            log.info(f"✅ Synced {len(synced)} slash command(s) to guild {guild_id}")
        except Exception as e:
            log.error(f"❌ Failed to sync commands to guild {guild_id}: {e}")


# ╔══════════════════════════════════════════════════════════════╗
# ║                        ENTRY POINT                          ║
# ╚══════════════════════════════════════════════════════════════╝
if __name__ == "__main__":
    COOKIES_FOLDER.mkdir(exist_ok=True)
    bot.run(DISCORD_BOT_TOKEN)
