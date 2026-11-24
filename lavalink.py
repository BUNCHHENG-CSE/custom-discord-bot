import asyncio
import json
import logging
import random
import time
from collections import deque
import aiohttp
import websockets

from config import Config
import state

log = logging.getLogger("lavalink")


def _same_track(a: dict | None, b: dict | None) -> bool:
    """Return True if two Lavalink track payloads refer to the same track."""
    if not a or not b:
        return False
    if a.get("encoded") and b.get("encoded"):
        return a["encoded"] == b["encoded"]
    ia = (a.get("info") or {}).get("identifier")
    ib = (b.get("info") or {}).get("identifier")
    return bool(ia and ib and ia == ib)


class Player:
    def __init__(self, guild_id, node):
        self.guild_id = guild_id
        self.node = node
        self.current = None
        self.queue: list[dict] = []
        self.paused = False
        self.volume = 100
        self.loop_mode: str = "off"  # "off", "track", "queue"
        self.filters: dict = {}
        # Playback position tracking & mid-song recovery
        self.position: int = 0               # last reported position in ms
        self.last_update: float = 0.0        # monotonic timestamp of last update
        self._retry_count: int = 0           # consecutive recovery attempts for current track
        self._last_retry_pos: int = 0        # position (ms) where last retry happened
        self._resuming: bool = False         # True while transparently resuming a cut-off track
        self._lock = asyncio.Lock()          # serializes track transitions per guild
        # Radio / autoplay (Jockie-style YouTube Mix continuation)
        self.radio = False
        self.last_track: dict | None = None          # seed for the next mix
        self.history: deque[str] = deque(maxlen=200)  # recent identifiers, avoids repeats
        self.recent_tracks: deque[dict] = deque(maxlen=10)  # random refill seeds
        self._refilling = False
        # "Started playing ..." announcements (Jockie-style)
        self.text_channel: int | None = None
        self.rest = None

    @property
    def playing(self):
        return self.current is not None

    def reset_progress(self) -> None:
        self.position = 0
        self.last_update = time.monotonic()
        self._retry_count = 0
        self._last_retry_pos = 0
        self._resuming = False

    def estimated_position(self) -> int:
        if not self.current:
            return 0
        if self.paused or self.last_update <= 0:
            return self.position
        elapsed_ms = int((time.monotonic() - self.last_update) * 1000)
        speed = (self.filters.get("timescale") or {}).get("speed", 1.0)
        pos = self.position + int(elapsed_ms * speed)
        length = (self.current.get("info") or {}).get("length") or 0
        if length > 0:
            return min(pos, length)
        return pos

    def remember(self, track: dict | None) -> None:
        if not track:
            return
        self.last_track = track
        self.recent_tracks.append(track)
        ident = track.get("info", {}).get("identifier")
        if ident and ident not in self.history:
            self.history.append(ident)


class Lavalink:
    def __init__(self):
        self.ws = None
        self.session_id: str | None = None
        self.players: dict[int, Player] = {}
        self._http: aiohttp.ClientSession | None = None
        self._voice_errors: dict[int, str] = {}
        # Guilds currently undergoing a deliberate leave+rejoin. While a guild is
        # in here, a WebSocketClosedEvent (from the leave) must NOT tear down the
        # player — otherwise a late close event would nuke the fresh rejoin.
        self._rejoining: set[int] = set()

        # Derive HTTP base URL from LAVALINK_URI / LAVALINK_HOST+PORT+SSL in .env
        base = Config.LAVALINK_URI.rstrip("/")
        if base.endswith("/v4/websocket"):
            base = base[: -len("/v4/websocket")]
        self._base = base.replace("wss://", "https://", 1).replace("ws://", "http://", 1)

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------
    async def connect(self):
        ws_url = self._base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        ws_url = ws_url.rstrip("/") + "/v4/websocket"

        headers = {
            "Authorization": Config.LAVALINK_PASSWORD,
            "User-Id": state.bot_user_id or "1",
            "Client-Name": "RawBot/1.0",
        }
        self.ws = await websockets.connect(ws_url, additional_headers=headers, max_size=2**25)

        ready = json.loads(await self.ws.recv())
        if ready.get("op") != "ready":
            raise RuntimeError(f"Lavalink handshake failed: {ready}")
        self.session_id = ready["sessionId"]
        log.info("Lavalink ready, session=%s", self.session_id)

        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession(
                headers={
                    "Authorization": Config.LAVALINK_PASSWORD,
                    "Content-Type": "application/json",
                }
            )

        asyncio.create_task(self._listen())

    async def _listen(self):
        try:
            async for raw in self.ws:
                evt = json.loads(raw)
                op = evt.get("op")
                if op == "event":
                    await self._handle_event(evt)
                elif op == "playerUpdate":
                    self._handle_player_update(evt)
        except Exception as e:
            log.warning("Lavalink WS closed: %s", e)
        finally:
            # Force the supervisor to reconnect on next tick
            self.ws = None
            self.session_id = None
            # Reset players — they belonged to the dead session
            self.players.clear()
            log.info("Lavalink WS cleared; supervisor will reconnect.")

    def _handle_player_update(self, evt: dict) -> None:
        gid = int(evt.get("guildId", 0))
        player = self.players.get(gid)
        if not player or not player.current:
            return
        st = evt.get("state") or {}
        pos = int(st.get("position") or 0)
        # Keep the last non-zero position if Lavalink reports 0 right as a
        # track ends before TrackEndEvent arrives.
        if pos > 0 or player.position == 0:
            player.position = pos
            player.last_update = time.monotonic()
            if player._retry_count > 0 and pos > player._last_retry_pos + 15000:
                player._retry_count = 0

    async def _try_recover_track(self, gid: int, player: Player,
                                 track: dict | None, reason: str) -> bool:
        """Attempt to resume or fallback when a song ends/fails prematurely.

        Returns True if a recovery PATCH was sent (caller should NOT advance
        to the next song), or False if the song finished normally / exhausted
        its retry budget.
        """
        if not track:
            return False
        info = track.get("info") or {}
        if info.get("isStream"):
            return False

        length = int(info.get("length") or 0)
        pos = player.position

        if reason == "finished":
            # Normal completion unless the stream closed well before the end
            # of the track (e.g. YouTube CDN premature EOF or 400ms chunk gap
            # in YoutubeMpegStreamAudioTrack).
            if length <= 0 or pos < 3000 or (length - pos) <= 15000:
                return False
        elif reason in ("loadFailed", "stuck"):
            # If it failed within the last 5 seconds of the song, treat as done.
            if length > 0 and pos > 0 and (length - pos) <= 5000:
                return False
        else:
            return False

        if player._retry_count >= 3:
            log.warning("Giving up on track '%s' in guild %s after %d retries (pos=%dms/%dms)",
                        info.get("title", "Unknown"), gid, player._retry_count, pos, length)
            return False

        player._retry_count += 1
        resume_pos = max(0, pos - 500) if pos >= 3000 else 0
        target_track = track

        # If the track failed right at the start (0:00) and already failed its
        # first retry on the same encoded track, try finding an alternate
        # stream on SoundCloud / YouTube Music before skipping the song.
        if pos < 3000 and player._retry_count >= 2:
            if player._retry_count > 2:
                return False
            title = info.get("title") or ""
            author = info.get("author") or ""
            query = f"{title} {author}".strip()
            if not query:
                return False
            alt_track = None
            for prefix in ("scsearch:", "ytmsearch:"):
                try:
                    alts, _, _ = await self.load_tracks(f"{prefix}{query}")
                except Exception:
                    alts = []
                for cand in alts:
                    c_info = cand.get("info") or {}
                    if c_info.get("identifier") and c_info.get("identifier") != info.get("identifier"):
                        alt_track = cand
                        break
                if alt_track:
                    break
            if not alt_track:
                return False
            target_track = alt_track
            resume_pos = 0

        player._last_retry_pos = resume_pos
        player._resuming = True
        player.current = target_track
        player.position = resume_pos
        player.last_update = time.monotonic()

        log.warning("Recovering track '%s' in guild %s (reason=%s, pos=%dms/%dms, attempt=%d/3)",
                    info.get("title", "Unknown"), gid, reason, pos, length, player._retry_count)
        try:
            payload: dict = {"track": {"encoded": target_track["encoded"]}, "paused": False}
            if resume_pos > 0:
                payload["position"] = resume_pos
            await self._rest("PATCH", self._player_path(gid), json=payload)
            player.paused = False
            return True
        except Exception as e:
            log.warning("Track recovery PATCH failed for guild %s: %s", gid, e)
            player._resuming = False
            return False

    async def _handle_event(self, evt):
        gid = int(evt["guildId"])
        t = evt["type"]
        if t == "WebSocketClosedEvent":
            detail = f"code={evt.get('code')} reason={evt.get('reason')}"
            self._voice_errors[gid] = detail
            log.warning("Discord voice websocket closed for guild %s (%s)", gid, detail)

        player = self.players.get(gid)
        if not player:
            return
        if t == "TrackStartEvent":
            started = evt.get("track")
            was_resuming = player._resuming
            player._resuming = False
            player.current = started
            player.last_update = time.monotonic()
            if was_resuming:
                log.info("Resumed track in guild %s at ~%dms (retry %d)",
                         gid, player.position, player._retry_count)
            else:
                player.position = 0
                player._retry_count = 0
                player._last_retry_pos = 0
                player.remember(player.current)
                asyncio.create_task(self._announce(player, player.current))
        elif t == "TrackEndEvent":
            # Only advance on a genuine finish or a load failure. Reasons like
            # "replaced" (we already set the new track), "stopped" and "cleanup"
            # must NOT trigger _next — doing so corrupts the queue / skips
            # tracks.
            reason = evt.get("reason")
            if reason in ("finished", "loadFailed"):
                async with player._lock:
                    ended_track = evt.get("track")
                    if not player.current or not _same_track(ended_track, player.current):
                        log.debug("Ignoring stale TrackEndEvent (%s) for guild %s", reason, gid)
                        return

                    # If the stream dropped mid-song or had a transient load
                    # failure, transparently resume from the last position.
                    if await self._try_recover_track(gid, player, player.current, reason):
                        return

                    last_finished = player.current
                    player.current = None
                    player.reset_progress()
                    if player.loop_mode == "track" and reason == "finished" and last_finished:
                        player.current = last_finished
                        player.paused = False
                        try:
                            await self._rest("PATCH", self._player_path(gid),
                                             json={"track": {"encoded": last_finished["encoded"]}, "paused": False})
                        except Exception:
                            await self._next_unlocked(gid)
                    else:
                        if player.loop_mode == "queue" and reason == "finished" and last_finished:
                            player.queue.append(last_finished)
                        await self._next_unlocked(gid)
            elif reason == "replaced":
                # The replacement's TrackStartEvent will set current; leave it.
                pass
            else:  # "stopped", "cleanup"
                if not player._resuming:
                    player.current = None
                    player.reset_progress()
        elif t == "TrackExceptionEvent":
            # Lavalink ALWAYS emits TrackEndEvent(reason="loadFailed") right
            # after TrackExceptionEvent. Advancing here as well caused a
            # double-skip that popped two tracks from the queue for one failure.
            exc = evt.get("exception") or {}
            log.warning("TrackExceptionEvent in guild %s at %dms: %s (%s)",
                        gid, player.position, exc.get("message"), exc.get("severity"))
        elif t == "TrackStuckEvent":
            async with player._lock:
                if not player.current or not _same_track(evt.get("track"), player.current):
                    return
                log.warning("TrackStuckEvent in guild %s at %dms (threshold=%sms)",
                            gid, player.position, evt.get("thresholdMs"))
                if await self._try_recover_track(gid, player, player.current, "stuck"):
                    return
                player.current = None
                player.reset_progress()
                await self._next_unlocked(gid)
        elif t == "WebSocketClosedEvent":
            # Discord closed the voice session (e.g. code 4006 "Session is no
            # longer valid"). The player is now a zombie: reset current, clear
            # the queue, and drop the player so the next tct!musicplay re-joins
            # cleanly instead of "playing" into a dead socket with no sound.
            # Ignore closes that belong to a deliberate leave+rejoin we started.
            if gid in self._rejoining:
                log.info("Ignoring WS close for guild %s (rejoin in progress)", gid)
                return
            player.current = None
            player.queue.clear()
            self.players.pop(gid, None)

    # ------------------------------------------------------------------
    # REST helper
    # ------------------------------------------------------------------
    async def _rest(self, method: str, path: str, **kwargs):
        if self._http is None:
            raise RuntimeError("Lavalink HTTP session not ready")
        url = f"{self._base}{path}"
        async with self._http.request(method, url, **kwargs) as resp:
            body = await resp.text()
            if resp.status >= 400:
                raise RuntimeError(f"Lavalink {method} {path} -> {resp.status}: {body[:300]}")
            if resp.status == 204 or not body:
                return None
            return json.loads(body)

    def _player_path(self, gid: int) -> str:
        return f"/v4/sessions/{self.session_id}/players/{gid}"

    # ------------------------------------------------------------------
    # Public API (all commands go over REST in v4)
    # ------------------------------------------------------------------
    async def load_tracks(self, identifier: str) -> tuple[list[dict], bool, str | None]:
        """Return (tracks, is_playlist, playlist_name).

        Computed per-call so concurrent tct!musicplay calls in different guilds
        can't clobber each other's playlist metadata (the old shared instance
        attributes raced and produced wrong "Playing/Queued" embeds)."""
        data = await self._rest("GET", "/v4/loadtracks", params={"identifier": identifier})
        lt = data.get("loadType")

        if lt == "playlist":
            name = data["data"].get("info", {}).get("name")
            return data["data"]["tracks"], True, name
        if lt == "search":
            return data["data"], False, None
        if lt == "track":
            return [data["data"]], False, None
        return [], False, None

    async def send_voice_state(self, gid: int, session_id: str,
                               endpoint: str, token: str, channel_id: int):
        self._voice_errors.pop(gid, None)
        body = {
            "voice": {
                "token": token,
                "endpoint": endpoint,
                "sessionId": session_id,
                "channelId": str(channel_id),  # ← Lavalink v4 requires this
            }
        }
        await self._rest("PATCH", self._player_path(gid), json=body)

        # A successful PATCH only means Lavalink accepted the voice data. The
        # Discord voice websocket can still reject it asynchronously (notably
        # with close code 4006). Do not advertise the player as usable until
        # Lavalink confirms that the media connection is actually open.
        for _ in range(60):
            voice_error = self._voice_errors.pop(gid, None)
            if voice_error:
                raise RuntimeError(f"Discord rejected the voice connection ({voice_error})")
            player_data = await self._rest("GET", self._player_path(gid))
            if player_data.get("state", {}).get("connected"):
                break
            await asyncio.sleep(0.25)
        else:
            try:
                await self._rest("DELETE", self._player_path(gid))
            except Exception:
                pass
            raise RuntimeError("Discord voice connection did not become ready")

        if gid not in self.players:
            self.players[gid] = Player(gid, self)
            log.info("Player registered for guild %s", gid)

    async def play(self, gid: int, track: dict):
        player = self.players.setdefault(gid, Player(gid, self))
        async with player._lock:
            if player.playing:
                player.queue.append(track)
                return False
            player.current = track
            player.paused = False
            player.reset_progress()
            try:
                await self._rest("PATCH", self._player_path(gid),
                                 json={"track": {"encoded": track["encoded"]}, "paused": False})
            except Exception as e:
                # PATCH failed (dead/stale session, voice not connected, etc.).
                # Reset state so the player isn't stuck "playing" a phantom track
                # and every future play() doesn't just queue forever.
                log.warning("play() PATCH failed for guild %s: %s", gid, e)
                player.current = None
                self.players.pop(gid, None)
                raise
            return True

    async def play_tracks(self, gid: int, tracks: list[dict]) -> bool:
        """Enqueue a batch of tracks atomically and start the first if idle."""
        if not tracks:
            return False
        player = self.players.setdefault(gid, Player(gid, self))
        async with player._lock:
            if player.playing:
                player.queue.extend(tracks)
                return False
            first = tracks[0]
            player.current = first
            player.paused = False
            player.reset_progress()
            player.queue.extend(tracks[1:])
            try:
                await self._rest("PATCH", self._player_path(gid),
                                 json={"track": {"encoded": first["encoded"]}, "paused": False})
            except Exception as e:
                log.warning("play_tracks() PATCH failed for guild %s: %s", gid, e)
                player.current = None
                player.queue.clear()
                self.players.pop(gid, None)
                raise
            return True

    async def pause(self, gid: int, paused: bool):
        await self._rest("PATCH", self._player_path(gid), json={"paused": paused})
        if gid in self.players:
            self.players[gid].paused = paused

    async def skip(self, gid: int):
        player = self.players.get(gid)
        if not player:
            return
        async with player._lock:
            player.reset_progress()
            await self._next_unlocked(gid)

    async def _next(self, gid: int):
        player = self.players.get(gid)
        if not player:
            return
        async with player._lock:
            await self._next_unlocked(gid)

    async def _next_unlocked(self, gid: int):
        player = self.players.get(gid)
        if not player:
            return
        player.reset_progress()
        player.paused = False
        if not player.queue and player.radio:
            await self._refill_radio(player)
        while player.queue:
            track = player.queue.pop(0)
            player.current = track
            try:
                await self._rest("PATCH", self._player_path(gid),
                                 json={"track": {"encoded": track["encoded"]}, "paused": False})
                return
            except Exception as e:
                log.warning("Failed to start next track in guild %s: %s", gid, e)
                player.current = None
        player.current = None
        try:
            await self._rest("PATCH", self._player_path(gid),
                             json={"track": {"encoded": None}, "paused": False})
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Radio / autoplay
    # ------------------------------------------------------------------
    def set_radio(self, gid: int, enabled: bool) -> None:
        player = self.players.get(gid)
        if player:
            player.radio = enabled

    async def _refill_radio(self, player: Player) -> None:
        """Queue a fresh YouTube Mix seeded from the last played track.

        YouTube's "RD<videoId>" list is the auto-generated radio for that
        video, so chaining it from whatever played last gives endless,
        drifting radio just like Jockie / YouTube autoplay."""
        if player._refilling:
            return
        # Random seed from recently played YouTube tracks, so the radio drifts
        # to new songs instead of looping the same mix.
        seeds = [s for s in player.recent_tracks
                 if s.get("info", {}).get("sourceName") == "youtube"]
        if not seeds and player.last_track:
            seeds = [player.last_track]
        if not seeds:
            return
        player._refilling = True
        try:
            for seed in random.sample(seeds, k=min(3, len(seeds))):
                vid = seed["info"]["identifier"]
                tracks, _, _ = await self.load_tracks(
                    f"https://www.youtube.com/watch?v={vid}&list=RD{vid}")
                fresh = [t for t in tracks
                         if t.get("info", {}).get("identifier") not in player.history]
                if fresh:
                    random.shuffle(fresh)
                    player.queue.extend(fresh)
                    log.info("Radio refill for guild %s: +%d shuffled tracks (seed %s)",
                             player.guild_id, len(fresh), vid)
                    break
        except Exception as e:
            log.warning("Radio refill failed for guild %s: %s", player.guild_id, e)
        finally:
            player._refilling = False

    async def _announce(self, player: Player, track: dict | None) -> None:
        """Post a Jockie-style 'Started playing <title> by <author>' embed."""
        if not track or not player.rest or not player.text_channel:
            return
        info = track.get("info", {})
        title = info.get("title", "Unknown")
        author = info.get("author", "Unknown")
        uri = info.get("uri")
        text = f"[{title} by {author}]({uri})" if uri else f"**{title} by {author}**"
        embed = {"description": f"🎵 Started playing {text}", "color": 0x2B2D31}
        try:
            await player.rest.post(f"/channels/{player.text_channel}/messages",
                                   json={"embeds": [embed]})
        except Exception as e:
            log.warning("Now-playing announce failed for guild %s: %s", player.guild_id, e)

    async def stop(self, gid: int):
        player = self.players.get(gid)
        if player:
            player.queue.clear()
            player.current = None
            player.radio = False
            player.reset_progress()
        try:
            await self._rest("PATCH", self._player_path(gid),
                             json={"track": {"encoded": None}})
        except Exception:
            pass

    async def destroy(self, gid: int):
        try:
            await self._rest("DELETE", self._player_path(gid))
        except Exception:
            pass
        self.players.pop(gid, None)

    # ------------------------------------------------------------------
    # Rejoin guard (used by a deliberate leave+rejoin so we get a fresh
    # Discord voice session/token when the bot is already in the channel)
    # ------------------------------------------------------------------
    def begin_rejoin(self, gid: int) -> None:
        self._rejoining.add(gid)

    def end_rejoin(self, gid: int) -> None:
        self._rejoining.discard(gid)

    async def set_filters(self, gid: int, filters: dict):
        await self._rest("PATCH", self._player_path(gid), json={"filters": filters})
        if gid in self.players:
            self.players[gid].filters = filters

    async def clear_filters(self, gid: int):
        await self.set_filters(gid, {})

    async def set_volume(self, gid: int, volume: int):
        volume = max(0, min(volume, 200))
        await self._rest("PATCH", self._player_path(gid), json={"volume": volume})
        if gid in self.players:
            self.players[gid].volume = volume
        return volume

    async def seek(self, gid: int, position_ms: int):
        pos = max(0, position_ms)
        await self._rest("PATCH", self._player_path(gid), json={"position": pos})
        player = self.players.get(gid)
        if player:
            player.position = pos
            player.last_update = time.monotonic()

    def set_loop(self, gid: int, mode: str) -> str:
        player = self.players.get(gid)
        if not player:
            return "off"
        mode = mode.lower()
        if mode in ("track", "song", "single", "1"):
            player.loop_mode = "track"
        elif mode in ("queue", "all"):
            player.loop_mode = "queue"
        else:
            player.loop_mode = "off"
        return player.loop_mode

    def shuffle(self, gid: int) -> int:
        player = self.players.get(gid)
        if not player or not player.queue:
            return 0
        random.shuffle(player.queue)
        return len(player.queue)

    def remove_track(self, gid: int, index: int) -> dict | None:
        player = self.players.get(gid)
        if not player or not player.queue:
            return None
        if 1 <= index <= len(player.queue):
            return player.queue.pop(index - 1)
        return None

    def clear_queue(self, gid: int) -> int:
        player = self.players.get(gid)
        if not player:
            return 0
        count = len(player.queue)
        player.queue.clear()
        return count


lavalink = Lavalink()
