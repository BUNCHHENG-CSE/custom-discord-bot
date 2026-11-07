import asyncio
import json
import logging
import random
from typing import Any, Callable, Coroutine

import websockets
from websockets.client import WebSocketClientProtocol

log = logging.getLogger("gateway")

GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json"

# All standard Discord Gateway intents (including privileged intents enabled in developer portal)
# GUILDS | GUILD_MEMBERS | GUILD_MODERATION | GUILD_VOICE_STATES | GUILD_MESSAGES | MESSAGE_CONTENT etc.
INTENTS = 3276799

# Activity Type 4 = Custom Status (renders custom text directly without hardcoded prefixes)
DEFAULT_PRESENCE = {
    "since": None,
    "activities": [
        {
            "name": "Custom Status",
            "type": 4,
            "state": "Helpful command: tct!help",
        }
    ],
    "status": "online",
    "afk": False,
}


class Gateway:
    def __init__(
        self,
        token: str,
        on_dispatch: Callable[[str, dict], Coroutine[Any, Any, None]],
        intents: int = INTENTS,
        initial_presence: dict | None = None,
    ):
        self.token = token
        self.on_dispatch = on_dispatch
        self.intents = intents
        self.presence = dict(initial_presence or DEFAULT_PRESENCE)

        self.ws: WebSocketClientProtocol | None = None
        self.seq: int | None = None
        self.session_id: str | None = None
        self.resume_gateway_url: str | None = None

        self.hb_task: asyncio.Task | None = None
        self.hb_interval: float = 41.25
        self.hb_ack: bool = True
        self.running: bool = False

    async def run(self) -> None:
        """Run the gateway connection loop with automatic reconnection."""
        self.running = True
        while self.running:
            try:
                await self._connect()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning("Gateway error: %s — reconnecting in 5s", e)
                await asyncio.sleep(5)

    async def _connect(self) -> None:
        # Connect to resume url if provided by Discord, else default gateway
        endpoint = self.resume_gateway_url or GATEWAY_URL
        log.info("Connecting to Discord Gateway: %s", endpoint)

        async with websockets.connect(endpoint, max_size=2**25) as ws:
            self.ws = ws

            # 1. Wait for Opcode 10 (Hello)
            hello_raw = await self.ws.recv()
            hello = json.loads(hello_raw)
            if hello.get("op") != 10:
                log.error("Expected Opcode 10 Hello, got: %s", hello)
                return

            self.hb_interval = hello["d"]["heartbeat_interval"] / 1000.0
            self.hb_ack = True

            # Cancel existing heartbeat task and start a fresh one
            if self.hb_task:
                self.hb_task.cancel()
            self.hb_task = asyncio.create_task(self._heartbeat(), name="gateway-hb")

            # 2. Resume (Opcode 6) if previous session exists, else Identify (Opcode 2)
            if self.session_id and self.seq is not None:
                log.info("Resuming session: %s (seq: %s)", self.session_id, self.seq)
                await self._resume()
            else:
                log.info("Identifying with presence: %s", self.presence)
                await self._identify()

            # 3. Enter event loop
            await self._listen()

    async def _identify(self) -> None:
        """Send Opcode 2 (Identify) with bot token, intents, client properties, and presence."""
        payload = {
            "op": 2,
            "d": {
                "token": self.token,
                "intents": self.intents,
                "properties": {
                    "os": "linux",
                    "browser": "tct-bot",
                    "device": "tct-bot",
                },
                "presence": self.presence,
            },
        }
        await self._send(payload)

    async def _resume(self) -> None:
        """Send Opcode 6 (Resume) to recover missed events."""
        payload = {
            "op": 6,
            "d": {
                "token": self.token,
                "session_id": self.session_id,
                "seq": self.seq,
            },
        }
        await self._send(payload)

    async def _listen(self) -> None:
        """Listen and process incoming WebSocket messages."""
        if not self.ws:
            return

        async for raw in self.ws:
            msg = json.loads(raw)
            op = msg.get("op")

            if op == 0:  # Dispatch Event
                self.seq = msg.get("s", self.seq)
                evt = msg.get("t")

                if evt == "READY":
                    data = msg["d"]
                    self.session_id = data["session_id"]
                    self.resume_gateway_url = f"{data.get('resume_gateway_url', GATEWAY_URL)}?v=10&encoding=json"
                    username = data.get("user", {}).get("username", "Unknown")
                    log.info("Ready as %s (session_id: %s)", username, self.session_id)
                elif evt == "RESUMED":
                    log.info("Session resumed successfully.")

                asyncio.create_task(self.on_dispatch(evt, msg.get("d", {})))

            elif op == 1:  # Heartbeat request from Discord
                await self._send({"op": 1, "d": self.seq})

            elif op == 7:  # Reconnect requested by Discord
                log.info("Gateway requested reconnect (Opcode 7).")
                await self.ws.close()
                return

            elif op == 9:  # Invalid Session
                can_resume = msg.get("d", False)
                log.warning("Invalid session (Opcode 9). Resumable: %s", can_resume)
                if not can_resume:
                    self.session_id = None
                    self.seq = None
                    self.resume_gateway_url = None
                await asyncio.sleep(2)
                await self.ws.close()
                return

            elif op == 11:  # Heartbeat ACK
                self.hb_ack = True

    async def _heartbeat(self) -> None:
        """Send periodic heartbeats according to Discord Gateway specification."""
        try:
            # Discord spec: first heartbeat after jitter * interval
            jitter = random.random()
            await asyncio.sleep(self.hb_interval * jitter)
            await self._send({"op": 1, "d": self.seq})

            while True:
                await asyncio.sleep(self.hb_interval)
                if not self.hb_ack:
                    log.warning("Heartbeat ACK missed (zombie connection) — closing to reconnect.")
                    if self.ws:
                        await self.ws.close(4000, "Heartbeat ACK missed")
                    return
                self.hb_ack = False
                await self._send({"op": 1, "d": self.seq})
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.debug("Heartbeat error: %s", e)

    async def _send(self, payload: dict) -> None:
        """Serialize payload to JSON and send over WebSocket."""
        if not self.ws:
            return
        try:
            data = json.dumps(payload)
            await self.ws.send(data)
        except websockets.ConnectionClosed:
            pass
        except Exception as e:
            log.warning("Failed to send WebSocket payload: %s", e)

    async def update_presence(
        self,
        text: str = "Helpful command: tct!help",
        activity_type: int = 4,
        status: str = "online",
        afk: bool = False,
    ) -> None:
        """
        Send Gateway Opcode 3 (Presence Update) to dynamically update the bot's status.

        Activity Types:
            0 = Playing <text>
            1 = Streaming <text>
            2 = Listening to <text>
            3 = Watching <text>
            4 = Custom Status (<text> without prefix)
            5 = Competing in <text>
        """
        if text:
            if activity_type == 4:
                activity = {
                    "name": "Custom Status",
                    "type": 4,
                    "state": text,
                }
            else:
                activity = {
                    "name": text,
                    "type": activity_type,
                }
            activities = [activity]
        else:
            activities = []

        payload = {
            "op": 3,
            "d": {
                "since": None,
                "activities": activities,
                "status": status,
                "afk": afk,
            },
        }
        # Cache current presence so it persists across reconnection/resumes
        self.presence = payload["d"]
        log.info("Updating presence: %s (type %d, status %s)", text, activity_type, status)
        await self._send(payload)

    async def send_voice_state(
        self,
        guild_id: int,
        channel_id: int | None,
        self_mute: bool = False,
        self_deaf: bool = False,
    ) -> None:
        """Send Gateway opcode 4 to make the bot join/leave/move a voice channel."""
        await self._send({
            "op": 4,
            "d": {
                "guild_id": str(guild_id),
                "channel_id": str(channel_id) if channel_id else None,
                "self_mute": self_mute,
                "self_deaf": self_deaf,
            },
        })

    async def close(self) -> None:
        """Close the WebSocket and cancel the heartbeat task."""
        self.running = False
        if self.hb_task:
            self.hb_task.cancel()
        if self.ws:
            await self.ws.close()
