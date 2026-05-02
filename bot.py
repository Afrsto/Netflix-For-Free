# bot.py
import os
import json
import random
import asyncio
import logging
import aiohttp
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from netflix_checker import check_cookie_file

# ------------------------------
# Configuration
# ------------------------------
ALLOWED_GUILD_ID = 1494152777381711945   # Only this server can use the bot

COOKIES_FOLDER    = Path("cookies")
SCRIPT_TIMEOUT    = 30
CONFIG_FILE       = Path("config.json")
CLEANUP_DELAY_SECONDS = 60

# Remote log file (raw URL for appending via GitHub API or just used as reference)
REMOTE_LOG_URL = "REMOTE_LOG_URL_D"
LOCAL_LOG_FILE = Path("users.txt")   # local mirror written alongside remote

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_TOKEN")
if not DISCORD_BOT_TOKEN:
    raise ValueError("Missing DISCORD_TOKEN environment variable")

# Optional: GitHub PAT for writing to the repo (set GITHUB_TOKEN env var)
GITHUB_TOKEN      = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO_OWNER = "Afrsto"
GITHUB_REPO_NAME  = "bot-users"
GITHUB_FILE_PATH  = "users.txt"
GITHUB_BRANCH     = "main"           # change if your default branch differs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("NetflixBot")

# ------------------------------
# Cookie-file rotation tracker
# ------------------------------
class CookieRotator:
    """
    Picks a random .txt file from COOKIES_FOLDER.
    Tracks which files have been used; once all are exhausted it resets
    the used set so every file can be chosen again.
    """
    def __init__(self):
        self._used: set[Path] = set()

    def pick(self) -> Path | None:
        all_files = list(COOKIES_FOLDER.glob("*.txt"))
        if not all_files:
            return None

        available = [f for f in all_files if f not in self._used]

        # All files have been used → reset and start over
        if not available:
            log.info("CookieRotator: all files used – resetting rotation pool.")
            self._used.clear()
            available = all_files

        chosen = random.choice(available)
        self._used.add(chosen)
        log.info(f"CookieRotator: picked '{chosen.name}' "
                 f"({len(self._used)}/{len(all_files)} used this cycle)")
        return chosen

cookie_rotator = CookieRotator()

# ------------------------------
# Remote / local user logger
# ------------------------------
async def log_user_activity(
    interaction: discord.Interaction,
    result: str,
    chosen_file: str = "N/A"
) -> None:
    """
    Build a rich log line and append it to:
      1. LOCAL_LOG_FILE  (always)
      2. GitHub repo file via the Contents API (when GITHUB_TOKEN is set)

    Log format:
    [TIMESTAMP] User: (Display: NAME) | ID: USERID | Account Created: DATE |
                Server: NAME (ID: GUILD_ID) | Member Since: DATE |
                Channel: #NAME (ID: CHANNEL_ID) | Roles: ROLE1, ROLE2 |
                Status: STATUS | Cookie File: FILE | Result: RESULT
    """
    now       = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    user      = interaction.user
    guild     = interaction.guild
    channel   = interaction.channel

    # ── user fields ──────────────────────────────────────────────────────────
    display_name   = user.display_name
    user_id        = user.id
    account_created = user.created_at.strftime("%Y-%m-%d %H:%M:%S UTC") if user.created_at else "N/A"

    # ── guild/member fields ──────────────────────────────────────────────────
    guild_name   = guild.name        if guild   else "DM"
    guild_id     = guild.id          if guild   else "N/A"
    channel_name = f"#{channel.name}" if hasattr(channel, "name") else "Unknown"
    channel_id   = channel.id        if channel else "N/A"

    # member-specific (joined_at, roles, status)
    member = guild.get_member(user.id) if guild else None
    if member is None and guild:
        try:
            member = await guild.fetch_member(user.id)
        except Exception:
            member = None

    member_since = (
        member.joined_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        if member and member.joined_at else "N/A"
    )

    roles = "None"
    if member:
        role_names = [r.name for r in member.roles if r.name != "@everyone"]
        roles = ", ".join(role_names) if role_names else "None"

    status = str(member.status).capitalize() if member else "Unknown"

    # ── compose line ─────────────────────────────────────────────────────────
    line = (
        f"[{now}] "
        f"User: (Display: {display_name}) | "
        f"ID: {user_id} | "
        f"Account Created: {account_created} | "
        f"Server: {guild_name} (ID: {guild_id}) | "
        f"Member Since: {member_since} | "
        f"Channel: {channel_name} (ID: {channel_id}) | "
        f"Roles: {roles} | "
        f"Status: {status} | "
        f"Cookie File: {chosen_file} | "
        f"Result: {result}\n"
    )

    # 1) Write locally
    try:
        LOCAL_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOCAL_LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception as exc:
        log.error(f"Failed to write local log: {exc}")

    log.info(f"Activity logged → {line.strip()}")

    # 2) Push to GitHub (non-blocking, best-effort)
    if GITHUB_TOKEN:
        asyncio.create_task(_push_log_to_github(line))
    else:
        log.debug("GITHUB_TOKEN not set – skipping remote log push.")


async def _push_log_to_github(new_line: str) -> None:
    """
    Appends new_line to GITHUB_FILE_PATH in the repo using the GitHub
    Contents API (GET current → decode → append → PUT back).
    Requires GITHUB_TOKEN with 'repo' or 'public_repo' scope.
    """
    api_url = (
        f"https://api.github.com/repos/{GITHUB_REPO_OWNER}/"
        f"{GITHUB_REPO_NAME}/contents/{GITHUB_FILE_PATH}"
    )
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }

    try:
        async with aiohttp.ClientSession() as session:
            # GET current file content + SHA
            async with session.get(api_url, headers=headers,
                                   params={"ref": GITHUB_BRANCH}) as resp:
                if resp.status == 404:
                    # File doesn't exist yet – create it
                    current_content = ""
                    sha = None
                elif resp.status == 200:
                    data = await resp.json()
                    import base64
                    current_content = base64.b64decode(data["content"]).decode("utf-8")
                    sha = data["sha"]
                else:
                    log.error(f"GitHub GET failed: {resp.status} {await resp.text()}")
                    return

            # Append the new line
            import base64
            updated_content = current_content + new_line
            encoded = base64.b64encode(updated_content.encode("utf-8")).decode("utf-8")

            payload: dict = {
                "message": f"log: activity at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}",
                "content": encoded,
                "branch": GITHUB_BRANCH,
            }
            if sha:
                payload["sha"] = sha

            async with session.put(api_url, headers=headers,
                                   json=payload) as put_resp:
                if put_resp.status in (200, 201):
                    log.info("Remote log pushed to GitHub successfully.")
                else:
                    log.error(f"GitHub PUT failed: {put_resp.status} {await put_resp.text()}")

    except Exception as exc:
        log.error(f"GitHub log push error: {exc}")


# ------------------------------
# Translations
# ------------------------------
TRANSLATIONS = {
    "en": {
        "lang_prompt":              "🌐 **Please select your language:**\n🌐 **الرجاء اختيار اللغة:**",
        "lang_selected":            "✅ Language selected: **English**",
        "confirm_prompt":           "**Do you want to generate a Netflix login link?**\n",
        "progress":                 "⏳ **Generating your Netflix link... please wait.**",
        "no_cookies_folder":        "❌ Cookies folder not found. Please contact the administrator.",
        "no_cookie_files":          "❌ No accounts available in the database right now.",
        "timeout":                  "⌛ The validation process took too long. Please try again later.",
        "unexpected_error":         "⚠️ An unexpected error occurred. Please try again.",
        "cookie_invalid":           "❌ The selected session is invalid or expired. Try again.",
        "success_title":            "✅ PC Login Link Ready",
        "success_desc":             "Click the link below to log in automatically:\n\n{link}",
        "footer":                   "⚠️ This link is for personal use only – do not share it.",
        "tv_instruction":           "📺 **TV Activation:** Visit **www.netflix.com/tv9** and enter the code shown on your screen.",
        "yes_label":                "Yes, generate link",
        "no_label":                 "No, cancel",
        "cancelled":                "❌ Process cancelled.",
        "not_for_you":              "❌ You cannot interact with this menu.",
        "timeout_msg":              "⏰ Request timed out due to inactivity.",
        "wrong_channel_no_config":  "⚠️ No channel configured. Admins must run `/channel`.",
        "wrong_channel_with_config":"❌ This command can only be used in {channel}.",
        "wrong_guild":              "❌ This bot is restricted to a specific server.",
        "setup_desc": (
            "Welcome! Use the `/create` command to generate a Netflix PC login link.\n\n"
            "**How to use:**\n"
            "1. Type `/create` in this channel.\n"
            "2. Select your language.\n"
            "3. Confirm generation.\n"
            "4. Wait a few seconds for your personal link.\n\n"
            "*Note: The link is single-use. Messages auto-delete after 1 minute for privacy.*"
        ),
    },
    "ar": {
        "lang_prompt":              "🌐 **Please select your language:**\n🌐 **الرجاء اختيار اللغة:**",
        "lang_selected":            "✅ تم اختيار اللغة: **العربية**",
        "confirm_prompt":           "**هل تريد إنشاء رابط تسجيل دخول لـ نتفليكس؟**\n",
        "progress":                 "⏳ **جاري إنشاء الرابط الخاص بك... يرجى الانتظار.**",
        "no_cookies_folder":        "❌ مجلد ملفات تعريف الارتباط غير موجود. يرجى الاتصال بالمسؤول.",
        "no_cookie_files":          "❌ لا توجد حسابات متاحة حالياً في قاعدة البيانات.",
        "timeout":                  "⌛ استغرق التحقق وقتاً طويلاً. يرجى المحاولة مرة أخرى لاحقاً.",
        "unexpected_error":         "⚠️ حدث خطأ غير متوقع أثناء معالجة الطلب.",
        "cookie_invalid":           "❌ الحساب المختار غير صالح أو منتهي الصلاحية. حاول مجدداً.",
        "success_title":            "✅ رابط دخول الكمبيوتر جاهز",
        "success_desc":             "انقر على الرابط أدناه لتسجيل الدخول تلقائياً:\n\n{link}",
        "footer":                   "⚠️ هذا الرابط للاستخدام الشخصي فقط – يُمنع مشاركته.",
        "tv_instruction":           "📺 **تفعيل التلفاز:** قم بزيارة **www.netflix.com/tv9** وأدخل الرمز المعروض على شاشتك.",
        "yes_label":                "نعم، أنشئ الرابط",
        "no_label":                 "لا، إلغاء",
        "cancelled":                "❌ تم إلغاء العملية.",
        "not_for_you":              "❌ لا يمكنك التفاعل مع هذه القائمة.",
        "timeout_msg":              "⏰ انتهت مهلة الطلب بسبب عدم التفاعل.",
        "wrong_channel_no_config":  "⚠️ لم يتم إعداد القناة. يجب على المسؤول استخدام أمر `/channel`.",
        "wrong_channel_with_config":"❌ لا يمكن استخدام هذا الأمر إلا في {channel}.",
        "wrong_guild":              "❌ هذا البوت مخصص للعمل في سيرفر محدد فقط.",
        "setup_desc": (
            "مرحباً! استخدم أمر `/create` لإنشاء رابط تسجيل دخول لـ نتفليكس.\n\n"
            "**طريقة الاستخدام:**\n"
            "1. اكتب `/create` في هذه القناة.\n"
            "2. اختر لغتك المفضلة.\n"
            "3. قم بتأكيد الإنشاء.\n"
            "4. انتظر بضع ثوانٍ للحصول على رابطك الشخصي.\n\n"
            "*ملاحظة: الروابط للاستخدام مرة واحدة. يتم حذف الرسائل تلقائياً بعد دقيقة واحدة للخصوصية.*"
        ),
    },
}


def get_user_lang(interaction: discord.Interaction) -> str:
    locale = str(interaction.locale)
    return "ar" if locale.startswith("ar") else "en"


# ------------------------------
# Config manager
# ------------------------------
class Config:
    def __init__(self):
        self.allowed_channel_id: int | None = None
        self.load()

    def load(self):
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r") as f:
                    data = json.load(f)
                    self.allowed_channel_id = data.get("allowed_channel_id")
            except Exception as e:
                log.error(f"Failed to load config: {e}")

    def save(self):
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump({"allowed_channel_id": self.allowed_channel_id}, f, indent=2)
        except Exception as e:
            log.error(f"Failed to save config: {e}")

    def set_allowed_channel(self, channel_id: int):
        self.allowed_channel_id = channel_id
        self.save()


config = Config()

# ------------------------------
# Bot setup
# ------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members          = True   # needed to fetch member status / join date / roles
bot = commands.Bot(command_prefix="!", intents=intents)


def is_allowed_channel(interaction: discord.Interaction) -> bool:
    if config.allowed_channel_id is None:
        return False
    return interaction.channel_id == config.allowed_channel_id


# ------------------------------
# Global interaction check (guild restriction)
# ------------------------------
async def global_interaction_check(interaction: discord.Interaction) -> bool:
    if interaction.guild is None or interaction.guild.id != ALLOWED_GUILD_ID:
        lang = get_user_lang(interaction)
        msg  = TRANSLATIONS[lang]["wrong_guild"]
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return False
    return True


# ------------------------------
# Language selection view
# ------------------------------
class LanguageSelectView(discord.ui.View):
    def __init__(self, original_interaction: discord.Interaction):
        super().__init__(timeout=60)
        self.original_interaction = original_interaction

    @discord.ui.button(label="English", style=discord.ButtonStyle.primary, emoji="🇬🇧")
    async def english_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._set_language(interaction, "en")

    @discord.ui.button(label="العربية", style=discord.ButtonStyle.primary, emoji="🇸🇦")
    async def arabic_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._set_language(interaction, "ar")

    async def _set_language(self, interaction: discord.Interaction, lang: str):
        if interaction.user.id != self.original_interaction.user.id:
            user_lang = get_user_lang(interaction)
            await interaction.response.send_message(
                TRANSLATIONS[user_lang]["not_for_you"], ephemeral=True
            )
            return

        # Delete the language selection message immediately
        try:
            await interaction.response.defer(ephemeral=True)
            await self.original_interaction.delete_original_response()
        except Exception:
            pass

        confirm_view = ConfirmView(
            self.original_interaction.user,
            self.original_interaction,
            lang
        )
        await interaction.followup.send(
            TRANSLATIONS[lang]["confirm_prompt"],
            view=confirm_view,
            ephemeral=True
        )
        self.stop()

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        try:
            await self.original_interaction.edit_original_response(
                content=TRANSLATIONS["en"]["timeout_msg"],
                view=None
            )
        except Exception:
            pass


# ------------------------------
# Confirmation View (Yes / No)
# ------------------------------
class ConfirmView(discord.ui.View):
    def __init__(
        self,
        original_user: discord.User | discord.Member,
        original_interaction: discord.Interaction,
        language: str,
    ):
        super().__init__(timeout=60)
        self.original_user        = original_user
        self.original_interaction = original_interaction
        self.language             = language

        # Dynamically localised buttons
        self.yes_btn          = discord.ui.Button(
            label=TRANSLATIONS[language]["yes_label"],
            style=discord.ButtonStyle.green
        )
        self.yes_btn.callback = self.yes_callback

        self.no_btn           = discord.ui.Button(
            label=TRANSLATIONS[language]["no_label"],
            style=discord.ButtonStyle.red
        )
        self.no_btn.callback  = self.no_callback

        self.add_item(self.yes_btn)
        self.add_item(self.no_btn)

    async def yes_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.original_user.id:
            await interaction.response.send_message(
                TRANSLATIONS[self.language]["not_for_you"], ephemeral=True
            )
            return

        await interaction.response.edit_message(
            content=TRANSLATIONS[self.language]["progress"],
            view=None
        )
        await self.generate_link(interaction)
        self.stop()

    async def no_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.original_user.id:
            await interaction.response.send_message(
                TRANSLATIONS[self.language]["not_for_you"], ephemeral=True
            )
            return

        await interaction.response.edit_message(
            content=TRANSLATIONS[self.language]["cancelled"], view=None
        )
        # Log cancellation
        await log_user_activity(interaction, result="Cancelled by user")
        self.stop()

    async def generate_link(self, interaction: discord.Interaction):
        lang = self.language
        t    = TRANSLATIONS[lang]

        if not COOKIES_FOLDER.exists():
            await interaction.edit_original_response(content=t["no_cookies_folder"])
            await log_user_activity(interaction, result="Error: cookies folder missing")
            return

        # ── use rotator instead of plain random.choice ──────────────────────
        chosen_file = cookie_rotator.pick()
        if chosen_file is None:
            await interaction.edit_original_response(content=t["no_cookie_files"])
            await log_user_activity(interaction, result="Error: no cookie files found")
            return

        log.info(f"User {interaction.user} triggered check for file: {chosen_file.name}")

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(check_cookie_file, str(chosen_file)),
                timeout=SCRIPT_TIMEOUT
            )
        except asyncio.TimeoutError:
            await interaction.edit_original_response(content=t["timeout"])
            await log_user_activity(
                interaction,
                result="Error: checker timed out",
                chosen_file=chosen_file.name
            )
            return
        except Exception as e:
            log.error(f"Checker error: {e}")
            await interaction.edit_original_response(content=t["unexpected_error"])
            await log_user_activity(
                interaction,
                result=f"Error: {e}",
                chosen_file=chosen_file.name
            )
            return

        if result:
            embed = discord.Embed(
                title=t["success_title"],
                description=t["success_desc"].format(link=result),
                color=discord.Color.green()
            )
            embed.set_footer(text=t["footer"])
            await interaction.edit_original_response(content=None, embed=embed)

            tv_message = await interaction.followup.send(
                t["tv_instruction"], ephemeral=True
            )

            # Log success
            await log_user_activity(
                interaction,
                result=f"Success – link generated",
                chosen_file=chosen_file.name
            )

            # Attempt to find the slash-command invocation message
            channel         = interaction.channel
            command_message = None
            try:
                async for msg in channel.history(limit=5):
                    if (
                        msg.author == interaction.user
                        and msg.interaction
                        and msg.interaction.id == interaction.id
                    ):
                        command_message = msg
                        break
            except Exception as e:
                log.error(f"Could not fetch command message: {e}")

            original_response = await interaction.original_response()

            asyncio.create_task(cleanup_messages(
                channel=channel,
                command_message=command_message,
                original_response=original_response,
                followup_message=tv_message,
                delay_seconds=CLEANUP_DELAY_SECONDS
            ))

            log.info(f"Link sent to {interaction.user} – cleanup in {CLEANUP_DELAY_SECONDS}s")
        else:
            await interaction.edit_original_response(content=t["cookie_invalid"])
            await log_user_activity(
                interaction,
                result="Failed – cookie invalid or expired",
                chosen_file=chosen_file.name
            )

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        try:
            await self.original_interaction.edit_original_response(
                content=TRANSLATIONS[self.language]["timeout_msg"],
                view=None
            )
        except Exception:
            pass


# ------------------------------
# Cleanup task
# ------------------------------
async def cleanup_messages(
    channel: discord.TextChannel,
    command_message: discord.Message | None,
    original_response: discord.WebhookMessage | None,
    followup_message: discord.Message | None,
    delay_seconds: int = CLEANUP_DELAY_SECONDS,
):
    await asyncio.sleep(delay_seconds)
    for msg in (command_message, original_response, followup_message):
        if msg is not None:
            try:
                await msg.delete()
            except Exception:
                pass   # message may already be deleted – silently ignore


# ------------------------------
# /channel command
# ------------------------------
@bot.tree.command(
    name="channel",
    description="Set the text channel where the bot will work (Admin only)"
)
@app_commands.default_permissions(administrator=True)
async def set_channel(
    interaction: discord.Interaction,
    channel: discord.TextChannel
):
    if not interaction.user.guild_permissions.administrator:
        lang = get_user_lang(interaction)
        msg  = (
            "❌ You need administrator permissions."
            if lang == "en" else
            "❌ تحتاج إلى صلاحيات المسؤول."
        )
        await interaction.response.send_message(msg, ephemeral=True)
        return

    config.set_allowed_channel(channel.id)

    lang        = get_user_lang(interaction)
    success_msg = (
        f"✅ Bot will now **only** respond in {channel.mention}."
        if lang == "en" else
        f"✅ البوت سيعمل الآن **فقط** في {channel.mention}."
    )
    await interaction.response.send_message(success_msg, ephemeral=True)

    # Bilingual setup embed
    embed = discord.Embed(
        title="🎬 Netflix Link Generator | مولد روابط نتفليكس",
        description=(
            f"**English:**\n{TRANSLATIONS['en']['setup_desc']}"
            f"\n\n**العربية:**\n{TRANSLATIONS['ar']['setup_desc']}"
        ),
        color=discord.Color.blue()
    )
    embed.set_footer(text="X2 Salah Utility")

    try:
        setup_msg = await channel.send(embed=embed)
        await setup_msg.pin()
        log.info(f"Pinned setup message in #{channel.name} (ID: {channel.id})")
    except discord.Forbidden:
        log.warning(f"Missing permissions to send/pin in #{channel.name}")
    except Exception as e:
        log.error(f"Failed to send setup message: {e}")


# ------------------------------
# /create command
# ------------------------------
@bot.tree.command(
    name="create",
    description="Generate a Netflix PC login link from a random cookie file"
)
async def create(interaction: discord.Interaction):
    user_lang = get_user_lang(interaction)

    if not is_allowed_channel(interaction):
        if config.allowed_channel_id is None:
            await interaction.response.send_message(
                TRANSLATIONS[user_lang]["wrong_channel_no_config"],
                ephemeral=True
            )
        else:
            allowed_channel = bot.get_channel(config.allowed_channel_id)
            mention         = allowed_channel.mention if allowed_channel else "the designated channel"
            msg             = TRANSLATIONS[user_lang]["wrong_channel_with_config"].format(channel=mention)
            await interaction.response.send_message(msg, ephemeral=True)
        return

    view = LanguageSelectView(interaction)
    await interaction.response.send_message(
        TRANSLATIONS["en"]["lang_prompt"],   # bilingual by content
        view=view,
        ephemeral=True
    )


# ------------------------------
# Bot events
# ------------------------------
@bot.event
async def on_ready():
    log.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    bot.tree.interaction_check = global_interaction_check

    guild = discord.Object(id=ALLOWED_GUILD_ID)
    try:
        synced = await bot.tree.sync(guild=guild)
        log.info(f"Synced {len(synced)} slash command(s) to guild {ALLOWED_GUILD_ID}")
    except Exception as e:
        log.error(f"Failed to sync commands: {e}")

    log.info(f"Log reference → {REMOTE_LOG_URL}")
    log.info(f"Local log     → {LOCAL_LOG_FILE.resolve()}")


# ------------------------------
# Run the bot
# ------------------------------
if __name__ == "__main__":
    COOKIES_FOLDER.mkdir(exist_ok=True)
    bot.run(DISCORD_BOT_TOKEN)
