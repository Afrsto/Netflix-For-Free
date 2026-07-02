import os
import re
import asyncio
import base64
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime

DISCORD_TOKEN   = os.getenv("DISCORD_TOKEN")
GITHUB_TOKEN    = os.getenv("GITHUB_TOKEN")
NETFLIX_LOG_URL = os.getenv("NETFLIX_LOG_URL", "https://github.com/Afrsto/bot-users/blob/main/Netflix-users.txt")

if not DISCORD_TOKEN:
    raise ValueError("DISCORD_TOKEN is missing! Make sure .env is in the same folder as this script.")

# ── Bot Setup ──
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
log_channels = {}


# ── GitHub Fetch Helper ──
def resolve_github_url(url: str) -> str:
    if "raw.githubusercontent.com" in url:
        return url
    if "api.github.com" in url:
        return url
    if "github.com" in url and "/blob/" in url:
        if GITHUB_TOKEN:
            m = re.match(r'https://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)', url)
            if m:
                owner, repo, branch, path = m.groups()
                return f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"
        return url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
    return url


async def fetch_log() -> str:
    url = resolve_github_url(NETFLIX_LOG_URL)
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise Exception(f"GitHub returned HTTP {resp.status}: {text[:300]}")
            if "api.github.com" in url:
                data = await resp.json()
                return base64.b64decode(data["content"]).decode("utf-8")
            return await resp.text()


# ── Log Parser ──
def parse_logs(content: str) -> list[dict]:
    entries = []
    for line in content.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        entry = _parse_line(line)
        if entry:
            entries.append(entry)
    return entries


def _parse_line(line: str) -> dict | None:
    data = {}
    ts_match = re.match(r'\[([^\]]+)\]', line)
    data["date_of_use"] = ts_match.group(1).replace(" EGY", "").strip() if ts_match else ""

    parts = [p.strip() for p in line.split("|")]
    for part in parts:
        if "👤 User:" in part:
            user_part = part.split("👤 User:")[1].strip()
            if "(Display:" in user_part:
                username, _, display = user_part.partition("(Display:")
                display = display.replace(")", "").strip()
                data["user"] = f"@{username.strip()} ({display})"
            else:
                data["user"] = f"@{user_part}"

        elif "🆔 ID:" in part:
            data["id"] = part.split("🆔 ID:")[1].strip()

        elif "🎁 Plan:" in part:
            data["plan"] = part.split("🎁 Plan:")[1].strip()

        elif "⏸️ Days Left:" in part:
            data["days_left"] = part.split("⏸️ Days Left:")[1].strip()

        elif "Device:" in part:
            data["device"] = part.split("Device:")[1].strip()

        elif "🏠 Server:" in part:
            server_part = part.split("🏠 Server:")[1].strip()
            if "(ID:" in server_part:
                server_part = server_part.rsplit("(ID:", 1)[0].strip()
            data["server"] = server_part

        elif "💬 Channel:" in part:
            data["channel"] = part.split("💬 Channel:")[1].strip()

        elif "🔎 Result:" in part:
            data["result"] = part.split("🔎 Result:")[1].strip()

        elif "🌐 Language:" in part:
            data["language"] = part.split("🌐 Language:")[1].strip()

        elif "📄 Files Used:" in part:
            data["files_used"] = part.split("📄 Files Used:")[1].strip()

        elif "📊 Status:" in part:
            data["status"] = part.split("📊 Status:")[1].strip()

        elif "🎞️ Quality:" in part:
            q_part = part.split("🎞️ Quality:")[1].strip()
            folder = re.search(r'folder:\s*([^)]+)\)', q_part)
            data["plan"] = folder.group(1).strip() if folder else q_part

    for key in ("user", "id", "date_of_use", "plan", "days_left", "device",
                "server", "channel", "result", "language", "files_used", "status"):
        data.setdefault(key, "")

    return data if data["user"] else None


# ── Embed Builder ──
def build_embed(entry: dict) -> discord.Embed:
    embed = discord.Embed(
        title="🎬 User Activity Log",
        color=0xE50914,
        timestamp=datetime.now()
    )
    embed.add_field(name="👤 User",        value=entry["user"]        or "\u200b", inline=True)
    embed.add_field(name="🆔 ID",          value=entry["id"]          or "\u200b", inline=True)
    embed.add_field(name="📌 Date of Use", value=entry["date_of_use"] or "\u200b", inline=True)

    embed.add_field(name="🎁 Plan",       value=entry["plan"]        or "\u200b", inline=True)
    embed.add_field(name="⏸️ Days Left",  value=entry["days_left"]   or "\u200b", inline=True)
    embed.add_field(name="💻 Device",     value=entry["device"]      or "\u200b", inline=True)

    embed.add_field(name="🏠 Server",      value=entry["server"]      or "\u200b", inline=True)
    embed.add_field(name="💬 Channel",    value=entry["channel"]     or "\u200b", inline=True)
    embed.add_field(name="🔎 Result",     value=entry["result"]      or "\u200b", inline=True)

    embed.add_field(name="🌐 Language",   value=entry["language"]    or "\u200b", inline=True)
    embed.add_field(name="📄 Files Used",  value=entry["files_used"]  or "\u200b", inline=True)
    embed.add_field(name="📊 Status",      value=entry["status"]      or "\u200b", inline=True)

    embed.set_footer(text="Netflix Utility • User Activity")
    return embed


# ── Bot Events ──
@bot.event
async def on_ready():
    print(f"✅ Bot logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"🔄 Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"❌ Command sync failed: {e}")


# ── Slash Command ──
@app_commands.checks.has_permissions(administrator=True)
@bot.tree.command(name="channel_log", description="Fetch Netflix logs and send them to a chosen channel")
@app_commands.describe(
    channel="The Discord channel to post the logs in",
    from_user="Start username (e.g. 'laider11'). Leave empty to start from the beginning.",
    from_date="Start date to disambiguate (e.g. '2026-05-03').",
    to_user="End username (e.g. 'x2.214'). Leave empty to go to the end.",
    to_date="End date to disambiguate (e.g. '2026-07-02').",
    limit="How many entries to send when NOT using a range (default 100, max 200)."
)
async def channel_log(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    from_user: str = None,
    from_date: str = None,
    to_user: str = None,
    to_date: str = None,
    limit: int = 100
):
    await interaction.response.defer(ephemeral=True)
    limit = max(1, min(limit, 200))

    try:
        raw_text = await fetch_log()
        entries = parse_logs(raw_text)

        if not entries:
            await interaction.followup.send("⚠️ No valid log entries found.", ephemeral=True)
            return

        start_idx = 0
        end_idx = len(entries) - 1

        # ── Find START index ──
        if from_user:
            found = False
            for i, e in enumerate(entries):
                if from_user.lower() in e["user"].lower():
                    if from_date and from_date not in e["date_of_use"]:
                        continue
                    start_idx = i
                    found = True
                    break
            if not found:
                await interaction.followup.send(
                    f"❌ Start user '{from_user}' (date: {from_date or 'any'}) not found.", ephemeral=True
                )
                return

        # ── Find END index ──
        if to_user:
            found = False
            for i in range(start_idx, len(entries)):
                if to_user.lower() in entries[i]["user"].lower():
                    if to_date and to_date not in entries[i]["date_of_use"]:
                        continue
                    end_idx = i
                    found = True  # keep going to get the LAST match
            if not found:
                await interaction.followup.send(
                    f"❌ End user '{to_user}' (date: {to_date or 'any'}) not found.", ephemeral=True
                )
                return

        # ── Slice entries ──
        selected = entries[start_idx:end_idx + 1]

        # If no range specified, fall back to recent-limit mode
        if not from_user and not to_user:
            selected = entries[-limit:]

        if not selected:
            await interaction.followup.send("⚠️ No entries matched the criteria.", ephemeral=True)
            return

        log_channels[interaction.guild.id] = channel.id

        sent_count = 0
        for entry in selected:
            embed = build_embed(entry)
            await channel.send(embed=embed)
            sent_count += 1
            await asyncio.sleep(1.05)  # rate-limit safety

        range_desc = f"entries {start_idx + 1}–{end_idx + 1}" if (from_user or to_user) else f"most recent {sent_count}"
        await interaction.followup.send(
            f"✅ Sent **{sent_count}** log {range_desc} to {channel.mention}.",
            ephemeral=True
        )

    except Exception as exc:
        await interaction.followup.send(f"❌ Error: `{exc}`", ephemeral=True)


# ── Entry Point ──
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)