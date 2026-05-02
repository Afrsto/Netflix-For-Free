import os
import json
import random
import asyncio
import logging
import aiohttp
import base64
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

# استيراد دالة الفحص من الملف الخارجي
from netflix_checker import check_cookie_file

# ------------------------------
# Configuration
# ------------------------------
ALLOWED_GUILD_ID = 1494152777381711945   # السيرفر المسموح له فقط[cite: 1]

COOKIES_FOLDER    = Path("cookies")
SCRIPT_TIMEOUT    = 30
CONFIG_FILE       = Path("config.json")
CLEANUP_DELAY_SECONDS = 60

# روابط السجلات
REMOTE_LOG_URL = "https://github.com/Afrsto/bot-users/blob/aaede067697628bf509e34bec49ae324fe46c0dc/users.txt"
LOCAL_LOG_FILE = Path("users.txt")

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_TOKEN")
GITHUB_TOKEN      = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO_OWNER = "Afrsto"
GITHUB_REPO_NAME  = "bot-users"
GITHUB_FILE_PATH  = "users.txt"
GITHUB_BRANCH     = "main"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("NetflixBot")

# ------------------------------
# Cookie-file rotation tracker
# ------------------------------
class CookieRotator:
    def __init__(self):
        self._used: set[Path] = set()

    def pick(self) -> Path | None:
        all_files = list(COOKIES_FOLDER.glob("*.txt"))
        if not all_files:
            return None

        available = [f for f in all_files if f not in self._used]

        if not available:
            log.info("CookieRotator: resetting rotation pool.")
            self._used.clear()
            available = all_files

        chosen = random.choice(available)
        self._used.add(chosen)
        return chosen

cookie_rotator = CookieRotator()

# ------------------------------
# Remote / local user logger (Updated with RTL Support)
# ------------------------------
async def log_user_activity(
    interaction: discord.Interaction,
    result: str,
    chosen_file: str = "N/A"
) -> None:
    """
    تنسيق السجل المطلوب:
    [Date and Time] User: | Username: | ID: | Account Created: | Server: (ID: ) | Member Since: | Channel: | Roles: | Status: | Result:
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    user = interaction.user
    guild = interaction.guild
    channel = interaction.channel

    # جلب معلومات العضو للحصول على الأدوار وتاريخ الانضمام[cite: 1]
    member = guild.get_member(user.id) if guild else None
    if member is None and guild:
        try:
            member = await guild.fetch_member(user.id)
        except Exception:
            member = None

    # حل مشكلة RTL/LTR: نضع علامة \u200f قبل وبعد النصوص التي قد تحتوي على العربية[cite: 1]
    def fix_rtl(text):
        return f"\u200f{text}\u200f" if text else "N/A"

    display_name    = fix_rtl((member.nick if member and member.nick else None) or user.global_name or user.name)
    username_full   = str(user) # الاسم التقني (مثل user#0000)
    user_id         = user.id
    account_created = user.created_at.strftime("%Y-%m-%d %H:%M:%S UTC") if user.created_at else "N/A"

    guild_name      = fix_rtl(guild.name) if guild else "DM"
    guild_id        = guild.id if guild else "N/A"
    channel_name    = fix_rtl(f"#{channel.name}") if hasattr(channel, "name") else "Unknown"
    channel_id      = channel.id if channel else "N/A"

    member_since    = (member.joined_at.strftime("%Y-%m-%d %H:%M:%S UTC") if member and member.joined_at else "N/A")

    roles = "None"
    if member:
        role_names = [fix_rtl(r.name) for r in member.roles if r.name != "@everyone"]
        roles = ", ".join(role_names) if role_names else "None"

    status = str(member.status).capitalize() if member else "Unknown"

    # صياغة السطر بالتنسيق الجديد المطلوب تماماً[cite: 1]
    line = (
        f"[{now}] "
        f"User: {display_name} | "
        f"Username: {username_full} | "
        f"ID: {user_id} | "
        f"Account Created: {account_created} | "
        f"Server: {guild_name} (ID: {guild_id}) | "
        f"Member Since: {member_since} | "
        f"Channel: {channel_name} (ID: {channel_id}) | "
        f"Roles: {roles} | "
        f"Status: {status} | "
        f"Result: {result}\n"
    )

    # 1) الحفظ محلياً بترميز utf-8[cite: 1]
    try:
        LOCAL_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOCAL_LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception as exc:
        log.error(f"Failed to write local log: {exc}")

    log.info(f"Activity logged → {line.strip()}")

    # 2) الرفع إلى GitHub
    if GITHUB_TOKEN:
        asyncio.create_task(_push_log_to_github(line))

async def _push_log_to_github(new_line: str) -> None:
    api_url = f"https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/contents/{GITHUB_FILE_PATH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, headers=headers, params={"ref": GITHUB_BRANCH}) as resp:
                if resp.status == 404:
                    current_content, sha = "", None
                elif resp.status == 200:
                    data = await resp.json()
                    current_content = base64.b64decode(data["content"]).decode("utf-8")
                    sha = data["sha"]
                else: return

            updated_content = current_content + new_line
            encoded = base64.b64encode(updated_content.encode("utf-8")).decode("utf-8")
            payload = {"message": f"log: {datetime.now(timezone.utc)}", "content": encoded, "branch": GITHUB_BRANCH}
            if sha: payload["sha"] = sha
            await session.put(api_url, headers=headers, json=payload)
    except Exception as exc:
        log.error(f"GitHub push error: {exc}")

# ------------------------------
# Translations
# ------------------------------
TRANSLATIONS = {
    "en": {
        "lang_prompt":              "🌐 **Please select your language:**\n🌐 **الرجاء اختيار اللغة:**",
        "lang_selected":            "✅ Language selected: **English**",
        "confirm_prompt":           "**Do you want to generate a Netflix login link?**\n",
        "progress":                 "⏳ **Generating your Netflix link... please wait.**",
        "no_cookies_folder":        "❌ Cookies folder not found.",
        "no_cookie_files":          "❌ No accounts available.",
        "timeout":                  "⌛ Process timed out.",
        "unexpected_error":         "⚠️ An error occurred.",
        "cookie_invalid":           "❌ Session invalid.",
        "success_title":            "✅ PC Login Link Ready",
        "success_desc":             "Click below to login:\n\n{link}",
        "footer":                   "⚠️ Personal use only.",
        "tv_instruction":           "📺 **TV Activation:** Visit **netflix.com/tv9** and enter the code.",
        "yes_label":                "Yes, generate link",
        "no_label":                 "No, cancel",
        "cancelled":                "❌ Process cancelled.",
        "not_for_you":              "❌ Not for you.",
        "timeout_msg":              "⏰ Request timed out.",
        "wrong_channel_no_config":  "⚠️ Run `/channel` first.",
        "wrong_channel_with_config":"❌ Use this in {channel}.",
        "wrong_guild":              "❌ Restricted bot.",
        "setup_desc":               "Use `/create` to get a Netflix link. Messages delete after 1 min.",
    },
    "ar": {
        "lang_prompt":              "🌐 **Please select your language:**\n🌐 **الرجاء اختيار اللغة:**",
        "lang_selected":            "✅ تم اختيار اللغة: **العربية**",
        "confirm_prompt":           "**هل تريد إنشاء رابط تسجيل دخول لـ نتفليكس؟**\n",
        "progress":                 "⏳ **جاري إنشاء الرابط الخاص بك... يرجى الانتظار.**",
        "no_cookies_folder":        "❌ مجلد ملفات تعريف الارتباط غير موجود.",
        "no_cookie_files":          "❌ لا توجد حسابات متاحة حالياً.",
        "timeout":                  "⌛ استغرق التحقق وقتاً طويلاً.",
        "unexpected_error":         "⚠️ حدث خطأ غير متوقع.",
        "cookie_invalid":           "❌ الحساب المختار غير صالح.",
        "success_title":            "✅ رابط دخول الكمبيوتر جاهز",
        "success_desc":             "انقر على الرابط أدناه لتسجيل الدخول تلقائياً:\n\n{link}",
        "footer":                   "⚠️ هذا الرابط للاستخدام الشخصي فقط.",
        "tv_instruction":           "📺 **تفعيل التلفاز:** قم بزيارة **netflix.com/tv9** وأدخل الرمز.",
        "yes_label":                "نعم، أنشئ الرابط",
        "no_label":                 "لا، إلغاء",
        "cancelled":                "❌ تم إلغاء العملية.",
        "not_for_you":              "❌ لا يمكنك التفاعل مع هذه القائمة.",
        "timeout_msg":              "⏰ انتهت مهلة الطلب.",
        "wrong_channel_no_config":  "⚠️ استخدم أمر `/channel` أولاً.",
        "wrong_channel_with_config":"❌ لا يمكن استخدام هذا الأمر إلا في {channel}.",
        "wrong_guild":              "❌ هذا البوت مخصص لسيرفر محدد فقط.",
        "setup_desc":               "استخدم أمر `/create` للحصول على رابط. تحذف الرسائل تلقائياً بعد دقيقة.",
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
                    self.allowed_channel_id = json.load(f).get("allowed_channel_id")
            except Exception: pass

    def save(self):
        with open(CONFIG_FILE, "w") as f:
            json.dump({"allowed_channel_id": self.allowed_channel_id}, f, indent=2)

config = Config()

# ------------------------------
# Bot setup & Interaction Views
# ------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

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
            await interaction.response.send_message(TRANSLATIONS[get_user_lang(interaction)]["not_for_you"], ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self.original_interaction.delete_original_response()
        await interaction.followup.send(TRANSLATIONS[lang]["confirm_prompt"], view=ConfirmView(interaction.user, self.original_interaction, lang), ephemeral=True)

class ConfirmView(discord.ui.View):
    def __init__(self, original_user, original_interaction, language):
        super().__init__(timeout=60)
        self.original_user = original_user
        self.language = language
        self.add_item(discord.ui.Button(label=TRANSLATIONS[language]["yes_label"], style=discord.ButtonStyle.green, custom_id="yes"))
        self.add_item(discord.ui.Button(label=TRANSLATIONS[language]["no_label"], style=discord.ButtonStyle.red, custom_id="no"))

    @bot.event
    async def on_interaction(self, interaction: discord.Interaction):
        if not interaction.data or "custom_id" not in interaction.data: return
        cid = interaction.data["custom_id"]
        if cid == "yes":
            await interaction.response.edit_message(content=TRANSLATIONS[self.language]["progress"], view=None)
            await self.generate_link(interaction)
        elif cid == "no":
            await interaction.response.edit_message(content=TRANSLATIONS[self.language]["cancelled"], view=None)
            await log_user_activity(interaction, result="Cancelled by user")

    async def generate_link(self, interaction: discord.Interaction):
        lang = self.language
        t = TRANSLATIONS[lang]
        chosen_file = cookie_rotator.pick()
        if not chosen_file:
            await interaction.edit_original_response(content=t["no_cookie_files"])
            return

        try:
            result = await asyncio.wait_for(asyncio.to_thread(check_cookie_file, str(chosen_file)), timeout=SCRIPT_TIMEOUT)
            if result:
                embed = discord.Embed(title=t["success_title"], description=t["success_desc"].format(link=result), color=discord.Color.green())
                await interaction.edit_original_response(content=None, embed=embed)
                tv_msg = await interaction.followup.send(t["tv_instruction"], ephemeral=True)
                await log_user_activity(interaction, result="Success", chosen_file=chosen_file.name)
                # Cleanup logic here...
            else:
                await interaction.edit_original_response(content=t["cookie_invalid"])
                await log_user_activity(interaction, result="Failed - Invalid", chosen_file=chosen_file.name)
        except Exception as e:
            await interaction.edit_original_response(content=t["unexpected_error"])
            await log_user_activity(interaction, result=f"Error: {e}", chosen_file=chosen_file.name)

# ------------------------------
# Commands
# ------------------------------
@bot.tree.command(name="channel", description="Set bot channel (Admin)")
@app_commands.default_permissions(administrator=True)
async def set_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    config.allowed_channel_id = channel.id
    config.save()
    await interaction.response.send_message(f"✅ Setup in {channel.mention}", ephemeral=True)

@bot.tree.command(name="create", description="Generate Netflix link")
async def create(interaction: discord.Interaction):
    if interaction.channel_id != config.allowed_channel_id:
        await interaction.response.send_message("❌ Wrong channel.", ephemeral=True)
        return
    await interaction.response.send_message(TRANSLATIONS["en"]["lang_prompt"], view=LanguageSelectView(interaction), ephemeral=True)

@bot.event
async def on_ready():
    log.info(f"Bot {bot.user} is ready.")
    await bot.tree.sync(guild=discord.Object(id=ALLOWED_GUILD_ID))

if __name__ == "__main__":
    COOKIES_FOLDER.mkdir(exist_ok=True)
    bot.run(DISCORD_BOT_TOKEN)
