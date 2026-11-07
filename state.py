import time

# Set by main.py after gateway connects
gateway = None              # type: ignore
bot_user_id: str | None = None
start_time: float = time.time()

# guild_id -> { user_id: channel_id }   — every user's current VC
voice_states: dict[int, dict[int, int]] = {}

# guild_id -> { "endpoint": str, "token": str }   — from VOICE_SERVER_UPDATE
pending_voice: dict[int, dict] = {}

# guild_id -> { "session_id": str, "channel_id": str }   — from VOICE_STATE_UPDATE (bot itself)
bot_voice_sessions: dict[int, dict] = {}

# channel_id -> dict with last deleted / edited message for tct!snipe & tct!editsnipe
snipe_cache: dict[int, dict] = {}
editsnipe_cache: dict[int, dict] = {}

# guild_ids with 24/7 voice mode enabled
music_247_guilds: set[int] = set()