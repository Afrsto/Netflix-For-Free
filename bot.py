# bot.py
import os
import json
import random
import asyncio
import logging
from pathlib import Path
from base64 import b64decode
from datetime import datetime
from urllib.parse import urlparse

import discord
from discord import app_commands
from discord.ext import commands
from github import Github, GithubException

from netflix_checker import check_cookie_file

# ╔══════════════════════════════════════════════════════════════╗
# ║                      CONFIGURATION                          ║
# ╚══════════════════════════════════════════════════════════════╝
ALLOWED_GUILD_ID       = 1494152777381711945   # Only this server can use the bot

COOKIES_FOLDER         = Path("cookies")
SCRIPT_TIMEOUT         = 30
CONFIG_FILE            = Path("config.json")
USER_LOG_FILE          = Path("users.txt")     # local fallback (also pushed to GitHub)
CLEANUP_DELAY_SECONDS  = 120

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_TOKEN")
GITHUB_TOKEN      = os.environ.get("GITHUB_TOKEN")
REMOTE_LOG_URL    = os.environ.get("REMOTE_LOG_URL_D")   # GitHub URL to the log file

if not DISCORD_BOT_TOKEN:
    raise ValueError("❌ Missing DISCORD_TOKEN environment variable")

# ────────────────────────────────────────────────────────────────
# Parse GitHub repo and file path from REMOTE_LOG_URL_D
# Example URL: https://github.com/Afrsto/bot-users/blob/e70a706.../users.txt
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
# ║                    USER ACTIVITY LOGGER (ASYNC)             ║
# ╚══════════════════════════════════════════════════════════════╝
async def log_user_activity(
    interaction: discord.Interaction,
    condition: str,
    result: str,
) -> None:
    """Append a structured log entry locally and push to GitHub."""
    now       = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    user      = interaction.user
    guild     = interaction.guild

    # Rich user information
    username      = str(user)
    display_name  = user.display_name
    user_id       = user.id
    account_since = user.created_at.strftime("%Y-%m-%d")
    server_name   = guild.name       if guild else "DM"
    server_id     = guild.id         if guild else "N/A"

    # ── Fetch fresh member data to avoid cache issues ─────────────────
    member_since = "N/A"
    roles_str    = "N/A"

    if guild:
        try:
            # Force fetch the member from Discord API (bypass cache)
            member = await guild.fetch_member(user.id)
            if member.joined_at:
                member_since = member.joined_at.strftime("%Y-%m-%d")
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

    line = (
        f"[{now}] "
        f"👤 User: {username} (Display: {display_name}) | "
        f"🆔 ID: {user_id} | "
        f"🗓️  Account Created: {account_since} | "
        f"🏠 Server: {server_name} (ID: {server_id}) | "
        f"📅 Member Since: {member_since} | "
        f"💬 Channel: #{channel_name} | "
        f"🎭 Roles: [{roles_str}] | "
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
        "tv_instruction":         "📺 **TV Activation:** Visit **www.netflix.com/tv9** and enter the code shown on your screen.",
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
            "4️⃣  Wait a few seconds for your personal link.\n\n"
            "*⚠️ Note: Links are single-use. Messages auto-delete after 2 minutes for privacy.*"
        ),
    },
    "ar": {
        "lang_prompt":            "🌐 **Please select your language:**\n🌐 **الرجاء اختيار اللغة:**",
        "lang_selected":          "✅ تم اختيار اللغة: **العربية**",
        "confirm_prompt":         "🎬 **هل تريد إنشاء رابط تسجيل دخول لـ نتفليكس؟**\n",
        "progress":               "⏳ **جاري إنشاء الرابط الخاص بك… يرجى الانتظار.**",
        "no_cookies_folder":      "❌ مجلد ملفات تعريف الارتباط غير موجود. يرجى الاتصال بالمسؤول.",
        "no_cookie_files":        "❌ لا توجد حسابات متاحة حالياً في قاعدة البيانات. حاول لاحقاً.",
        "timeout":                "⌛ استغرق التحقق وقتاً طويلاً. يرجى المحاولة مرة أخرى لاحقاً.",
        "unexpected_error":       "⚠️ حدث خطأ غير متوقع أثناء معالجة الطلب.",
        "cookie_invalid":         "❌ الحساب المختار غير صالح أو منتهي الصلاحية. حاول مجدداً.",
        "success_title":          "✅ 🎬 رابط تسجيل الدخول إلى نتفليكس جاهز!",
        "success_desc":           "🔗 انقر على الرابط أدناه لتسجيل الدخول تلقائياً:\n\n{link}",
        "footer":                 "⚠️ هذا الرابط للاستخدام الشخصي فقط – يُمنع مشاركته.",
        "tv_instruction":         "📺 **تفعيل التلفاز:** قم بزيارة **www.netflix.com/tv9** وأدخل الرمز المعروض على شاشتك.",
        "yes_label":              "✅  نعم، أنشئ الرابط",
        "no_label":               "❌  لا، إلغاء",
        "cancelled":              "🚫 تم إلغاء العملية.",
        "not_for_you":            "🚫 لا يمكنك التفاعل مع هذه القائمة.",
        "timeout_msg":            "⏰ انتهت مهلة الطلب بسبب عدم التفاعل.",
        "wrong_channel_no_config": "⚠️ لم يتم إعداد القناة. يجب على المسؤول استخدام أمر `/channel` أولاً.",
        "wrong_channel_with_config": "❌ لا يمكن استخدام هذا الأمر إلا في {channel}.",
        "wrong_guild":            "❌ هذا البوت مخصص للعمل في سيرفر محدد فقط.",
        "setup_desc": (
            "مرحباً! 👋 استخدم أمر `/create` لإنشاء رابط تسجيل دخول لـ نتفليكس.\n\n"
            "**📋 طريقة الاستخدام:**\n"
            "1️⃣  اكتب `/create` في هذه القناة.\n"
            "2️⃣  اختر لغتك المفضلة.\n"
            "3️⃣  قم بتأكيد الإنشاء.\n"
            "4️⃣  انتظر بضع ثوانٍ للحصول على رابطك الشخصي.\n\n"
            "*⚠️ ملاحظة: الروابط للاستخدام مرة واحدة. يتم حذف الرسائل تلقائياً بعد دقيقتين للخصوصية.*"
        ),
    },
}


def get_user_lang(interaction: discord.Interaction) -> str:
    """Detect Arabic locale, otherwise default to English."""
    return "ar" if str(interaction.locale).startswith("ar") else "en"


# ╔══════════════════════════════════════════════════════════════╗
# ║                      CONFIG MANAGER                         ║
# ╚══════════════════════════════════════════════════════════════╝
class Config:
    def __init__(self) -> None:
        self.allowed_channel_id: int | None = None
        self.load()

    def load(self) -> None:
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r") as f:
                    data = json.load(f)
                    self.allowed_channel_id = data.get("allowed_channel_id")
            except Exception as e:
                log.error(f"❌ Failed to load config: {e}")

    def save(self) -> None:
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump({"allowed_channel_id": self.allowed_channel_id}, f, indent=2)
        except Exception as e:
            log.error(f"❌ Failed to save config: {e}")

    def set_allowed_channel(self, channel_id: int) -> None:
        self.allowed_channel_id = channel_id
        self.save()


config = Config()

# ╔══════════════════════════════════════════════════════════════╗
# ║                        BOT SETUP                            ║
# ╚══════════════════════════════════════════════════════════════╝
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


def is_allowed_channel(interaction: discord.Interaction) -> bool:
    if config.allowed_channel_id is None:
        return False
    return interaction.channel_id == config.allowed_channel_id


# ╔══════════════════════════════════════════════════════════════╗
# ║              GLOBAL INTERACTION CHECK (guild guard)         ║
# ╚══════════════════════════════════════════════════════════════╝
async def global_interaction_check(interaction: discord.Interaction) -> bool:
    if interaction.guild is None or interaction.guild.id != ALLOWED_GUILD_ID:
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

        confirm_view = ConfirmView(self.original_interaction.user, self.original_interaction, lang)
        await interaction.followup.send(TRANSLATIONS[lang]["confirm_prompt"], view=confirm_view, ephemeral=True)
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
        await log_user_activity(interaction, "Cancelled", "User clicked No")
        self.stop()

    async def generate_link(self, interaction: discord.Interaction) -> None:
        lang = self.language
        t = TRANSLATIONS[lang]

        if not COOKIES_FOLDER.exists():
            await interaction.edit_original_response(content=t["no_cookies_folder"])
            await log_user_activity(interaction, "Error", "Cookies folder missing")
            return

        txt_files = list(COOKIES_FOLDER.glob("*.txt"))
        if not txt_files:
            await interaction.edit_original_response(content=t["no_cookie_files"])
            await log_user_activity(interaction, "Error", "No cookie files found")
            return

        chosen_file = pick_cookie_file(txt_files)
        log.info(f"🎯 {interaction.user} → checking file: {chosen_file.name}")

        try:
            result = await asyncio.wait_for(asyncio.to_thread(check_cookie_file, str(chosen_file)), timeout=SCRIPT_TIMEOUT)
        except asyncio.TimeoutError:
            await interaction.edit_original_response(content=t["timeout"])
            await log_user_activity(interaction, "Timeout", "Cookie validation timeout")
            return
        except Exception as e:
            log.error(f"❌ Checker error: {e}")
            await interaction.edit_original_response(content=t["unexpected_error"])
            await log_user_activity(interaction, "Error", f"Exception: {str(e)[:80]}")
            return

        if result:
            user = interaction.user
            member = interaction.guild.get_member(user.id) if interaction.guild else None

            embed = discord.Embed(
                title=t["success_title"],
                description=t["success_desc"].format(link=result),
                color=discord.Color.from_rgb(229, 9, 20),
                timestamp=datetime.now(),
            )
            embed.set_thumbnail(url="https://upload.wikimedia.org/wikipedia/commons/0/08/Netflix_2015_logo.svg")
            embed.add_field(name="👤 User", value=f"{user.mention} (`{user}`)", inline=True)
            embed.add_field(name="🆔 User ID", value=str(user.id), inline=True)
            embed.add_field(name="🗓️ Account Created", value=user.created_at.strftime("%Y-%m-%d"), inline=True)
            if member and member.joined_at:
                embed.add_field(name="📅 Server Member Since", value=member.joined_at.strftime("%Y-%m-%d"), inline=True)
            if member:
                roles_str = ", ".join(r.name for r in member.roles[1:]) or "None"
                embed.add_field(name="🎭 Roles", value=roles_str, inline=False)
            embed.set_footer(text=t["footer"] + "  •  X2 Salah Utility 🎬")

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
                delay_seconds=CLEANUP_DELAY_SECONDS,
            ))

            log.info(f"🔗 Link sent to {interaction.user} – cleanup in {CLEANUP_DELAY_SECONDS}s")
            await log_user_activity(interaction, "✅ Success", "Link generated")
        else:
            await interaction.edit_original_response(content=t["cookie_invalid"])
            await log_user_activity(interaction, "❌ Failed", "Cookie invalid or expired")

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
async def cleanup_messages(channel: discord.TextChannel, command_message: discord.Message | None, original_response: discord.WebhookMessage, followup_message: discord.Message | None, delay_seconds: int) -> None:
    await asyncio.sleep(delay_seconds)
    for msg in (command_message, original_response, followup_message):
        if msg is not None:
            try:
                await msg.delete()
            except Exception:
                pass
    log.info("🧹 Cleanup complete.")


# ╔══════════════════════════════════════════════════════════════╗
# ║               /channel COMMAND  (Admin only)                ║
# ╚══════════════════════════════════════════════════════════════╝
@bot.tree.command(name="channel", description="📌 Set the text channel where the bot will work (Admin only)")
@app_commands.default_permissions(administrator=True)
async def set_channel(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
    if not interaction.user.guild_permissions.administrator:
        lang = get_user_lang(interaction)
        msg = "❌ You need administrator permissions." if lang == "en" else "❌ تحتاج إلى صلاحيات المسؤول."
        await interaction.response.send_message(msg, ephemeral=True)
        return

    config.set_allowed_channel(channel.id)
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

    if not is_allowed_channel(interaction):
        if config.allowed_channel_id is None:
            await interaction.response.send_message(TRANSLATIONS[user_lang]["wrong_channel_no_config"], ephemeral=True)
        else:
            allowed_channel = bot.get_channel(config.allowed_channel_id)
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
    log.info(f"🏠 Allowed guild: {ALLOWED_GUILD_ID}")
    log.info(f"📂 Cookies folder: {COOKIES_FOLDER.resolve()}")
    if GITHUB_REPO and GITHUB_FILE_PATH:
        log.info(f"📡 GitHub log target: {GITHUB_REPO}/{GITHUB_FILE_PATH}")
    else:
        log.warning("⚠️ GitHub logging is DISABLED – REMOTE_LOG_URL_D not set or invalid")
    log.info("━" * 60)

    bot.tree.interaction_check = global_interaction_check

    guild = discord.Object(id=ALLOWED_GUILD_ID)
    try:
        synced = await bot.tree.sync(guild=guild)
        log.info(f"✅ Synced {len(synced)} slash command(s) to guild {ALLOWED_GUILD_ID}")
    except Exception as e:
        log.error(f"❌ Failed to sync commands: {e}")


# ╔══════════════════════════════════════════════════════════════╗
# ║                        ENTRY POINT                          ║
# ╚══════════════════════════════════════════════════════════════╝
if __name__ == "__main__":
    COOKIES_FOLDER.mkdir(exist_ok=True)
    bot.run(DISCORD_BOT_TOKEN)
