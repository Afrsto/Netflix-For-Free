# bot.py
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
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_TOKEN")
if not DISCORD_BOT_TOKEN:
    raise ValueError("Missing DISCORD_TOKEN environment variable")
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
