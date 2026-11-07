from interactions import slash, subcommand, Interaction, STR, CHANNEL, INT
from lavalink import lavalink
import state
import asyncio
import re
from urllib.parse import urlparse, parse_qs, urlencode

# Lavalink v4 filter presets
FILTER_PRESETS: dict[str, dict] = {
    "bassboost": {
        "equalizer": [
            {"band": 0, "gain": 0.60},
            {"band": 1, "gain": 0.45},
            {"band": 2, "gain": 0.30},
            {"band": 3, "gain": 0.15},
        ],
    },
    "nightcore": {
        "timescale": {"speed": 1.25, "pitch": 1.25, "rate": 1.0},
    },
    "vaporwave": {
        "timescale": {"speed": 0.80, "pitch": 0.80, "rate": 1.0},
    },
    "8d": {
        "rotation": {"rotationHz": 0.2},
    },
    "karaoke": {
        "karaoke": {"level": 1.0, "monoLevel": 1.0,
                    "filterBand": 220.0, "filterWidth": 100.0},
    },
    "tremolo": {
        "tremolo": {"depth": 0.5, "frequency": 5.0},
    },
    "vibrato": {
        "vibrato": {"depth": 0.5, "frequency": 5.0},
    },
}

async def _ensure_joined(i: Interaction) -> bool:
    """Return True if the bot is connected to a voice channel in this guild."""
    if not i.guild_id:
        await i.followup("Voice only works in servers.", ephemeral=True)
        return False
    gid = int(i.guild_id)
    if gid not in lavalink.players:
        await i.followup(
            "❌ I'm not in a voice channel. Use `tct!musicjoin` first.",
            ephemeral=True,
        )
        return False
    return True


_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _parse_youtube_url(query: str) -> dict | None:
    """Parse a YouTube link into {video_id, list_id, is_mix, host}.

    Returns None for non-YouTube input. A link counts as a Mix/Radio when:
      * list= starts with "RD" (RD<id>, RDMM, RDAMVM<id>, RDEM..., RDCLAK...)
      * or start_radio=1 is present (YouTube's "start a radio" flag)
    """
    if not query.startswith(("http://", "https://")):
        return None
    try:
        u = urlparse(query)
    except Exception:
        return None
    host = u.netloc.lower()
    if not any(h in host for h in ("youtube.com", "youtu.be", "youtube-nocookie.com")):
        return None

    q = parse_qs(u.query)
    video_id = (q.get("v") or [None])[0]
    if "youtu.be" in host:
        video_id = u.path.lstrip("/").split("/")[0] or None
    elif not video_id and u.path.startswith(("/shorts/", "/live/", "/embed/")):
        video_id = u.path.split("/")[2] if len(u.path.split("/")) > 2 else None
    if video_id and not _VIDEO_ID_RE.match(video_id):
        video_id = None

    list_id = (q.get("list") or [None])[0]
    is_mix = bool(
        (list_id and list_id.upper().startswith("RD"))
        or (q.get("start_radio") or ["0"])[0] == "1"
    )
    return {"video_id": video_id, "list_id": list_id, "is_mix": is_mix, "host": host}


def _youtube_attempts(query: str) -> tuple[list[str], bool]:
    """Build the Lavalink identifiers to try, in priority order.

    Returns (attempts, is_mix). For a Mix/Radio link we try:
      1. the link as given (minus tracking junk like &pp=, &si=, &index=)
      2. the canonical mix  watch?v=<id>&list=RD<id>  - this rescues malformed
         list IDs (e.g. "RDo<id>"), which make youtube-source fail with
         "Could not read mix page" / "Something went wrong"
      3. the bare video, so the user still hears *something*
    Non-mix input is returned unchanged.
    """
    yt = _parse_youtube_url(query)
    if not yt or not yt["is_mix"]:
        return [query], False

    vid, lst = yt["video_id"], yt["list_id"]
    base = "https://music.youtube.com" if "music.youtube.com" in yt["host"] else "https://www.youtube.com"
    attempts: list[str] = []
    if lst:
        params = {"v": vid, "list": lst} if vid else {"list": lst}
        attempts.append(f"{base}/watch?{urlencode(params)}" if vid else f"{base}/playlist?{urlencode(params)}")
    if vid:
        canonical = f"https://www.youtube.com/watch?v={vid}&list=RD{vid}"
        if canonical not in attempts:
            attempts.append(canonical)
        attempts.append(f"https://www.youtube.com/watch?v={vid}")
    return attempts or [query], True


async def _ensure_voice(gid: int, channel: int) -> bool:
    """Join (or cleanly re-join) a voice channel and wait for Lavalink.

    A 4006 "Session is no longer valid" happens when a previous voice session
    was never torn down on Discord's side: the old Lavalink voice websocket is
    still holding the guild's voice session, so a plain re-join reuses the dead
    session and Discord rejects it. To get a fresh session we must FULLY clean
    up first:
      1. destroy the Lavalink player  -> closes its voice websocket
      2. send Gateway op-4 leave      -> tells Discord to drop the voice session
      3. wait for Discord to process the teardown
      4. send Gateway op-4 join       -> Discord issues a brand-new session+token
    Only then do we wait for the player to re-register.

    Returns True once the Lavalink player is registered."""
    lavalink.begin_rejoin(gid)
    try:
        # 1 + 2: tear everything down so no dangling voice session survives
        try:
            await lavalink.destroy(gid)
        except Exception:
            pass
        state.bot_voice_sessions.pop(gid, None)
        state.pending_voice.pop(gid, None)
        await state.gateway.send_voice_state(gid, None)   # leave voice
        await asyncio.sleep(1.0)                           # let Discord settle
        # 3: join fresh
        await state.gateway.send_voice_state(gid, channel)
        for _ in range(100):
            await asyncio.sleep(0.1)
            if gid in lavalink.players:
                return True
        return False
    finally:
        lavalink.end_rejoin(gid)



@subcommand("music", "Music commands", "join", "Join a voice channel", [
    {"name": "channel", "description": "Voice channel (defaults to yours)",
     "type": CHANNEL, "required": False},
])
async def join(i: Interaction, channel=None):
    await i.defer()
    if not i.guild_id:
        return await i.followup("Voice only works in servers.", ephemeral=True)
    gid = int(i.guild_id)

    # Figure out which channel to join
    target = int(channel) if channel else state.voice_states.get(gid, {}).get(int(i.user_id))
    if not target:
        return await i.followup(
            "❌ You're not in a voice channel. Join one first, or pass the channel.",
            ephemeral=True,
        )

    # Tell Discord to move the bot into that channel via Gateway opcode 4.
    # Leave first, then rejoin, so Discord always issues a fresh voice
    # session/token — re-sending op-4 to a channel the bot is already in is a
    # no-op and never triggers a new VOICE_SERVER_UPDATE.
    state.bot_voice_sessions.pop(gid, None)
    state.pending_voice.pop(gid, None)
    await state.gateway.send_voice_state(gid, None)
    await asyncio.sleep(0.5)
    await state.gateway.send_voice_state(gid, target)
    await i.followup(f"🔊 Joining <#{target}>…")
    # Note: the actual connection finishes when Discord sends us
    # VOICE_STATE_UPDATE + VOICE_SERVER_UPDATE, which main.py forwards to Lavalink.


@subcommand("music", "Music commands", "leave", "Disconnect from voice")
async def leave(i: Interaction):
    await i.defer()
    gid = int(i.guild_id)
    await lavalink.destroy(gid)
    await state.gateway.send_voice_state(gid, None)
    await i.followup("👋 Left the voice channel.")


@subcommand("music", "Music commands", "play", "Play a track", [
    {"name": "query", "description": "URL or search query", "type": STR, "required": True},
])
async def play(i: Interaction, query):
    await i.defer()
    gid = int(i.guild_id)

    # Auto-join voice (keep your existing code)
    if gid not in lavalink.players:
        user_vc = state.voice_states.get(gid, {}).get(int(i.user_id))
        if not user_vc:
            return await i.followup("❌ Join a voice channel first.", ephemeral=True)
        # Clean leave+rejoin so Discord issues a fresh voice session/token even
        # if the bot was already sitting in the channel (fixes "Voice join
        # failed" after a stale session was dropped).
        if not await _ensure_voice(gid, user_vc):
            return await i.followup("❌ Voice join failed.", ephemeral=True)

    # Build the list of identifiers to try, in priority order. YouTube
    # Mix/Radio links (list=RD... or start_radio=1) are kept intact so the
    # whole generated playlist is queued, with fallbacks to the canonical mix
    # and finally the bare video. For a plain text search we try YouTube search
    # first (what users expect from Jockie), then YouTube Music, then
    # SoundCloud as a last resort.
    is_url = query.startswith(("http://", "https://"))
    explicit_prefix = query.startswith(("ytsearch:", "scsearch:", "ytmsearch:", "spsearch:"))
    is_mix = False
    if is_url:
        attempts, is_mix = _youtube_attempts(query)
    elif explicit_prefix:
        attempts = [query]
    else:
        attempts = [f"ytsearch:{query}", f"ytmsearch:{query}", f"scsearch:{query}"]

    # Load — try each identifier until one returns results
    tracks: list[dict] = []
    is_playlist = False
    playlist_name = None
    last_err = None
    for identifier in attempts:
        try:
            tracks, is_playlist, playlist_name = await lavalink.load_tracks(identifier)
        except Exception as e:
            last_err = e
            tracks = []
        if tracks:
            break

    # Radio mode (Jockie-style): when the queue runs dry, keep playing with
    # random related songs from YouTube Mixes of recently played tracks.
    player = lavalink.players.get(gid)
    if player:
        player.text_channel = i.channel_id
        player.rest = i.rest
        if tracks and tracks[0].get("info", {}).get("sourceName") == "youtube":
            player.radio = True

    if not tracks:
        if last_err:
            return await i.followup(f"❌ Couldn't load that: `{last_err}`", ephemeral=True)
        return await i.followup(f"❌ No results for **{query}**.", ephemeral=True)

    # ─── Detect playlist vs single track ───
    # load_tracks reports whether the identifier resolved to a playlist and its
    # name, so we can build the right embed.

    if is_playlist:
        # Playlist embed (like Jockie)
        total_secs = sum((t.get("info", {}).get("length") or 0) for t in tracks) // 1000
        mins, secs = divmod(total_secs, 60)
        hours, mins = divmod(mins, 60)
        if hours:
            length_str = f"{hours}:{mins:02d}:{secs:02d}"
        else:
            length_str = f"{mins}:{secs:02d}"

        embed = {
            "title": "Added Mix 📻 (radio on - queue auto-refills)" if is_mix else "Added Playlist",
            "color": 0x57F287,
            "fields": [
                {"name": "Playlist", "value": f"**{playlist_name or 'Playlist'}**", "inline": False},
                {"name": "Playlist Length", "value": length_str, "inline": True},
                {"name": "Tracks", "value": str(len(tracks)), "inline": True},
            ],
        }
        # Queue all tracks, start the first
        first_started = False
        try:
            for t in tracks:
                started = await lavalink.play(gid, t)
                if started:
                    first_started = True
        except Exception as e:
            return await i.followup(f"❌ Failed to start playback: `{e}`", ephemeral=True)
        await i.followup(embeds=[embed])
    else:
        # Single track embed
        track = tracks[0]
        info = track.get("info", {})
        try:
            started = await lavalink.play(gid, track)
        except Exception as e:
            return await i.followup(f"❌ Failed to start playback: `{e}`", ephemeral=True)
        embed = {
            "title": info.get("title", "Unknown"),
            "url": info.get("uri"),
            "author": {"name": info.get("author", "Unknown")},
            "color": 0x57F287 if started else 0xFEE75C,
            "fields": [
                {"name": "Status", "value": "▶️ Playing" if started else "➕ Queued", "inline": True},
            ],
        }
        if info.get("artworkUrl"):
            embed["thumbnail"] = {"url": info["artworkUrl"]}
        await i.followup(embeds=[embed], components=_music_controls() if started else None)


@subcommand("music", "Music commands", "pause", "Pause/resume")
async def pause(i: Interaction):
    await i.defer()
    if not await _ensure_joined(i):
        return
    p = lavalink.players[int(i.guild_id)]
    await lavalink.pause(int(i.guild_id), not p.paused)
    await i.followup("⏯️ Toggled pause.")


@subcommand("music", "Music commands", "skip", "Skip current track")
async def skip(i: Interaction):
    await i.defer()
    if not await _ensure_joined(i):
        return
    await lavalink.skip(int(i.guild_id))
    await i.followup("⏭️ Skipped.")


@subcommand("music", "Music commands", "stop", "Stop and disconnect")
async def stop(i: Interaction):
    await i.defer()
    gid = int(i.guild_id)
    if gid not in lavalink.players:
        return await i.followup("Not playing.", ephemeral=True)
    await lavalink.destroy(gid)
    await state.gateway.send_voice_state(gid, None)
    await i.followup("⏹️ Stopped.")


def _music_controls():
    return [{
        "type": 1,
        "components": [
            {"type": 2, "style": 1, "label": "Pause/Resume", "emoji": {"name": "⏯️"}, "custom_id": "music:ctl:pause"},
            {"type": 2, "style": 2, "label": "Skip", "emoji": {"name": "⏭️"}, "custom_id": "music:ctl:skip"},
            {"type": 2, "style": 2, "label": "Loop", "emoji": {"name": "🔁"}, "custom_id": "music:ctl:loop"},
            {"type": 2, "style": 2, "label": "Shuffle", "emoji": {"name": "🔀"}, "custom_id": "music:ctl:shuffle"},
            {"type": 2, "style": 4, "label": "Stop", "emoji": {"name": "⏹️"}, "custom_id": "music:ctl:stop"},
        ],
    }]


@subcommand("music", "Music commands", "nowplaying", "Show current track")
async def nowplaying(i: Interaction):
    await i.defer()
    if not await _ensure_joined(i):
        return
    gid = int(i.guild_id)
    p = lavalink.players[gid]
    if not p.current:
        return await i.followup("Nothing playing.", ephemeral=True)
    info = p.current.get("info", {})
    length = _format_ms(info.get("length") or 0)
    embed = {
        "title": f"🎵 {info.get('title', 'Unknown')}",
        "url": info.get("uri"),
        "description": f"**Artist:** {info.get('author', 'Unknown')}\n**Duration:** `{length}`",
        "color": 0x5865F2,
        "footer": {
            "text": (
                f"Volume: {p.volume}% • Loop: {p.loop_mode.capitalize()} • "
                f"Radio: {'On' if p.radio else 'Off'} • 24/7: {'On' if gid in state.music_247_guilds else 'Off'}"
            )
        },
    }
    if info.get("artworkUrl"):
        embed["thumbnail"] = {"url": info["artworkUrl"]}
    await i.followup(embeds=[embed], components=_music_controls())

@subcommand("music", "Music commands", "filter", "Apply an audio filter", [
    {"name": "preset", "description": "Filter preset or 'clear'",
     "type": STR, "required": True,
     "choices": [
         {"name": "Bass Boost", "value": "bassboost"},
         {"name": "Nightcore",  "value": "nightcore"},
         {"name": "Vaporwave",  "value": "vaporwave"},
         {"name": "8D Audio",   "value": "8d"},
         {"name": "Karaoke",    "value": "karaoke"},
         {"name": "Tremolo",    "value": "tremolo"},
         {"name": "Vibrato",    "value": "vibrato"},
         {"name": "Clear",      "value": "clear"},
     ]},
])
async def filter_cmd(i: Interaction, preset):
    await i.defer()
    if not await _ensure_joined(i):
        return
    gid = int(i.guild_id)

    preset = preset.lower()
    if preset == "clear":
        await lavalink.clear_filters(gid)
        return await i.followup("🎚️ Cleared all filters.")

    filters = FILTER_PRESETS.get(preset)
    if not filters:
        return await i.followup(f"❌ Unknown preset `{preset}`.", ephemeral=True)

    # Always clear first, then apply so filters don't stack
    await lavalink.clear_filters(gid)
    await lavalink.set_filters(gid, filters)
    await i.followup(f"🎚️ Applied **{preset}** filter.")


def _format_ms(ms: int) -> str:
    secs = max(0, int(ms) // 1000)
    m, s = divmod(secs, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _parse_time(time_str: str) -> int | None:
    time_str = str(time_str).strip()
    if ":" in time_str:
        parts = time_str.split(":")
        try:
            if len(parts) == 2:
                return (int(parts[0]) * 60 + int(parts[1])) * 1000
            if len(parts) == 3:
                return (int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])) * 1000
        except ValueError:
            return None
    try:
        return int(time_str) * 1000
    except ValueError:
        return None


@subcommand("music", "Music commands", "queue", "Show the music queue")
async def queue_cmd(i: Interaction):
    await i.defer()
    if not await _ensure_joined(i):
        return
    gid = int(i.guild_id)
    p = lavalink.players.get(gid)
    if not p or (not p.current and not p.queue):
        return await i.followup("📭 The queue is completely empty.")

    curr_info = p.current.get("info", {}) if p.current else {}
    curr_title = curr_info.get("title", "Unknown")
    curr_url = curr_info.get("uri")
    curr_len = _format_ms(curr_info.get("length") or 0)
    curr_display = f"[{curr_title}]({curr_url}) (`{curr_len}`)" if curr_url else f"`{curr_title}` (`{curr_len}`)"

    lines = []
    total_ms = (curr_info.get("length") or 0)
    for idx, t in enumerate(p.queue[:10], start=1):
        info = t.get("info", {})
        length = info.get("length") or 0
        total_ms += length
        title = info.get("title", "Unknown")
        uri = info.get("uri")
        t_str = _format_ms(length)
        link = f"[{title[:55]}]({uri})" if uri else title[:55]
        lines.append(f"`{idx}.` {link} — `{t_str}`")

    embed = {
        "title": "🎶 Music Queue",
        "color": 0x5865F2,
        "fields": [
            {"name": "▶️ Now Playing", "value": curr_display, "inline": False},
        ],
    }
    if lines:
        remaining = len(p.queue) - 10
        desc = "\n".join(lines)
        if remaining > 0:
            desc += f"\n*... and {remaining} more track(s)*"
        embed["fields"].append({"name": f"Upcoming ({len(p.queue)} tracks)", "value": desc, "inline": False})
    else:
        embed["fields"].append({"name": "Upcoming", "value": "*No upcoming tracks in queue.*", "inline": False})

    loop_str = p.loop_mode.capitalize()
    radio_str = "Enabled" if p.radio else "Disabled"
    embed["footer"] = {"text": f"Loop: {loop_str} • Radio/Autoplay: {radio_str} • Total Queue Time: {_format_ms(total_ms)}"}
    await i.followup(embeds=[embed])


@subcommand("music", "Music commands", "volume", "Set playback volume", [
    {"name": "volume", "description": "Volume level (0-200)", "type": INT, "required": True},
])
async def volume_cmd(i: Interaction, volume: int):
    await i.defer()
    if not await _ensure_joined(i):
        return
    gid = int(i.guild_id)
    vol = await lavalink.set_volume(gid, int(volume))
    await i.followup(f"🔊 Volume set to **{vol}%**.")


@subcommand("music", "Music commands", "loop", "Set loop mode (off, track, queue)", [
    {"name": "mode", "description": "Loop mode (off, track, queue)", "type": STR, "required": True},
])
async def loop_cmd(i: Interaction, mode: str):
    await i.defer()
    if not await _ensure_joined(i):
        return
    gid = int(i.guild_id)
    new_mode = lavalink.set_loop(gid, mode)
    emoji = "🔂" if new_mode == "track" else ("🔁" if new_mode == "queue" else "➡️")
    await i.followup(f"{emoji} Loop mode set to **{new_mode.upper()}**.")


@subcommand("music", "Music commands", "shuffle", "Shuffle the current queue")
async def shuffle_cmd(i: Interaction):
    await i.defer()
    if not await _ensure_joined(i):
        return
    gid = int(i.guild_id)
    count = lavalink.shuffle(gid)
    if count == 0:
        return await i.followup("❌ Not enough tracks in queue to shuffle.", ephemeral=True)
    await i.followup(f"🔀 Shuffled **{count}** tracks in the queue.")


@subcommand("music", "Music commands", "remove", "Remove a track from queue by position", [
    {"name": "index", "description": "Queue position (1, 2, ...)", "type": INT, "required": True},
])
async def remove_cmd(i: Interaction, index: int):
    await i.defer()
    if not await _ensure_joined(i):
        return
    gid = int(i.guild_id)
    removed = lavalink.remove_track(gid, int(index))
    if not removed:
        return await i.followup(f"❌ Invalid track number `{index}`.", ephemeral=True)
    title = removed.get("info", {}).get("title", "Track")
    await i.followup(f"🗑️ Removed **{title}** from the queue.")


@subcommand("music", "Music commands", "clear", "Clear all queued tracks")
async def clear_cmd(i: Interaction):
    await i.defer()
    if not await _ensure_joined(i):
        return
    gid = int(i.guild_id)
    count = lavalink.clear_queue(gid)
    await i.followup(f"🧹 Cleared **{count}** tracks from the queue.")


@subcommand("music", "Music commands", "seek", "Seek to a timestamp in current track", [
    {"name": "time", "description": "Time in track (e.g. 1:30 or 90)", "type": STR, "required": True},
])
async def seek_cmd(i: Interaction, time: str):
    await i.defer()
    if not await _ensure_joined(i):
        return
    ms = _parse_time(time)
    if ms is None:
        return await i.followup("❌ Invalid time format. Examples: `1:30`, `2:15:00`, `90`.", ephemeral=True)
    gid = int(i.guild_id)
    await lavalink.seek(gid, ms)
    await i.followup(f"⏩ Seeked to `{_format_ms(ms)}`.")


@subcommand("music", "Music commands", "radio", "Toggle autoplay/radio mode (on/off)", [
    {"name": "mode", "description": "on or off (leave blank to toggle)", "type": STR, "required": False},
])
async def radio_cmd(i: Interaction, mode: str | None = None):
    await i.defer()
    if not await _ensure_joined(i):
        return
    gid = int(i.guild_id)
    player = lavalink.players.get(gid)
    if not player:
        return
    if mode is None or not str(mode).strip():
        enabled = not player.radio
    else:
        enabled = str(mode).strip().lower() in ("on", "true", "1", "enable", "yes")
    lavalink.set_radio(gid, enabled)
    status = "ON 📻 (queue will auto-refill)" if enabled else "OFF"
    await i.followup(f"📻 Radio / Autoplay is now **{status}**.")


@subcommand("music", "Music commands", "history", "Show recently played tracks")
async def history_cmd(i: Interaction):
    await i.defer()
    if not await _ensure_joined(i):
        return
    gid = int(i.guild_id)
    player = lavalink.players.get(gid)
    if not player or not player.recent_tracks:
        return await i.followup("📭 No tracks in history yet.")
    lines = []
    for idx, t in enumerate(reversed(list(player.recent_tracks)), start=1):
        info = t.get("info", {})
        title = info.get("title", "Unknown")[:55]
        author = info.get("author", "Unknown")[:30]
        uri = info.get("uri")
        link = f"[{title}]({uri})" if uri else f"**{title}**"
        lines.append(f"`{idx}.` {link} — *{author}*")
    embed = {
        "title": "🕘 Recently Played Tracks",
        "description": "\n".join(lines[:10]),
        "color": 0x5865F2,
    }
    await i.followup(embeds=[embed])


@subcommand("music", "Music commands", "247", "Toggle 24/7 voice stay mode", [
    {"name": "mode", "description": "on or off (leave blank to toggle)", "type": STR, "required": False},
])
async def mode247_cmd(i: Interaction, mode: str | None = None):
    await i.defer()
    gid = int(i.guild_id)
    if mode is None or not str(mode).strip():
        enabled = gid not in state.music_247_guilds
    else:
        enabled = str(mode).strip().lower() in ("on", "true", "1", "enable", "yes")
    if enabled:
        state.music_247_guilds.add(gid)
    else:
        state.music_247_guilds.discard(gid)
    try:
        from database import db
        await db.ensure_guild(gid)
        await db.pool.execute("UPDATE guilds SET music_247=$1 WHERE id=$2", enabled, gid)
    except Exception:
        pass
    await i.followup(f"🌙 24/7 mode is now **{'ENABLED' if enabled else 'DISABLED'}**.")


async def handle_component(i: Interaction):
    cid = i.custom_id or ""
    if not i.guild_id:
        return
    gid = int(i.guild_id)
    player = lavalink.players.get(gid)
    if not player:
        return await i.respond("❌ Nothing is currently playing.", ephemeral=True)

    user_vc = state.voice_states.get(gid, {}).get(int(i.user_id))
    bot_vc = int((state.bot_voice_sessions.get(gid) or {}).get("channel_id") or 0)
    if bot_vc and user_vc != bot_vc:
        return await i.respond("❌ Join my voice channel to use the player controls.", ephemeral=True)

    if cid == "music:ctl:pause":
        new_paused = not player.paused
        await lavalink.pause(gid, new_paused)
        await i.respond(f"⏯️ **{'Paused' if new_paused else 'Resumed'}** by <@{i.user_id}>.")
    elif cid == "music:ctl:skip":
        await lavalink.skip(gid)
        await i.respond(f"⏭️ **Skipped** by <@{i.user_id}>.")
    elif cid == "music:ctl:loop":
        order = {"off": "track", "track": "queue", "queue": "off"}
        next_mode = order.get(player.loop_mode, "off")
        lavalink.set_loop(gid, next_mode)
        await i.respond(f"🔁 Loop mode set to **{next_mode.upper()}** by <@{i.user_id}>.")
    elif cid == "music:ctl:shuffle":
        count = lavalink.shuffle(gid)
        if count == 0:
            return await i.respond("❌ Not enough tracks in queue to shuffle.", ephemeral=True)
        await i.respond(f"🔀 Shuffled **{count}** tracks by <@{i.user_id}>.")
    elif cid == "music:ctl:stop":
        await lavalink.destroy(gid)
        await state.gateway.send_voice_state(gid, None)
        await i.respond(f"⏹️ **Stopped and disconnected** by <@{i.user_id}>.")


