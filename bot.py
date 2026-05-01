import os
import json
import random
import asyncio
import logging
from pathlib import Path
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

# Note: Ensure netflix_checker.py is in the same directory
from netflix_checker import check_cookie_file

# ------------------------------
# Configuration
# ------------------------------
ALLOWED_GUILD_ID = 1494152777381711945   

COOKIES_FOLDER = Path("cookies")
SCRIPT_TIMEOUT = 30
CONFIG_FILE = Path("config.json")
CLEANUP_DELAY_SECONDS = 120
USER_DATA_FILE = Path("users.txt")

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_TOKEN")
if not DISCORD_BOT_TOKEN:
    raise ValueError("Missing DISCORD_TOKEN environment variable")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("NetflixBot")

# ------------------------------
# User Data Logger
# ------------------------------
def log_user_activity(user: discord.User | discord.Member, condition: str):
    """Records user interaction details into users.txt."""
    USER_DATA_FILE.touch(exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = (
        f"[{timestamp}] ID: {user.id} | "
        f"Username: {user.name} | "
        f"Display Name: {user.display_name} | "
        f"Date and time: {timestamp} | "
        f"Condition: {condition}\n"
    )
    
    with open(USER_DATA_FILE, "a", encoding="utf-8") as f:
        f.write(log_entry)

# ------------------------------
# Translations
# ------------------------------
TRANSLATIONS = {
    "en": {
        "lang_prompt": "🌐 **Please select your language:**\n🌐 **الرجاء اختيار اللغة:**",
        "lang_selected": "✅ Language selected: **English**",
        "confirm_prompt": "**Do you want to generate a Netflix login link?**\n",
        "progress": "⏳ **Generating your Netflix link... please wait.**",
        "no_cookies_folder": "❌ Cookies folder not found. Please contact the administrator.",
        "no_cookie_files": "❌ No accounts available in the database right now.",
        "timeout": "⌛ The validation process took too long. Please try again later.",
        "unexpected_error": "⚠️ An unexpected error occurred. Please try again.",
        "cookie_invalid": "❌ The selected session is invalid or expired. Try again.",
        "success_title": "✅ PC Login Link Ready",
        "success_desc": "Click the link below to log in automatically:\n\n{link}",
        "footer": "⚠️ This link is for personal use only – do not share it.",
        "tv_instruction": "📺 **TV Activation:** You can also activate the account on your TV by visiting **www.netflix.com/tv9** and entering the code displayed on your screen.",
        "yes_label": "Yes, generate link",
        "no_label": "No, cancel",
        "cancelled": "❌ Process cancelled.",
        "not_for_you": "❌ You cannot interact with this menu.",
        "timeout_msg": "⏰ Request timed out due to inactivity.",
        "wrong_channel_no_config": "⚠️ No channel configured. Admins must run `/channel`.",
        "wrong_channel_with_config": "❌ This command can only be used in {channel}.",
        "wrong_guild": "❌ This bot is restricted to a specific server.",
        "setup_desc": "Welcome! Use the `/create` command to generate a Netflix PC login link.\n\n**How to use:**\n1. Type `/create` in this channel.\n2. Select your language.\n3. Confirm generation.\n4. Wait a few seconds for your personal link.\n\n*Note: The link is single-use. Messages auto-delete after 2 minutes for privacy.*"
    },
    "ar": {
        "lang_prompt": "🌐 **Please select your language:**\n🌐 **الرجاء اختيار اللغة:**",
        "lang_selected": "✅ تم اختيار اللغة: **العربية**",
        "confirm_prompt": "**هل تريد إنشاء رابط تسجيل دخول لـ نتفليكس؟**\n",
        "progress": "⏳ **جاري إنشاء الرابط الخاص بك... يرجى الانتظار.**",
        "no_cookies_folder": "❌ مجلد ملفات تعريف الارتباط غير موجود. يرجى الاتصال بالمسؤول.",
        "no_cookie_files": "❌ لا توجد حسابات متاحة حالياً في قاعدة البيانات.",
        "timeout": "⌛ استغرق التحقق وقتاً طويلاً. يرجى المحاولة مرة أخرى لاحقاً.",
        "unexpected_error": "⚠️ حدث خطأ غير متوقع أثناء معالجة الطلب.",
        "cookie_invalid": "❌ الحساب المختار غير صالح أو منتهي الصلاحية. حاول مجدداً.",
        "success_title": "✅ رابط دخول الكمبيوتر جاهز",
        "success_desc": "انقر على الرابط أدناه لتسجيل الدخول تلقائياً:\n\n{link}",
        "footer": "⚠️ هذا الرابط للاستخدام الشخصي فقط – يُمنع مشاركته.",
        "tv_instruction": "📺 **تفعيل التلفاز:** يمكنك تفعيل الحساب على التلفاز بزيارة الرابط **www.netflix.com/tv9** وإدخال الرمز المعروض على شاشتك.",
        "yes_label": "نعم، أنشئ الرابط",
        "no_label": "لا، إلغاء",
        "cancelled": "❌ تم إلغاء العملية.",
        "not_for_you": "❌ لا يمكنك التفاعل مع هذه القائمة.",
        "timeout_msg": "⏰ انتهت مهلة الطلب بسبب عدم التفاعل.",
        "wrong_channel_no_config": "⚠️ لم يتم إعداد القناة. يجب على المسؤول استخدام أمر `/channel`.",
        "wrong_channel_with_config": "❌ لا يمكن استخدام هذا الأمر إلا في {channel}.",
        "wrong_guild": "❌ هذا البوت مخصص للعمل في سيرفر محدد فقط.",
        "setup_desc": "مرحباً! استخدم أمر `/create` لإنشاء رابط تسجيل دخول لـ نتفليكس.\n\n**طريقة الاستخدام:**\n1. اكتب `/create` في هذه القناة.\n2. اختر لغتك المفضلة.\n3. قم بتأكيد الإنشاء.\n4. انتظر بضع ثوانٍ للحصول على رابطك الشخصي.\n\n*ملاحظة: الروابط للاستخدام مرة واحدة. يتم حذف الرسائل تلقائياً بعد دقيقتين للخصوصية.*"
    }
}

def get_user_lang(interaction: discord.Interaction) -> str:
    locale = str(interaction.locale)
    return "ar" if locale.startswith("ar") else "en"

# ------------------------------
# Config manager
# ------------------------------
class Config:
    def __init__(self):
        self.allowed_channel_id = None
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
bot = commands.Bot(command_prefix="!", intents=intents)

def is_allowed_channel(interaction: discord.Interaction) -> bool:
    if config.allowed_channel_id is None:
        return False
    return interaction.channel_id == config.allowed_channel_id

async def global_interaction_check(interaction: discord.Interaction) -> bool:
    if interaction.guild is None or interaction.guild.id != ALLOWED_GUILD_ID:
        lang = get_user_lang(interaction)
        if interaction.response.is_done():
            await interaction.followup.send(TRANSLATIONS[lang]["wrong_guild"], ephemeral=True)
        else:
            await interaction.response.send_message(TRANSLATIONS[lang]["wrong_guild"], ephemeral=True)
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
            await interaction.response.send_message(TRANSLATIONS[user_lang]["not_for_you"], ephemeral=True)
            return

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=TRANSLATIONS[lang]["lang_selected"],
            view=self
        )

        confirm_view = ConfirmView(self.original_interaction.user, self.original_interaction, lang)
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
        except:
            pass

# ------------------------------
# Confirmation View (Yes / No)
# ------------------------------
class ConfirmView(discord.ui.View):
    def __init__(self, original_user: discord.User | discord.Member, original_interaction: discord.Interaction, language: str):
        super().__init__(timeout=60)
        self.original_user = original_user
        self.original_interaction = original_interaction
        self.language = language
        
        self.yes_btn = discord.ui.Button(label=TRANSLATIONS[language]["yes_label"], style=discord.ButtonStyle.green)
        self.yes_btn.callback = self.yes_callback
        
        self.no_btn = discord.ui.Button(label=TRANSLATIONS[language]["no_label"], style=discord.ButtonStyle.red)
        self.no_btn.callback = self.no_callback
        
        self.add_item(self.yes_btn)
        self.add_item(self.no_btn)

    async def yes_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.original_user.id:
            await interaction.response.send_message(TRANSLATIONS[self.language]["not_for_you"], ephemeral=True)
            return
        
        await interaction.response.edit_message(
            content=TRANSLATIONS[self.language]["progress"],
            view=None
        )
        await self.generate_link(interaction)
        self.stop()

    async def no_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.original_user.id:
            await interaction.response.send_message(TRANSLATIONS[self.language]["not_for_you"], ephemeral=True)
            return
            
        log_user_activity(interaction.user, "User cancelled")
        await interaction.response.edit_message(content=TRANSLATIONS[self.language]["cancelled"], view=None)
        self.stop()

    async def generate_link(self, interaction: discord.Interaction):
        lang = self.language
        t = TRANSLATIONS[lang]

        if not COOKIES_FOLDER.exists():
            log_user_activity(interaction.user, "Error: Folder missing")
            await interaction.edit_original_response(content=t["no_cookies_folder"])
            return

        txt_files = list(COOKIES_FOLDER.glob("*.txt"))
        if not txt_files:
            log_user_activity(interaction.user, "Error: No cookies")
            await interaction.edit_original_response(content=t["no_cookie_files"])
            return

        chosen_file = random.choice(txt_files)
        log.info(f"User {interaction.user} triggered check for file: {chosen_file}")

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(check_cookie_file, str(chosen_file)),
                timeout=SCRIPT_TIMEOUT
            )
        except asyncio.TimeoutError:
            log_user_activity(interaction.user, "Error: Timeout")
            await interaction.edit_original_response(content=t["timeout"])
            return
        except Exception as e:
            log_user_activity(interaction.user, f"Error: {str(e)}")
            log.error(f"Checker error: {e}")
            await interaction.edit_original_response(content=t["unexpected_error"])
            return

        if result:
            log_user_activity(interaction.user, "Success: Link Generated")
            embed = discord.Embed(
                title=t["success_title"],
                description=t["success_desc"].format(link=result),
                color=discord.Color.green()
            )
            embed.set_footer(text=t["footer"])
            await interaction.edit_original_response(content=None, embed=embed)

            tv_message = await interaction.followup.send(
                t["tv_instruction"],
                ephemeral=True
            )

            channel = interaction.channel
            command_message = None
            try:
                async for msg in channel.history(limit=5):
                    if msg.author == interaction.user and msg.interaction and msg.interaction.id == interaction.id:
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
        else:
            log_user_activity(interaction.user, "Error: Invalid Cookie")
            await interaction.edit_original_response(content=t["cookie_invalid"])

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        try:
            await self.original_interaction.edit_original_response(
                content=TRANSLATIONS[self.language]["timeout_msg"],
                view=None
            )
        except:
            pass

# ------------------------------
# Cleanup task
# ------------------------------
async def cleanup_messages(
    channel: discord.TextChannel,
    command_message: discord.Message,
    original_response: discord.WebhookMessage,
    followup_message: discord.Message,
    delay_seconds: int = CLEANUP_DELAY_SECONDS
):
    await asyncio.sleep(delay_seconds)

    if command_message:
        try:
            await command_message.delete()
        except:
            pass

    if original_response:
        try:
            await original_response.delete()
        except:
            pass

    if followup_message:
        try:
            await followup_message.delete()
        except:
            pass

# ------------------------------
# /channel command
# ------------------------------
@bot.tree.command(name="channel", description="Set the text channel where the bot will work (Admin only)")
@app_commands.default_permissions(administrator=True)
async def set_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator:
        lang = get_user_lang(interaction)
        msg = "❌ You need administrator permissions." if lang == "en" else "❌ تحتاج إلى صلاحيات المسؤول."
        await interaction.response.send_message(msg, ephemeral=True)
        return

    config.set_allowed_channel(channel.id)
    
    lang = get_user_lang(interaction)
    success_msg = f"✅ Bot will now **only** respond in {channel.mention}." if lang == "en" else f"✅ البوت سيعمل الآن **فقط** في {channel.mention}."
    await interaction.response.send_message(success_msg, ephemeral=True)

    embed = discord.Embed(
        title="🎬 Netflix Link Generator | مولد روابط نتفليكس",
        description=f"**English:**\n{TRANSLATIONS['en']['setup_desc']}\n\n**العربية:**\n{TRANSLATIONS['ar']['setup_desc']}",
        color=discord.Color.blue()
    )
    embed.set_footer(text="X2 Salah Utility")
    
    try:
        setup_msg = await channel.send(embed=embed)
        await setup_msg.pin()
    except Exception as e:
        log.error(f"Failed to send setup message: {e}")

# ------------------------------
# /create command
# ------------------------------
@bot.tree.command(name="create", description="Generate a Netflix PC login link from a random cookie file")
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
            msg = TRANSLATIONS[user_lang]["wrong_channel_with_config"].format(channel=allowed_channel.mention if allowed_channel else "the designated channel")
            await interaction.response.send_message(msg, ephemeral=True)
        return

    # Trigger language prompt
    view = LanguageSelectView(interaction)
    await interaction.response.send_message(
        TRANSLATIONS["en"]["lang_prompt"],
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
        await bot.tree.sync(guild=guild)
        log.info(f"Synced slash commands to guild {ALLOWED_GUILD_ID}")
    except Exception as e:
        log.error(f"Failed to sync commands: {e}")

if __name__ == "__main__":
    COOKIES_FOLDER.mkdir(exist_ok=True)
    bot.run(DISCORD_BOT_TOKEN)
