import os
from github import Github, GithubException
from base64 import b64decode, b64encode

# --- Add near other environment variables ---
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = "Afrsto/bot"            # your repo name
GITHUB_FILE_PATH = "users.txt"        # path inside the repo

# Global variable to cache file SHA (avoids fetching every time)
_github_file_sha = None

def get_github_repo():
    """Return the GitHub repository object."""
    if not GITHUB_TOKEN:
        log.warning("GITHUB_TOKEN not set – logs will NOT be pushed to GitHub.")
        return None
    g = Github(GITHUB_TOKEN)
    return g.get_repo(GITHUB_REPO)

def update_users_txt_on_github(new_line: str):
    """Append a line to users.txt in the GitHub repo using the API."""
    global _github_file_sha
    repo = get_github_repo()
    if not repo:
        return

    try:
        # 1. Get current file content and its SHA
        contents = repo.get_contents(GITHUB_FILE_PATH)
        current_content = b64decode(contents.content).decode("utf-8")
        _github_file_sha = contents.sha
    except GithubException as e:
        if e.status == 404:
            # File does not exist yet – we'll create it
            current_content = ""
            _github_file_sha = None
        else:
            log.error(f"GitHub API error: {e}")
            return

    # 2. Append new line
    new_content = current_content + new_line

    # 3. Commit the change
    try:
        if _github_file_sha:
            repo.update_file(
                path=GITHUB_FILE_PATH,
                message=f"Add log entry from {new_line.split('|')[2] if '|' in new_line else 'bot'}",
                content=new_content,
                sha=_github_file_sha,
                branch="main"  # or "master"
            )
        else:
            repo.create_file(
                path=GITHUB_FILE_PATH,
                message="Create users.txt with initial log",
                content=new_content,
                branch="main"
            )
        log.info("Successfully updated users.txt on GitHub")
    except GithubException as e:
        log.error(f"Failed to commit to GitHub: {e}")

# -------------------------------------------------
# Replace your current log_user_activity with this version
def log_user_activity(interaction: discord.Interaction, condition: str, result: str) -> None:
    """Append a structured log entry to both local users.txt and GitHub."""
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    user_id  = interaction.user.id
    username = interaction.user.name
    server   = interaction.guild.name if interaction.guild else "DM"
    line = (
        f"[{now}] ID: {user_id} | Username: {username} | "
        f"Server Name: {server} | Date and Time: {now} | "
        f"Status: {condition} | Operation Result: {result}\n"
    )

    # 1. Write to local file (optional, but keeps local copy)
    try:
        with open(USER_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
        log.info(f"Logged activity for {username} ({user_id}) locally")
    except Exception as e:
        log.error(f"Failed to write local log: {e}")

    # 2. Push to GitHub
    update_users_txt_on_github(line)
