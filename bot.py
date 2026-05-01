# bot.py
import os
import json
import random
import asyncio
import logging
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from netflix_checker import check_cookie_file

# ------------------------------
# Configuration
# ------------------------------
COOKIES_FOLDER = Path("cookies")
SCRIPT_TIMEOUT = 30
CONFIG_FILE = Path("config.json")
CLEANUP_DELAY_SECONDS = 120

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_TOKEN")
if not DISCORD_BOT_TOKEN:
    raise ValueError("Missing DISCORD_TOKEN environment variable")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("NetflixBot")

# ------------------------------
# Translations
# ------------------------------
TRANSLATIONS = {
    "en": {
        "lang_prompt": "🌐 **Please select language / الرجاء اختيار اللغة:**",
        "lang_selected": "✅ Language selected: English",
        "confirm_prompt": "**Do you want to generate a Netflix login link?**\nThis process will use one random cookie file.",
        "progress": "⏳ **Generating your Netflix link…**",
        "no_cookies_folder": "❌ Cookies folder not found. Contact the bot admin.",
        "no_cookie_files": "❌ No cookie files found in the folder.",
        "timeout": "⌛ The check took too long. Please try again later.",
        "unexpected_error": "⚠️ An unexpected error occurred while processing the file.",
        "cookie_invalid": "❌ The selected cookie file is invalid or expired.",
        "success_title": "✅ PC Login Link Ready",
        "success_desc": "Click the link below to log in automatically:\n\n{link}",
        "footer": "This link is personal – do not share it.",
        "tv_instruction": "You can also run the account on the TV through this link netflix.com/tv9 and enter the code for TV.",
        "yes_label": "Yes",
        "no_label": "No",
        "cancelled": "❌ Cancelled.",
        "not_for_you": "❌ This button is not for you.",
        "timeout_msg": "⏰ Timeout – no response received.",
        "wrong_channel_no_config": "⚠️ No channel has been configured. Ask an admin to use `/channel`.",
        "wrong_channel_with_config": "❌ This command can only be used in {channel}.",
    },
    "ar": {
        "lang_prompt": "🌐 **الرجاء اختيار اللغة / Please select language:**",
        "lang_selected": "✅ تم اختيار اللغة: العربية",
        "confirm_prompt": "**هل تريد إنشاء رابط دخول نتفليكس؟**\nستستخدم هذه العملية ملف كعكات عشوائي واحد.",
        "progress": "⏳ **جاري إنشاء رابط نتفليكس…**",
        "no_cookies_folder": "❌ مجلد الكعكات غير موجود. اتصل بمدير البوت.",
        "no_cookie_files": "❌ لا توجد ملفات كعكات في المجلد.",
        "timeout": "⌛ استغرق التحقق وقتًا طويلاً. يرجى المحاولة لاحقًا.",
        "unexpected_error": "⚠️ حدث خطأ غير متوقع أثناء معالجة الملف.",
        "cookie_invalid": "❌ ملف الكعكات المختار غير صالح أو منتهي الصلاحية.",
        "success_title": "✅ رابط دخول الكمبيوتر جاهز",
        "success_desc": "انقر على الرابط أدناه لتسجيل الدخول تلقائيًا:\n\n{link}",
        "footer": "هذا الرابط شخصي – لا تشاركه.",
        "tv_instruction": "يمكنك أيضًا تشغيل الحساب على التلفزيون عبر هذا الرابط netflix.com/tv9 وإدخال الرمز للتلفزيون.",
        "yes_label": "نعم",
        "no_label": "لا",
        "cancelled": "❌ تم الإلغاء.",
        "not_for_you": "❌ هذا الزر ليس لك.",
        "timeout_msg": "⏰ انتهى الوقت – لم يتم استلام رد.",
        "wrong_channel_no_config": "⚠️ لم يتم تكوين أي قناة. اطلب من المدير استخدام `/channel`.",
        "wrong_channel_with_config": "❌ لا يمكن استخدام هذا الأمر إلا في {channel}.",
    }
}

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
            await interaction.response.send_message(TRANSLATIONS[lang]["not_for_you"], ephemeral=True)
            return

        # Disable buttons and update original message
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=TRANSLATIONS[lang]["lang_selected"],
            view=self
        )

        # Show confirmation view in chosen language
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
        self.value = None
        self.tv_message = None

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.green)
    async def yes_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.original_user.id:
            await interaction.response.send_message(TRANSLATIONS[self.language]["not_for_you"], ephemeral=True)
            return
        self.value = True
        # Immediately edit the message to show progress
        await interaction.response.edit_message(
            content=TRANSLATIONS[self.language]["progress"],
            view=None
        )
        await self.generate_link(interaction)
        self.stop()

    @discord.ui.button(label="No", style=discord.ButtonStyle.red)
    async def no_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.original_user.id:
            await interaction.response.send_message(TRANSLATIONS[self.language]["not_for_you"], ephemeral=True)
            return
        self.value = False
        await interaction.response.send_message(TRANSLATIONS[self.language]["cancelled"], ephemeral=True)
        self.stop()

    async def generate_link(self, interaction: discord.Interaction):
        lang = self.language
        t = TRANSLATIONS[lang]

        if not COOKIES_FOLDER.exists():
            await interaction.edit_original_response(content=t["no_cookies_folder"])
            return

        txt_files = list(COOKIES_FOLDER.glob("*.txt"))
        if not txt_files:
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
            await interaction.edit_original_response(content=t["timeout"])
            return
        except Exception as e:
            log.error(f"Checker error: {e}")
            await interaction.edit_original_response(content=t["unexpected_error"])
            return

        if result:
            embed = discord.Embed(
                title=t["success_title"],
                description=t["success_desc"].format(link=result),
                color=discord.Color.green()
            )
            embed.set_footer(text=t["footer"])
            await interaction.edit_original_response(content=None, embed=embed)

            # TV instruction – ephemeral, only the user sees it
            self.tv_message = await interaction.followup.send(
                t["tv_instruction"],
                ephemeral=True
            )

            # Cleanup
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
                followup_message=self.tv_message,
                delay_seconds=CLEANUP_DELAY_SECONDS
            ))

            log.info(f"Link sent to {interaction.user} – cleanup in {CLEANUP_DELAY_SECONDS}s")
        else:
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
        except discord.Forbidden:
            log.warning("Missing manage_messages permission to delete user command message.")
        except Exception as e:
            log.error(f"Failed to delete command message: {e}")

    if original_response:
        try:
            await original_response.delete()
        except Exception as e:
            log.error(f"Failed to delete original response: {e}")

    if followup_message:
        try:
            await followup_message.delete()
        except Exception as e:
            log.error(f"Failed to delete followup message: {e}")

# ------------------------------
# /channel command
# ------------------------------
@bot.tree.command(name="channel", description="Set the text channel where the bot will work (Admin only)")
@app_commands.default_permissions(administrator=True)
async def set_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You need administrator permissions.", ephemeral=True)
        return

    config.set_allowed_channel(channel.id)
    await interaction.response.send_message(f"✅ Bot will now **only** respond in {channel.mention}.", ephemeral=True)

    embed = discord.Embed(
        title="🎬 Netflix Link Generator",
        description=(
            "Welcome! Use the `/create` command to generate a Netflix PC login link.\n\n"
            "**How to use:**\n"
            "1. Type `/create` in this channel.\n"
            "2. Select your language (English or Arabic).\n"
            "3. Click **Yes** on the confirmation buttons.\n"
            "4. Wait a few seconds – the bot will give you a personal login link.\n"
            "5. Use the link to log in on your PC browser.\n\n"
            "**Note:** The link is one-time use and expires shortly.\n"
            "The bot will automatically delete all messages after 2 minutes for privacy."
        ),
        color=discord.Color.blue()
    )
    try:
        setup_msg = await channel.send(embed=embed)
        await setup_msg.pin()
        log.info(f"Pinned setup message in {channel.name} (ID: {channel.id})")
    except discord.Forbidden:
        log.warning(f"Missing permissions to send/pin message in {channel.name}")
    except Exception as e:
        log.error(f"Failed to send setup message: {e}")

# ------------------------------
# /create command
# ------------------------------
@bot.tree.command(name="create", description="Generate a Netflix PC login link from a random cookie file")
async def create(interaction: discord.Interaction):
    if not is_allowed_channel(interaction):
        if config.allowed_channel_id is None:
            await interaction.response.send_message(
                TRANSLATIONS["en"]["wrong_channel_no_config"],
                ephemeral=True
            )
        else:
            allowed_channel = bot.get_channel(config.allowed_channel_id)
            msg = TRANSLATIONS["en"]["wrong_channel_with_config"].format(channel=allowed_channel.mention)
            await interaction.response.send_message(msg, ephemeral=True)
        return

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
    try:
        synced = await bot.tree.sync()
        log.info(f"Synced {len(synced)} slash commands")
    except Exception as e:
        log.error(f"Failed to sync commands: {e}")

# ------------------------------
# Run the bot
# ------------------------------
if __name__ == "__main__":
    COOKIES_FOLDER.mkdir(exist_ok=True)
    bot.run(DISCORD_BOT_TOKEN)
