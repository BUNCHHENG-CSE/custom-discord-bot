import asyncio
import logging
import aiohttp
import signal
from pathlib import Path
from collections import OrderedDict

from config import Config
from rest import REST
from gateway import Gateway
from interactions import Interaction, all_specs, resolve_handler
from database import db
from lavalink import lavalink
import modules  # noqa: F401 — importing registers slash commands
import state



logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("main")


def acquire_instance_lock():
    """Keep a second copy of this bot from stealing Discord voice sessions."""
    lock_path = Path(__file__).with_name(".bot-instance.lock")
    handle = lock_path.open("a+b")
    try:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"1")
            handle.flush()
        handle.seek(0)
        if __import__("os").name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        handle.close()
        raise RuntimeError(
            "Another bot instance is already running from this directory."
        ) from None
    return handle


# ==================================================================
# Permission cache
# ==================================================================
class Cache:
    owner_ids = Config.OWNER_IDS

    def __init__(self):
        self.members: dict[tuple[int, int], dict] = {}
        self.roles: dict[tuple[int, int], dict] = {}
        self.guilds: dict[int, dict] = {}

    def member_permissions(self, guild_id: int, user_id) -> str:
        member = self.members.get((guild_id, int(user_id)))
        if not member:
            return "0"
        perms = 0
        # Every guild member implicitly has the @everyone role, whose ID is
        # the guild ID. Discord does not include it in member["roles"].
        for role_id in [guild_id, *member.get("roles", [])]:
            role = self.roles.get((guild_id, int(role_id)))
            if role:
                perms |= int(role.get("permissions", "0"))
        return str(perms)


cache = Cache()


# ==================================================================
# Slash command registration
# ==================================================================
async def register_commands(rest: REST) -> str:
    me = await rest.get("/users/@me")
    app_id = me["id"]
    # Handlers still use the internal registry, but public invocation is now
    # message-prefix based. Publishing an empty list removes the old slash
    # commands from Discord on startup.
    specs = []
    if Config.GUILD_ID:
        await rest.put(f"/applications/{app_id}/guilds/{Config.GUILD_ID}/commands",
                       json=specs)
        log.info("Registered %d commands to guild %s", len(specs), Config.GUILD_ID)
    else:
        await rest.put(f"/applications/{app_id}/commands", json=specs)
        log.info("Registered %d global commands", len(specs))
    return app_id


# ==================================================================
# Voice wiring helpers
# ==================================================================
async def _try_lavalink_voice(gid: int) -> None:
    sess = state.bot_voice_sessions.get(gid)
    srv = state.pending_voice.get(gid)
    log.info("_try_lavalink_voice: gid=%s sess=%s srv=%s",
             gid, bool(sess), bool(srv))
    if not sess or not srv:
        return
    try:
        await lavalink.send_voice_state(
            gid,
            sess["session_id"],
            srv["endpoint"],
            srv["token"],
            sess["channel_id"],       # ← pass the channel ID
        )
        # Tokens are single-use: drop it so a stray/duplicate VOICE_STATE_UPDATE
        # can't re-send the same (now invalid) handshake and trigger 4006.
        state.pending_voice.pop(gid, None)
        log.info("✅ Voice handshake sent to Lavalink for guild %s", gid)
    except Exception as e:
        log.warning("❌ Lavalink voiceUpdate failed: %s", e)


# ==================================================================
# Audit cache & Idle voice helpers
# ==================================================================
_message_cache: OrderedDict[int, dict] = OrderedDict()

async def _auto_disconnect_if_empty(gid: int, channel_id: int):
    await asyncio.sleep(60)
    if gid in state.music_247_guilds:
        return
    bot_sess = state.bot_voice_sessions.get(gid)
    if not bot_sess or int(bot_sess.get("channel_id") or 0) != channel_id:
        return
    vs = state.voice_states.get(gid, {})
    human_listeners = [u for u, c in vs.items() if c == channel_id and str(u) != str(state.bot_user_id)]
    if not human_listeners and gid in lavalink.players:
        player = lavalink.players.get(gid)
        if player and player.text_channel and player.rest:
            try:
                await player.rest.post(f"/channels/{player.text_channel}/messages",
                                       json={"content": "👋 Disconnected from voice: channel remained empty for 60 seconds."})
            except Exception:
                pass
        await lavalink.destroy(gid)
        await state.gateway.send_voice_state(gid, None)


# ==================================================================
# Gateway dispatch
# ==================================================================
async def on_dispatch(event: str, d: dict) -> None:
    try:
        # -------- Cache building --------
        if event == "GUILD_CREATE":
            gid = int(d["id"])
            self_guild = {
                "name": d.get("name", "the server"),
                "owner_id": d.get("owner_id"),
                "icon": d.get("icon"),
                "member_count": int(d.get("member_count", 0)),
                "channels": d.get("channels", []),
            }
            cache.guilds[gid] = self_guild
            for m in d.get("members", []):
                cache.members[(gid, int(m["user"]["id"]))] = m
            for r in d.get("roles", []):
                cache.roles[(gid, int(r["id"]))] = r
            # Seed voice state cache from the initial payload
            vs = state.voice_states.setdefault(gid, {})
            for v in d.get("voice_states", []):
                if v.get("channel_id"):
                    vs[int(v["user_id"])] = int(v["channel_id"])

        elif event in ("GUILD_MEMBER_ADD", "GUILD_MEMBER_UPDATE"):
            cache.members[(int(d["guild_id"]), int(d["user"]["id"]))] = d
            if event == "GUILD_MEMBER_ADD":
                from modules.welcome import handle_member_join
                from modules.roles import assign_autoroles
                try:
                    await assign_autoroles(rest, d)
                except Exception:
                    # A missing role or hierarchy problem must not prevent the
                    # independent welcome-message feature from running.
                    log.exception("Could not assign autorole in guild %s", d["guild_id"])
                await handle_member_join(rest, d, cache)

        elif event == "GUILD_MEMBER_REMOVE":
            gid = int(d["guild_id"])
            cache.members.pop((gid, int(d["user"]["id"])), None)
            from modules.welcome import handle_member_remove
            await handle_member_remove(rest, d, cache)

        elif event in ("GUILD_ROLE_CREATE", "GUILD_ROLE_UPDATE"):
            cache.roles[(int(d["guild_id"]), int(d["role"]["id"]))] = d["role"]

        elif event == "GUILD_ROLE_DELETE":
            guild_id = int(d["guild_id"])
            role_id = int(d["role_id"])
            cache.roles.pop((guild_id, role_id), None)
            await db.pool.execute(
                "DELETE FROM guild_staff_roles WHERE guild_id = $1 AND role_id = $2",
                guild_id, role_id,
            )
            await db.pool.execute(
                "DELETE FROM guild_autoroles WHERE guild_id = $1 AND role_id = $2",
                guild_id, role_id,
            )
            await db.pool.execute(
                "DELETE FROM reaction_roles WHERE guild_id = $1 AND role_id = $2",
                guild_id, role_id,
            )

        elif event in ("MESSAGE_REACTION_ADD", "MESSAGE_REACTION_REMOVE"):
            if str(d.get("user_id")) != str(state.bot_user_id):
                from modules.roles import handle_reaction
                await handle_reaction(rest, d, added=event == "MESSAGE_REACTION_ADD")
                if event == "MESSAGE_REACTION_ADD":
                    from modules.community import handle_starboard_reaction
                    try:
                        await handle_starboard_reaction(rest, d)
                    except Exception:
                        log.exception("Starboard error")

        # -------- Interactions --------
        elif event == "INTERACTION_CREATE":
            await handle_interaction(d)

        # -------- Automod & Audit Cache --------
        elif event == "MESSAGE_CREATE":
            if (d.get("guild_id") and not d.get("webhook_id")
                    and not d["author"].get("bot")):
                mid = int(d["id"])
                _message_cache[mid] = {
                    "content": d.get("content", ""),
                    "author_id": d["author"]["id"],
                    "author_name": d["author"]["username"],
                    "guild_id": int(d["guild_id"]),
                    "channel_id": int(d["channel_id"]),
                }
                if len(_message_cache) > 2000:
                    _message_cache.popitem(last=False)

                from modules.community import handle_afk_message, award_message_xp
                try:
                    await handle_afk_message(rest, d)
                except Exception:
                    pass

                # MESSAGE_CREATE carries the sender's current roles. Cache them
                # so prefix commands use the same permission checks as slash
                # commands, then route any tct! command before automod.
                cache.members[(int(d["guild_id"]), int(d["author"]["id"]))] = {
                    "user": d["author"],
                    "roles": (d.get("member") or {}).get("roles", []),
                }
                from prefix_commands import handle_prefix_message
                handled = await handle_prefix_message(rest, d, cache)
                if handled:
                    return

                try:
                    await award_message_xp(
                        rest, int(d["guild_id"]), int(d["channel_id"]), int(d["author"]["id"])
                    )
                except Exception:
                    pass

                from helpers.permissions import can_use_staff_commands
                from interactions import MessageContext
                ctx = MessageContext(rest, d, cache)
                is_staff_user = await can_use_staff_commands(ctx, db.pool)

                from modules.moderation import automod_message
                await automod_message(
                    int(d["guild_id"]), int(d["channel_id"]),
                    int(d["author"]["id"]), rest,
                    content=d.get("content", ""),
                    message_id=mid,
                    is_staff_user=is_staff_user,
                )

        elif event == "MESSAGE_DELETE":
            mid = int(d.get("id") or 0)
            cached = _message_cache.pop(mid, None)
            if cached and cached.get("content"):
                import time as _time
                state.snipe_cache[int(cached["channel_id"])] = {
                    "content": cached["content"],
                    "author_id": cached["author_id"],
                    "author_name": cached["author_name"],
                    "timestamp": int(_time.time()),
                }
            gid = int(d.get("guild_id") or (cached.get("guild_id") if cached else 0))
            if gid:
                cfg = await db.get_guild(gid)
                log_ch = cfg.get("log_channel") if cfg else None
                if log_ch:
                    cid = d.get("channel_id")
                    if cached and cached.get("content"):
                        author_val = f"<@{cached['author_id']}> ({cached['author_name']})"
                        content_val = cached["content"][:1000]
                    else:
                        author_val = "Unknown (not cached)"
                        content_val = "*Message content not in cache.*"
                    embed = {
                        "title": "🗑️ Message Deleted",
                        "color": 0xED4245,
                        "fields": [
                            {"name": "Channel", "value": f"<#{cid}>", "inline": True},
                            {"name": "Author", "value": author_val, "inline": True},
                            {"name": "Content", "value": content_val, "inline": False},
                        ],
                        "footer": {"text": f"Message ID: {mid}"},
                    }
                    try:
                        await rest.post(f"/channels/{log_ch}/messages", json={"embeds": [embed]})
                    except Exception:
                        pass

        elif event == "MESSAGE_UPDATE":
            mid = int(d.get("id") or 0)
            new_content = d.get("content")
            cached = _message_cache.get(mid)
            if cached and new_content and cached.get("content") != new_content:
                import time as _time
                state.editsnipe_cache[int(cached["channel_id"])] = {
                    "before": cached["content"],
                    "after": new_content,
                    "author_id": cached["author_id"],
                    "author_name": cached["author_name"],
                    "timestamp": int(_time.time()),
                }
                gid = cached.get("guild_id")
                cfg = await db.get_guild(gid)
                log_ch = cfg.get("log_channel") if cfg else None
                if log_ch:
                    embed = {
                        "title": "✏️ Message Edited",
                        "color": 0xFEE75C,
                        "fields": [
                            {"name": "Channel", "value": f"<#{cached['channel_id']}>", "inline": True},
                            {"name": "Author", "value": f"<@{cached['author_id']}>", "inline": True},
                            {"name": "Before", "value": cached["content"][:900] or "—", "inline": False},
                            {"name": "After", "value": new_content[:900] or "—", "inline": False},
                        ],
                        "footer": {"text": f"Message ID: {mid}"},
                    }
                    try:
                        await rest.post(f"/channels/{log_ch}/messages", json={"embeds": [embed]})
                    except Exception:
                        pass
                cached["content"] = new_content

        # -------- Voice --------
        elif event == "VOICE_STATE_UPDATE":
            gid = int(d["guild_id"])
            uid = int(d["user_id"])
            cid = d.get("channel_id")
            log.info("VOICE_STATE_UPDATE: guild=%s user=%s channel=%s (bot=%s)",
                     gid, uid, cid, state.bot_user_id)

            old_cid = state.voice_states.get(gid, {}).get(uid)
            vs = state.voice_states.setdefault(gid, {})
            if cid:
                vs[uid] = int(cid)
            else:
                vs.pop(uid, None)

            # Audit voice joins/leaves/moves
            if old_cid != cid and str(uid) != str(state.bot_user_id):
                cfg = await db.get_guild(gid)
                log_ch = cfg.get("log_channel") if cfg else None
                if log_ch:
                    if not old_cid and cid:
                        action_text = f"📥 <@{uid}> joined voice channel <#{cid}>"
                    elif old_cid and not cid:
                        action_text = f"📤 <@{uid}> left voice channel <#{old_cid}>"
                    else:
                        action_text = f"🔀 <@{uid}> moved from <#{old_cid}> to <#{cid}>"
                    embed = {"description": action_text, "color": 0x3498DB}
                    try:
                        await rest.post(f"/channels/{log_ch}/messages", json={"embeds": [embed]})
                    except Exception:
                        pass

            # Auto-disconnect if voice channel becomes empty
            bot_sess = state.bot_voice_sessions.get(gid)
            if bot_sess and bot_sess.get("channel_id"):
                b_cid = int(bot_sess["channel_id"])
                humans = [u for u, ch_id in vs.items() if ch_id == b_cid and str(u) != str(state.bot_user_id)]
                if not humans and gid in lavalink.players:
                    asyncio.create_task(_auto_disconnect_if_empty(gid, b_cid))


            if str(uid) == state.bot_user_id:
                if cid:
                    # VOICE_STATE_UPDATE and VOICE_SERVER_UPDATE may arrive in
                    # either order. The join command clears both old values
                    # before sending opcode 4, so keep whichever fresh half
                    # arrived first and combine them in _try_lavalink_voice.
                    state.bot_voice_sessions[gid] = {
                        "session_id": d.get("session_id", ""),
                        "channel_id": cid,
                    }
                    log.debug("Bot voice session captured for guild %s (channel %s)",
                              gid, cid)
                    await _try_lavalink_voice(gid)
                else:
                    # Bot left/disconnected — clear cached voice data so the
                    # next join starts from a clean slate.
                    state.bot_voice_sessions.pop(gid, None)
                    state.pending_voice.pop(gid, None)


        elif event == "VOICE_SERVER_UPDATE":
            gid = int(d["guild_id"])
            state.pending_voice[gid] = {
                # Pass Discord's endpoint through exactly as supplied. Some
                # regions include a non-default TLS port (for example :2083).
                "endpoint": d["endpoint"],
                "token": d["token"],
            }
            log.debug("Voice server token captured for guild %s", gid)
            await _try_lavalink_voice(gid)

    except Exception:
        log.exception("Dispatch error for %s", event)


# ==================================================================
# Interaction dispatch
# ==================================================================
async def handle_interaction(d: dict) -> None:
    i = Interaction(rest, d, cache)
    if i.guild_id and i.user_id and d.get("member"):
        cache.members[(int(i.guild_id), int(i.user_id))] = d["member"]

    # PING verification from Discord
    if i.type == 1:
        return await rest.post(
            f"/interactions/{i.id}/{i.token}/callback", json={"type": 1}
        )

    # Slash command
    if i.type == 2:
        full = f"{i.name}:{i.sub}" if i.sub else i.name
        handler = resolve_handler(full)
        if not handler:
            return await i.respond("❌ Command not implemented.", ephemeral=True)
        try:
            await handler(i, **i.opts)
        except Exception as e:
            log.exception("Command error %s", full)
            try:
                if i._responded:
                    await i.followup(f"❌ Error: `{e}`", ephemeral=True)
                else:
                    await i.respond(f"❌ Error: `{e}`", ephemeral=True)
            except Exception:
                pass

    # Component (button / select)
    elif i.type == 3:
        try:
            cid = i.custom_id or ""
            if cid.startswith("ticket:"):
                from modules.tickets import handle_component
                await handle_component(i)
            elif cid.startswith("giveaway:"):
                from modules.giveaways import handle_component
                await handle_component(i)
            elif cid.startswith("welcome:") or cid.startswith("goodbye:"):
                from modules.welcome import handle_component
                await handle_component(i)
            elif cid.startswith("music:"):
                from modules.music import handle_component
                await handle_component(i)
            elif cid.startswith("game:"):
                from modules.games import handle_component
                await handle_component(i)
            elif cid.startswith("community:"):
                from modules.community import handle_component
                await handle_component(i)
            elif cid.startswith("help:"):
                from modules.utility import handle_help_component
                await handle_help_component(i)
        except Exception:
            log.exception("Component error")


# ==================================================================
# Lavalink supervisor (retries until connected)
# ==================================================================
async def lavalink_supervisor() -> None:
    backoff = 5
    while True:
        # Determine if we need to (re)connect
        need_connect = (
            lavalink.ws is None
            or getattr(lavalink.ws, "state", None) is not None
               and lavalink.ws.state.name != "OPEN"
        )
        if need_connect:
            lavalink.ws = None  # force clean state
            try:
                await lavalink.connect()
                log.info("Lavalink connected (session=%s).", lavalink.session_id)
                backoff = 5
            except Exception as e:
                log.warning("Lavalink unavailable (%s). Retrying in %ss…", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue
        await asyncio.sleep(5)

# ==================================================================
# Boot
# ==================================================================
rest = REST(Config.TOKEN)
gateway = Gateway(Config.TOKEN, on_dispatch)

_shutdown = asyncio.Event()


def _handle_signal(*_):
    _shutdown.set()


async def main() -> None:
    await rest.start()
    await db.connect()
    try:
        rows_247 = await db.pool.fetch("SELECT id FROM guilds WHERE music_247 = TRUE")
        for r in rows_247:
            state.music_247_guilds.add(int(r["id"]))
    except Exception:
        pass
    await register_commands(rest)

    me = await rest.get("/users/@me")
    state.bot_user_id = me["id"]
    state.gateway = gateway
    log.info("Bot: %s#%s", me["username"], me.get("discriminator", "0"))

    # Graceful Ctrl+C on Windows
    try:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _handle_signal)
            except NotImplementedError:
                pass  # Windows doesn't support all signals
    except Exception:
        pass

    # Background services
    tasks: list[asyncio.Task] = []
    tasks.append(asyncio.create_task(lavalink_supervisor(), name="lavalink-sup"))

    from modules.moderation import expire_loop
    from modules.giveaways import end_loop
    from modules.alerts import poll_loop
    from modules.community import reminder_loop
    tasks.append(asyncio.create_task(expire_loop(rest), name="mod-expiry"))
    tasks.append(asyncio.create_task(end_loop(rest), name="giveaway-end"))
    tasks.append(asyncio.create_task(reminder_loop(rest), name="reminders"))

    session = aiohttp.ClientSession()
    tasks.append(asyncio.create_task(poll_loop(rest, session), name="feed-poll"))

    log.info("Starting gateway…")
    gw_task = asyncio.create_task(gateway.run(), name="gateway")

    try:
        # Run until gateway dies or Ctrl+C
        await asyncio.wait(
            {gw_task, asyncio.create_task(_shutdown.wait())},
            return_when=asyncio.FIRST_COMPLETED,
        )
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt — shutting down…")
    finally:
        log.info("Shutting down…")
        gateway.running = False
        for t in tasks:
            t.cancel()
        gw_task.cancel()
        await asyncio.gather(*tasks, gw_task, return_exceptions=True)
        await session.close()
        await rest.close()
        await db.close()
        log.info("Shutdown complete.")


if __name__ == "__main__":
    try:
        _instance_lock = acquire_instance_lock()
    except RuntimeError as e:
        log.error("%s", e)
        raise SystemExit(1)
    else:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            pass
