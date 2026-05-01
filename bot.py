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

# MODIFIED: Cleanup delay reduced from 180 to 120 seconds (2 minutes)
CLEANUP_DELAY_SECONDS = 120

# ------------------------------
# Read token from environment variable
# ------------------------------
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_TOKEN")
if not DISCORD_BOT_TOKEN:
    raise ValueError("Missing DISCORD_TOKEN environment variable")

# ------------------------------
# Logging setup
# ------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("NetflixBot")

# ------------------------------
# Configuration manager (channel restriction)
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
intents.message_content = True  # needed to delete user messages (manage_messages)
bot = commands.Bot(command_prefix="!", intents=intents)

# ------------------------------
# Helper: check if command is used in allowed channel
# ------------------------------
def is_allowed_channel(interaction: discord.Interaction) -> bool:
    if config.allowed_channel_id is None:
        return False
    return interaction.channel_id == config.allowed_channel_id

# ------------------------------
# Slash command: /channel (admin only)
# ------------------------------
@bot.tree.command(name="channel", description="Set the text channel where the bot will work (Admin only)")
@app_commands.default_permissions(administrator=True)
async def set_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    """Select a text channel – all bot commands will be restricted to this channel."""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You need administrator permissions.", ephemeral=True)
        return

    config.set_allowed_channel(channel.id)
    await interaction.response.send_message(f"✅ Bot will now **only** respond in {channel.mention}.", ephemeral=True)

    # Send setup message in the chosen channel and pin it
    embed = discord.Embed(
        title="🎬 Netflix Link Generator",
        description=(
            "Welcome! Use the `/create` command to generate a Netflix PC login link.\n\n"
            "**How to use:**\n"
            "1. Type `/create` in this channel.\n"
            "2. Click **Yes** on the confirmation buttons.\n"
            "3. Wait a few seconds – the bot will give you a personal login link.\n"
            "4. Use the link to log in on your PC browser.\n\n"
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
# Confirmation View (Yes / No)
# ------------------------------
class ConfirmView(discord.ui.View):
    def __init__(self, original_user: discord.User | discord.Member, original_interaction: discord.Interaction):
        super().__init__(timeout=60)
        self.original_user = original_user
        self.original_interaction = original_interaction
        self.value = None  # True = Yes, False = No

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.green)
    async def yes_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.original_user.id:
            await interaction.response.send_message("❌ This button is not for you.", ephemeral=True)
            return
        self.value = True
        self.stop()
        await interaction.response.defer(ephemeral=True)

    @discord.ui.button(label="No", style=discord.ButtonStyle.red)
    async def no_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.original_user.id:
            await interaction.response.send_message("❌ This button is not for you.", ephemeral=True)
            return
        self.value = False
        self.stop()
        await interaction.response.send_message("❌ Cancelled.", ephemeral=True)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            await self.original_interaction.edit_original_response(content="⏰ Timeout – no response received.", view=None)
        except:
            pass

# ------------------------------
# Cleanup task: delete all related messages after delay
# ------------------------------
async def cleanup_messages(
    channel: discord.TextChannel,
    command_message: discord.Message,
    original_response: discord.WebhookMessage,
    followup_message: discord.Message,
    delay_seconds: int = CLEANUP_DELAY_SECONDS
):
    """Wait `delay_seconds` then delete all provided messages."""
    await asyncio.sleep(delay_seconds)

    # Delete the user's command message if it exists and bot has manage_messages
    if command_message:
        try:
            await command_message.delete()
        except discord.Forbidden:
            log.warning("Missing manage_messages permission to delete user command message.")
        except Exception as e:
            log.error(f"Failed to delete command message: {e}")

    # Delete the bot's ephemeral original response (the embed)
    if original_response:
        try:
            await original_response.delete()
        except Exception as e:
            log.error(f"Failed to delete original response: {e}")

    # Delete the followup TV message
    if followup_message:
        try:
            await followup_message.delete()
        except Exception as e:
            log.error(f"Failed to delete followup message: {e}")

# ------------------------------
# Modified /create command
# ------------------------------
@bot.tree.command(name="create", description="Generate a Netflix PC login link from a random cookie file")
async def create(interaction: discord.Interaction):
    # 1. Channel restriction check
    if not is_allowed_channel(interaction):
        if config.allowed_channel_id is None:
            await interaction.response.send_message("⚠️ No channel has been configured. Ask an admin to use `/channel`.", ephemeral=True)
        else:
            allowed_channel = bot.get_channel(config.allowed_channel_id)
            await interaction.response.send_message(f"❌ This command can only be used in {allowed_channel.mention} (if set).", ephemeral=True)
        return

    # 2. Send confirmation buttons (Yes/No) in an ephemeral message
    view = ConfirmView(original_user=interaction.user, original_interaction=interaction)
    await interaction.response.send_message(
        "**Do you want to generate a Netflix login link?**\nThis process will use one random cookie file.",
        view=view,
        ephemeral=True
    )
    # Wait for button interaction
    timeout = await view.wait()
    if timeout or view.value is None:
        return

    if not view.value:  # User clicked No
        return

    # User clicked Yes – proceed
    await interaction.edit_original_response(content="⏳ **Generating your Netflix link…**", view=None)

    # Check cookies folder
    if not COOKIES_FOLDER.exists():
        await interaction.edit_original_response(content="❌ Cookies folder not found. Contact the bot admin.")
        return

    txt_files = list(COOKIES_FOLDER.glob("*.txt"))
    if not txt_files:
        await interaction.edit_original_response(content="❌ No cookie files found in the folder.")
        return

    chosen_file = random.choice(txt_files)
    log.info(f"User {interaction.user} triggered check for file: {chosen_file}")

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(check_cookie_file, str(chosen_file)),
            timeout=SCRIPT_TIMEOUT
        )
    except asyncio.TimeoutError:
        await interaction.edit_original_response(content="⌛ The check took too long. Please try again later.")
        return
    except Exception as e:
        log.error(f"Checker error: {e}")
        await interaction.edit_original_response(content="⚠️ An unexpected error occurred while processing the file.")
        return

    if result:
        # Send the link embed in the same ephemeral response
        embed = discord.Embed(
            title="✅ PC Login Link Ready",
            description=f"Click the link below to log in automatically:\n\n{result}",
            color=discord.Color.green()
        )
        embed.set_footer(text="This link is personal – do not share it.")
        await interaction.edit_original_response(content=None, embed=embed)

        # MODIFIED: TV instruction message matches requirement exactly
        tv_msg = await interaction.followup.send(
            "You can also run the account on the TV through this link netflix.com/tv9 and enter the code for TV.",
            ephemeral=False
        )

        # Store messages for cleanup
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

        # MODIFIED: Cleanup now uses 120 seconds (2 minutes)
        asyncio.create_task(cleanup_messages(
            channel=channel,
            command_message=command_message,
            original_response=original_response,
            followup_message=tv_msg,
            delay_seconds=CLEANUP_DELAY_SECONDS
        ))

        log.info(f"Link sent to {interaction.user} – cleanup scheduled in {CLEANUP_DELAY_SECONDS}s")
    else:
        await interaction.edit_original_response(content="❌ The selected cookie file is invalid or expired.")

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
    bot.run(DISCORD_BOT_TOKEN)# bot.py
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

# MODIFIED: Cleanup delay reduced from 180 to 120 seconds (2 minutes)
CLEANUP_DELAY_SECONDS = 120

# ------------------------------
# Read token from environment variable
# ------------------------------
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_TOKEN")
if not DISCORD_BOT_TOKEN:
    raise ValueError("Missing DISCORD_TOKEN environment variable")

# ------------------------------
# Logging setup
# ------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("NetflixBot")

# ------------------------------
# Configuration manager (channel restriction)
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
intents.message_content = True  # needed to delete user messages (manage_messages)
bot = commands.Bot(command_prefix="!", intents=intents)

# ------------------------------
# Helper: check if command is used in allowed channel
# ------------------------------
def is_allowed_channel(interaction: discord.Interaction) -> bool:
    if config.allowed_channel_id is None:
        return False
    return interaction.channel_id == config.allowed_channel_id

# ------------------------------
# Slash command: /channel (admin only)
# ------------------------------
@bot.tree.command(name="channel", description="Set the text channel where the bot will work (Admin only)")
@app_commands.default_permissions(administrator=True)
async def set_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    """Select a text channel – all bot commands will be restricted to this channel."""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You need administrator permissions.", ephemeral=True)
        return

    config.set_allowed_channel(channel.id)
    await interaction.response.send_message(f"✅ Bot will now **only** respond in {channel.mention}.", ephemeral=True)

    # Send setup message in the chosen channel and pin it
    embed = discord.Embed(
        title="🎬 Netflix Link Generator",
        description=(
            "Welcome! Use the `/create` command to generate a Netflix PC login link.\n\n"
            "**How to use:**\n"
            "1. Type `/create` in this channel.\n"
            "2. Click **Yes** on the confirmation buttons.\n"
            "3. Wait a few seconds – the bot will give you a personal login link.\n"
            "4. Use the link to log in on your PC browser.\n\n"
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
# Confirmation View (Yes / No)
# ------------------------------
class ConfirmView(discord.ui.View):
    def __init__(self, original_user: discord.User | discord.Member, original_interaction: discord.Interaction):
        super().__init__(timeout=60)
        self.original_user = original_user
        self.original_interaction = original_interaction
        self.value = None  # True = Yes, False = No

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.green)
    async def yes_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.original_user.id:
            await interaction.response.send_message("❌ This button is not for you.", ephemeral=True)
            return
        self.value = True
        self.stop()
        await interaction.response.defer(ephemeral=True)

    @discord.ui.button(label="No", style=discord.ButtonStyle.red)
    async def no_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.original_user.id:
            await interaction.response.send_message("❌ This button is not for you.", ephemeral=True)
            return
        self.value = False
        self.stop()
        await interaction.response.send_message("❌ Cancelled.", ephemeral=True)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            await self.original_interaction.edit_original_response(content="⏰ Timeout – no response received.", view=None)
        except:
            pass

# ------------------------------
# Cleanup task: delete all related messages after delay
# ------------------------------
async def cleanup_messages(
    channel: discord.TextChannel,
    command_message: discord.Message,
    original_response: discord.WebhookMessage,
    followup_message: discord.Message,
    delay_seconds: int = CLEANUP_DELAY_SECONDS
):
    """Wait `delay_seconds` then delete all provided messages."""
    await asyncio.sleep(delay_seconds)

    # Delete the user's command message if it exists and bot has manage_messages
    if command_message:
        try:
            await command_message.delete()
        except discord.Forbidden:
            log.warning("Missing manage_messages permission to delete user command message.")
        except Exception as e:
            log.error(f"Failed to delete command message: {e}")

    # Delete the bot's ephemeral original response (the embed)
    if original_response:
        try:
            await original_response.delete()
        except Exception as e:
            log.error(f"Failed to delete original response: {e}")

    # Delete the followup TV message
    if followup_message:
        try:
            await followup_message.delete()
        except Exception as e:
            log.error(f"Failed to delete followup message: {e}")

# ------------------------------
# Modified /create command
# ------------------------------
@bot.tree.command(name="create", description="Generate a Netflix PC login link from a random cookie file")
async def create(interaction: discord.Interaction):
    # 1. Channel restriction check
    if not is_allowed_channel(interaction):
        if config.allowed_channel_id is None:
            await interaction.response.send_message("⚠️ No channel has been configured. Ask an admin to use `/channel`.", ephemeral=True)
        else:
            allowed_channel = bot.get_channel(config.allowed_channel_id)
            await interaction.response.send_message(f"❌ This command can only be used in {allowed_channel.mention} (if set).", ephemeral=True)
        return

    # 2. Send confirmation buttons (Yes/No) in an ephemeral message
    view = ConfirmView(original_user=interaction.user, original_interaction=interaction)
    await interaction.response.send_message(
        "**Do you want to generate a Netflix login link?**\nThis process will use one random cookie file.",
        view=view,
        ephemeral=True
    )
    # Wait for button interaction
    timeout = await view.wait()
    if timeout or view.value is None:
        return

    if not view.value:  # User clicked No
        return

    # User clicked Yes – proceed
    await interaction.edit_original_response(content="⏳ **Generating your Netflix link…**", view=None)

    # Check cookies folder
    if not COOKIES_FOLDER.exists():
        await interaction.edit_original_response(content="❌ Cookies folder not found. Contact the bot admin.")
        return

    txt_files = list(COOKIES_FOLDER.glob("*.txt"))
    if not txt_files:
        await interaction.edit_original_response(content="❌ No cookie files found in the folder.")
        return

    chosen_file = random.choice(txt_files)
    log.info(f"User {interaction.user} triggered check for file: {chosen_file}")

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(check_cookie_file, str(chosen_file)),
            timeout=SCRIPT_TIMEOUT
        )
    except asyncio.TimeoutError:
        await interaction.edit_original_response(content="⌛ The check took too long. Please try again later.")
        return
    except Exception as e:
        log.error(f"Checker error: {e}")
        await interaction.edit_original_response(content="⚠️ An unexpected error occurred while processing the file.")
        return

    if result:
        # Send the link embed in the same ephemeral response
        embed = discord.Embed(
            title="✅ PC Login Link Ready",
            description=f"Click the link below to log in automatically:\n\n{result}",
            color=discord.Color.green()
        )
        embed.set_footer(text="This link is personal – do not share it.")
        await interaction.edit_original_response(content=None, embed=embed)

        # MODIFIED: TV instruction message matches requirement exactly
        tv_msg = await interaction.followup.send(
            "You can also run the account on the TV through this link netflix.com/tv9 and enter the code for TV.",
            ephemeral=False
        )

        # Store messages for cleanup
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

        # MODIFIED: Cleanup now uses 120 seconds (2 minutes)
        asyncio.create_task(cleanup_messages(
            channel=channel,
            command_message=command_message,
            original_response=original_response,
            followup_message=tv_msg,
            delay_seconds=CLEANUP_DELAY_SECONDS
        ))

        log.info(f"Link sent to {interaction.user} – cleanup scheduled in {CLEANUP_DELAY_SECONDS}s")
    else:
        await interaction.edit_original_response(content="❌ The selected cookie file is invalid or expired.")

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
