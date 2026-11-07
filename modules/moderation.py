import asyncio, time, re
from datetime import datetime, timezone
from interactions import slash, Interaction, USER, STR, INT
from database import db
from config import Config
from helpers.permissions import can_use_staff_commands
import state

_durations = {"s":1,"m":60,"h":3600,"d":86400,"w":604800}

async def _require_staff(i: Interaction) -> bool:
    """Guard moderation handlers even when called outside the prefix router."""
    if await can_use_staff_commands(i, db.pool):
        return True
    await i.respond(
        "❌ This command is limited to the server owner, administrators, "
        "and configured moderator roles.",
        ephemeral=True,
    )
    return False

def parse_duration(s: str):
    total, num, unit = 0, "", ""
    for c in s.lower():
        if c.isdigit(): num += c
        elif c in _durations and num:
            total += int(num) * _durations[c]; num = ""
        elif not c.isspace(): return None
    return total or None

async def _log(guild_id, rest, action, target, moderator, reason, case_id, duration=None):
    cfg = await db.get_guild(guild_id)
    if not cfg or not cfg["log_channel"]: return
    embed = {"title": f"Case #{case_id} — {action.upper()}", "color": 0xED4245,
             "fields": [
                 {"name": "User", "value": f"<@{target}>", "inline": True},
                 {"name": "Moderator", "value": f"<@{moderator}>", "inline": True},
                 {"name": "Reason", "value": reason or "—", "inline": False},
             ]}
    await rest.post(f"/channels/{cfg['log_channel']}/messages", json={"embeds": [embed]})

@slash("ban", "Ban a member", [
    {"name": "member", "description": "Member to ban", "type": USER, "required": True},
    {"name": "reason", "description": "Reason", "type": STR, "required": False},
    {"name": "delete_days", "description": "Delete messages (0-7)", "type": INT, "required": False},
])
async def ban(i: Interaction, member, reason="No reason", delete_days=0):
    if not await _require_staff(i):
        return
    await i.rest.put(f"/guilds/{i.guild_id}/bans/{member}",
                     params={"delete_message_days": min(max(delete_days or 0, 0), 7)},
                     reason=reason)
    case = await db.add_case(i.guild_id, int(member), int(i.user_id), "ban", reason)
    await i.respond(f"✅ Banned <@{member}> — Case #{case}")
    await _log(i.guild_id, i.rest, "ban", member, i.user_id, reason, case)

@slash("kick", "Kick a member", [
    {"name": "member", "description": "Member to kick", "type": USER, "required": True},
    {"name": "reason", "description": "Reason", "type": STR, "required": False},
])
async def kick(i: Interaction, member, reason="No reason"):
    if not await _require_staff(i):
        return
    await i.rest.delete(f"/guilds/{i.guild_id}/members/{member}", reason=reason)
    case = await db.add_case(i.guild_id, int(member), int(i.user_id), "kick", reason)
    await i.respond(f"✅ Kicked <@{member}> — Case #{case}")
    await _log(i.guild_id, i.rest, "kick", member, i.user_id, reason, case)

@slash("timeout", "Timeout a member", [
    {"name": "member", "description": "Member", "type": USER, "required": True},
    {"name": "duration", "description": "e.g. 10m, 1h, 1d", "type": STR, "required": True},
    {"name": "reason", "description": "Reason", "type": STR, "required": False},
])
async def timeout(i: Interaction, member, duration, reason="No reason"):
    if not await _require_staff(i):
        return
    secs = parse_duration(duration)
    if not secs or secs > 28*86400:
        return await i.respond("❌ Duration must be ≤ 28 days (e.g. `10m`, `1h`).", ephemeral=True)
    until = datetime.now(timezone.utc).timestamp() + secs
    iso = datetime.fromtimestamp(until, tz=timezone.utc).isoformat()
    await i.rest.patch(f"/guilds/{i.guild_id}/members/{member}",
                       json={"communication_disabled_until": iso}, reason=reason)
    case = await db.add_case(i.guild_id, int(member), int(i.user_id), "timeout", reason,
                              duration=secs, expires_at=datetime.fromtimestamp(until, tz=timezone.utc))
    await i.respond(f"✅ Timed out <@{member}> for {duration} — Case #{case}")
    await _log(i.guild_id, i.rest, "timeout", member, i.user_id, reason, case, secs)

@slash("unban", "Unban a user by ID", [
    {"name": "user_id", "description": "ID of user to unban", "type": STR, "required": True},
    {"name": "reason", "description": "Reason", "type": STR, "required": False},
])
async def unban(i: Interaction, user_id, reason="No reason"):
    if not await _require_staff(i):
        return
    user_id = str(user_id).strip().strip("<@!>")
    try:
        await i.rest.delete(f"/guilds/{i.guild_id}/bans/{user_id}", reason=reason)
    except Exception as e:
        return await i.respond(f"❌ Failed to unban `{user_id}`: `{e}`", ephemeral=True)
    case = await db.add_case(i.guild_id, int(user_id), int(i.user_id), "unban", reason)
    await i.respond(f"✅ Unbanned <@{user_id}> — Case #{case}")
    await _log(i.guild_id, i.rest, "unban", user_id, i.user_id, reason, case)

@slash("untimeout", "Remove timeout from a member", [
    {"name": "member", "description": "Member to untimeout", "type": USER, "required": True},
    {"name": "reason", "description": "Reason", "type": STR, "required": False},
])
async def untimeout(i: Interaction, member, reason="No reason"):
    if not await _require_staff(i):
        return
    member_id = int(str(member).strip().strip("<@!>"))
    try:
        await i.rest.patch(f"/guilds/{i.guild_id}/members/{member_id}",
                           json={"communication_disabled_until": None}, reason=reason)
    except Exception as e:
        return await i.respond(f"❌ Failed to remove timeout: `{e}`", ephemeral=True)
    await db.pool.execute(
        "UPDATE mod_cases SET active=FALSE WHERE guild_id=$1 AND user_id=$2 AND action='timeout'",
        int(i.guild_id), member_id,
    )
    case = await db.add_case(i.guild_id, member_id, int(i.user_id), "untimeout", reason)
    await i.respond(f"✅ Removed timeout for <@{member_id}> — Case #{case}")
    await _log(i.guild_id, i.rest, "untimeout", member_id, i.user_id, reason, case)


@slash("warn", "Warn a member", [
    {"name": "member", "description": "Member", "type": USER, "required": True},
    {"name": "reason", "description": "Reason", "type": STR, "required": True},
])
async def warn(i: Interaction, member, reason):
    if not await _require_staff(i):
        return
    case = await db.add_case(i.guild_id, int(member), int(i.user_id), "warn", reason)
    # Try DM
    try:
        dm = await i.rest.post("/users/@me/channels", json={"recipient_id": str(member)})
        await i.rest.post(f"/channels/{dm['id']}/messages",
                          json={"content": f"⚠️ You were warned in a server: {reason}"})
    except Exception: pass
    await i.respond(f"✅ Warned <@{member}> — Case #{case}")
    await _log(i.guild_id, i.rest, "warn", member, i.user_id, reason, case)

@slash("warnings", "List infractions", [
    {"name": "member", "description": "Member", "type": USER, "required": True},
])
async def warnings(i: Interaction, member):
    if not await _require_staff(i):
        return
    rows = await db.get_cases(i.guild_id, int(member))
    if not rows:
        return await i.respond(f"**<@{member}>** has no infractions.")
    lines = [f"`#{r['id']}` **{r['action'].upper()}** — {r['reason'] or '—'} — <@{r['moderator_id']}>"
             for r in rows[:20]]
    embed = {"title": f"Infractions — <@{member}>", "description": "\n".join(lines), "color": 0xFAA61A}
    await i.respond(embeds=[embed])

@slash("purge", "Bulk delete messages", [
    {"name": "amount", "description": "1-100", "type": INT, "required": True},
])
async def purge(i: Interaction, amount):
    if not await _require_staff(i):
        return
    amount = min(max(amount, 1), 100)
    msgs = await i.rest.get(f"/channels/{i.channel_id}/messages", params={"limit": amount})
    ids = [m["id"] for m in msgs if (time.time() - _snowflake_ts(m["id"])) < 14*86400]
    if len(ids) == 1:
        await i.rest.delete(f"/channels/{i.channel_id}/messages/{ids[0]}")
    elif len(ids) > 1:
        await i.rest.post(f"/channels/{i.channel_id}/messages/bulk-delete", json={"messages": ids})
    await i.respond(f"🧹 Deleted {len(ids)} messages.", ephemeral=True)


@slash("delwarn", "Delete a specific infraction case by ID", [
    {"name": "case_id", "description": "Case ID number", "type": INT, "required": True},
])
async def delwarn(i: Interaction, case_id: int):
    if not await _require_staff(i):
        return
    res = await db.pool.execute(
        "DELETE FROM mod_cases WHERE id=$1 AND guild_id=$2",
        int(case_id), int(i.guild_id),
    )
    if res == "DELETE 0":
        return await i.respond(f"❌ Case `#{case_id}` was not found in this server.", ephemeral=True)
    await i.respond(f"✅ Deleted case `#{case_id}`.")


@slash("clearwarns", "Clear all infractions for a member", [
    {"name": "member", "description": "Member", "type": USER, "required": True},
])
async def clearwarns(i: Interaction, member):
    if not await _require_staff(i):
        return
    target_id = int(str(member).strip().strip("<@!>"))
    res = await db.pool.execute(
        "DELETE FROM mod_cases WHERE guild_id=$1 AND user_id=$2",
        int(i.guild_id), target_id,
    )
    count = int(res.split()[-1]) if res and res.startswith("DELETE ") else 0
    await i.respond(f"🧹 Cleared **{count}** infraction(s) for <@{target_id}>.")


@slash("lock", "Lock a text channel for @everyone", [
    {"name": "channel", "description": "Channel to lock (defaults to current)", "type": STR, "required": False},
])
async def lock_channel(i: Interaction, channel=None):
    if not await _require_staff(i):
        return
    target_ch = int(str(channel).strip().strip("<#>")) if channel else int(i.channel_id)
    SEND_MESSAGES = 1 << 11
    try:
        await i.rest.put(
            f"/channels/{target_ch}/permissions/{i.guild_id}",
            json={"type": 0, "allow": "0", "deny": str(SEND_MESSAGES)},
            reason=f"Locked by {i.user_id}",
        )
    except Exception as e:
        return await i.respond(f"❌ Failed to lock <#{target_ch}>: `{e}`", ephemeral=True)
    await i.respond(f"🔒 Locked <#{target_ch}> for `@everyone`.")


@slash("unlock", "Unlock a text channel for @everyone", [
    {"name": "channel", "description": "Channel to unlock (defaults to current)", "type": STR, "required": False},
])
async def unlock_channel(i: Interaction, channel=None):
    if not await _require_staff(i):
        return
    target_ch = int(str(channel).strip().strip("<#>")) if channel else int(i.channel_id)
    try:
        await i.rest.put(
            f"/channels/{target_ch}/permissions/{i.guild_id}",
            json={"type": 0, "allow": "0", "deny": "0"},
            reason=f"Unlocked by {i.user_id}",
        )
    except Exception as e:
        return await i.respond(f"❌ Failed to unlock <#{target_ch}>: `{e}`", ephemeral=True)
    await i.respond(f"🔓 Unlocked <#{target_ch}> for `@everyone`.")


@slash("slowmode", "Set channel slowmode delay", [
    {"name": "duration", "description": "Delay (e.g. off, 5s, 10m, or seconds)", "type": STR, "required": True},
])
async def slowmode(i: Interaction, duration: str):
    if not await _require_staff(i):
        return
    raw = str(duration).strip().lower()
    if raw in ("off", "0", "0s", "none", "disable"):
        secs = 0
    elif raw.isdigit():
        secs = int(raw)
    else:
        secs = parse_duration(raw)
    if secs is None or secs < 0 or secs > 21600:
        return await i.respond("❌ Slowmode must be between `0` (`off`) and `6h` (`21600s`).", ephemeral=True)
    try:
        await i.rest.patch(
            f"/channels/{i.channel_id}",
            json={"rate_limit_per_user": secs},
            reason=f"Slowmode set by {i.user_id}",
        )
    except Exception as e:
        return await i.respond(f"❌ Failed to set slowmode: `{e}`", ephemeral=True)
    if secs == 0:
        await i.respond("⏱️ Slowmode disabled for this channel.")
    else:
        await i.respond(f"⏱️ Slowmode set to **{duration}** (`{secs}s`).")


@slash("nick", "Change or reset a member's nickname", [
    {"name": "member", "description": "Member", "type": USER, "required": True},
    {"name": "nickname", "description": "New nickname (leave blank to reset)", "type": STR, "required": False},
])
async def nick(i: Interaction, member, nickname=None):
    if not await _require_staff(i):
        return
    target_id = int(str(member).strip().strip("<@!>"))
    new_nick = nickname.strip()[:32] if nickname and nickname.strip().lower() not in ("reset", "off", "clear") else None
    try:
        await i.rest.patch(
            f"/guilds/{i.guild_id}/members/{target_id}",
            json={"nick": new_nick},
            reason=f"Nickname changed by {i.user_id}",
        )
    except Exception as e:
        return await i.respond(f"❌ Could not update nickname: `{e}`", ephemeral=True)
    if new_nick:
        await i.respond(f"✏️ Changed <@{target_id}>'s nickname to **{new_nick}**.")
    else:
        await i.respond(f"✏️ Reset <@{target_id}>'s nickname.")


def _snowflake_ts(sid):
    return ((int(sid) >> 22) + 1420070400000) / 1000

# ---- AutoMod detection (called from main dispatch) ----
_spam_cache: dict[tuple[int,int], list[float]] = {}
_INVITE_RE = re.compile(r"(?:https?://)?(?:www\.)?(?:discord\.(?:gg|io|me|li)|discord(?:app)?\.com/invite)/[a-zA-Z0-9]+", re.IGNORECASE)
_MENTION_RE = re.compile(r"<@!?\d+>|<@&\d+>")

async def automod_message(i_guild, i_channel, author_id, rest, content="", message_id=None, is_staff_user=False):
    if is_staff_user:
        return

    # 1. Anti-Invite filter
    if content and _INVITE_RE.search(content):
        if message_id:
            try:
                await rest.delete(f"/channels/{i_channel}/messages/{message_id}")
            except Exception:
                pass
        bot_id = int(state.bot_user_id or 0)
        case = await db.add_case(i_guild, author_id, bot_id, "automod_invite", "Posted unauthorized invite link")
        await rest.post(f"/channels/{i_channel}/messages",
                        json={"content": f"⚠️ <@{author_id}> Discord invite links are not allowed here."})
        await _log(i_guild, rest, "automod_invite", author_id, bot_id, "Posted unauthorized invite link", case)
        return

    # 2. Mass-mention filter (> 5 mentions)
    if content and len(_MENTION_RE.findall(content)) >= 5:
        if message_id:
            try:
                await rest.delete(f"/channels/{i_channel}/messages/{message_id}")
            except Exception:
                pass
        try:
            bot_id = int(state.bot_user_id or 0)
            until = datetime.now(timezone.utc).timestamp() + 600
            iso = datetime.fromtimestamp(until, tz=timezone.utc).isoformat()
            await rest.patch(f"/guilds/{i_guild}/members/{author_id}",
                             json={"communication_disabled_until": iso},
                             reason="Automod: mass mentions")
            case = await db.add_case(i_guild, author_id, bot_id, "timeout", "Automod: mass mentions",
                                     duration=600, expires_at=datetime.fromtimestamp(until, tz=timezone.utc))
            await rest.post(f"/channels/{i_channel}/messages",
                            json={"content": f"🚫 <@{author_id}> muted for 10m (mass mentions)."})
            await _log(i_guild, rest, "timeout", author_id, bot_id, "Automod: mass mentions", case, 600)
        except Exception:
            pass
        return

    # 3. Spam rate limiting
    key = (i_guild, author_id)
    now = time.time()
    bucket = _spam_cache.setdefault(key, [])
    bucket[:] = [t for t in bucket if now - t < 5]
    bucket.append(now)
    if len(bucket) >= 6:
        _spam_cache[key] = []
        try:
            bot_id = int(state.bot_user_id or 0)
            until = datetime.now(timezone.utc).timestamp() + 300
            iso = datetime.fromtimestamp(until, tz=timezone.utc).isoformat()
            await rest.patch(f"/guilds/{i_guild}/members/{author_id}",
                             json={"communication_disabled_until": iso},
                             reason="Automod: spam")
            case = await db.add_case(i_guild, author_id, bot_id, "timeout", "Automod: spam",
                                     duration=300, expires_at=datetime.fromtimestamp(until, tz=timezone.utc))
            await rest.post(f"/channels/{i_channel}/messages",
                            json={"content": f"🚫 <@{author_id}> muted for 5m (spam)."})
            await _log(i_guild, rest, "timeout", author_id, bot_id, "Automod: spam", case, 300)
        except Exception:
            pass


# ---- Expiry background task ----
async def expire_loop(rest, interval=30):
    while True:
        await asyncio.sleep(interval)
        try:
            rows = await db.pool.fetch(
                "SELECT id, guild_id, user_id FROM mod_cases "
                "WHERE active=TRUE AND expires_at <= NOW() AND action='timeout'")
            for r in rows:
                try:
                    await rest.patch(f"/guilds/{r['guild_id']}/members/{r['user_id']}",
                                     json={"communication_disabled_until": None})
                except Exception: pass
                await db.pool.execute("UPDATE mod_cases SET active=FALSE WHERE id=$1", r["id"])
        except Exception: pass
