import asyncio
import json
import logging
import random
from collections import deque
import aiohttp
import websockets

from config import Config
import state

log = logging.getLogger("lavalink")


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

        # Derive HTTP base URL from the ws URI in .env
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

        if self._http is None:
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
        except Exception as e:
            log.warning("Lavalink WS closed: %s", e)
        finally:
            # Force the supervisor to reconnect on next tick
            self.ws = None
            self.session_id = None
            # Reset players — they belonged to the dead session
            self.players.clear()
            log.info("Lavalink WS cleared; supervisor will reconnect.")

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
            player.current = evt.get("track")
            player.remember(player.current)
            asyncio.create_task(self._announce(player, player.current))
        elif t == "TrackEndEvent":
            # Only advance on a genuine finish or a load failure. Reasons like
            # "replaced" (we already set the new track), "stopped" and "cleanup"
            # must NOT trigger _next — doing so corrupts the queue / skips
            # tracks.
            reason = evt.get("reason")
            if reason in ("finished", "loadFailed"):
                last_finished = player.current
                player.current = None
                if player.loop_mode == "track" and reason == "finished" and last_finished:
                    player.current = last_finished
                    try:
                        await self._rest("PATCH", self._player_path(gid),
                                         json={"track": {"encoded": last_finished["encoded"]}})
                    except Exception:
                        await self._next(gid)
                else:
                    if player.loop_mode == "queue" and reason == "finished" and last_finished:
                        player.queue.append(last_finished)
                    await self._next(gid)
            elif reason == "replaced":
                # The replacement's TrackStartEvent will set current; leave it.
                pass
            else:  # "stopped", "cleanup"
                player.current = None
        elif t in ("TrackExceptionEvent", "TrackStuckEvent"):
            # Track failed to decode/stream — skip it and move on.
            player.current = None
            await self._next(gid)
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
        if player.playing:
            player.queue.append(track)
            return False
        player.current = track
        try:
            await self._rest("PATCH", self._player_path(gid),
                             json={"track": {"encoded": track["encoded"]}})
        except Exception as e:
            # PATCH failed (dead/stale session, voice not connected, etc.).
            # Reset state so the player isn't stuck "playing" a phantom track
            # and every future play() doesn't just queue forever.
            log.warning("play() PATCH failed for guild %s: %s", gid, e)
            player.current = None
            self.players.pop(gid, None)
            raise
        return True

    async def pause(self, gid: int, paused: bool):
        await self._rest("PATCH", self._player_path(gid), json={"paused": paused})
        if gid in self.players:
            self.players[gid].paused = paused

    async def skip(self, gid: int):
        await self._next(gid)

    async def _next(self, gid: int):
        player = self.players.get(gid)
        if not player:
            return
        if not player.queue and player.radio:
            await self._refill_radio(player)
        if player.queue:
            track = player.queue.pop(0)
            player.current = track
            await self._rest("PATCH", self._player_path(gid),
                             json={"track": {"encoded": track["encoded"]}})
        else:
            player.current = None
            try:
                await self._rest("PATCH", self._player_path(gid),
                                 json={"track": {"encoded": None}})
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
        await self._rest("PATCH", self._player_path(gid), json={"position": max(0, position_ms)})

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

