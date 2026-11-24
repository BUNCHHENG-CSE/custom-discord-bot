"""Community engagement: XP/Leveling, AFK, Reminders, Suggestions, and Starboard."""

import asyncio
import random
import re
import time
from datetime import datetime, timezone

from database import db
from helpers.permissions import can_manage_bot_async, can_use_staff_commands
from interactions import slash, Interaction, USER, STR, INT, ROLE, CHANNEL
from modules.moderation import parse_duration

# Cooldown tracker for XP gain: (guild_id, user_id) -> last_awarded_epoch
_xp_cooldowns: dict[tuple[int, int], float] = {}
# Message counts accumulated while a user is on XP cooldown, flushed in batches
# so ordinary chatter doesn't cost one DB round-trip per message.
_pending_msgs: dict[tuple[int, int], int] = {}
_flush_task: asyncio.Task | None = None
_FLUSH_INTERVAL = 30.0


async def _flush_pending_msgs() -> None:
    if not _pending_msgs:
        return
    batch = [(g, u, n) for (g, u), n in _pending_msgs.items()]
    _pending_msgs.clear()
    await db.pool.executemany(
        """INSERT INTO user_levels (guild_id, user_id, xp, level, messages)
           VALUES ($1, $2, 0, 0, $3)
           ON CONFLICT (guild_id, user_id)
           DO UPDATE SET messages = user_levels.messages + EXCLUDED.messages""",
        batch,
    )


async def _flush_loop() -> None:
    while True:
        await asyncio.sleep(_FLUSH_INTERVAL)
        try:
            await _flush_pending_msgs()
        except Exception:
            pass
        # Drop expired cooldown entries so the dict doesn't grow forever
        cutoff = time.time() - 60.0
        for k in [k for k, t in _xp_cooldowns.items() if t < cutoff]:
            _xp_cooldowns.pop(k, None)
# Suggestion vote tracker: message_id -> {"up": set[int], "down": set[int]}
_suggestion_votes: dict[int, dict[str, set[int]]] = {}


def _xp_for_next_level(level: int) -> int:
    """Standard curve: 100, 255, 475, 760..."""
    return 5 * (level ** 2) + 50 * level + 100


# =====================================================================
# 1. XP & Leveling System
# =====================================================================
async def award_message_xp(rest, guild_id: int, channel_id: int, user_id: int) -> None:
    now = time.time()
    key = (guild_id, user_id)

    # Use Redis for 60s XP cooldown if available, with in-memory fallback
    on_cooldown = False
    if db.redis:
        try:
            rkey = f"xp_cd:{guild_id}:{user_id}"
            # Atomic check-and-set: one round-trip instead of GET + SET
            on_cooldown = not await db.redis.set(rkey, "1", ex=60, nx=True)
        except Exception:
            on_cooldown = (now - _xp_cooldowns.get(key, 0.0)) < 60.0
    else:
        on_cooldown = (now - _xp_cooldowns.get(key, 0.0)) < 60.0

    global _flush_task
    if _flush_task is None or _flush_task.done():
        _flush_task = asyncio.create_task(_flush_loop(), name="xp-flush")

    if on_cooldown:
        _pending_msgs[key] = _pending_msgs.get(key, 0) + 1
        return

    _xp_cooldowns[key] = now
    gained = random.randint(15, 25)
    msgs = 1 + _pending_msgs.pop(key, 0)
    row = await db.pool.fetchrow(
        """INSERT INTO user_levels (guild_id, user_id, xp, level, messages)
           VALUES ($1, $2, $3, 0, $4)
           ON CONFLICT (guild_id, user_id)
           DO UPDATE SET xp = user_levels.xp + $3, messages = user_levels.messages + $4
           RETURNING xp, level""",
        guild_id, user_id, gained, msgs,
    )
    if not row:
        return

    xp, level = int(row["xp"]), int(row["level"])
    needed = _xp_for_next_level(level)
    if xp >= needed:
        new_level = level + 1
        await db.pool.execute(
            "UPDATE user_levels SET level = $1, xp = $2 WHERE guild_id = $3 AND user_id = $4",
            new_level, xp - needed, guild_id, user_id,
        )
        # Check for level reward role
        reward = await db.pool.fetchrow(
            "SELECT role_id FROM level_rewards WHERE guild_id = $1 AND level = $2",
            guild_id, new_level,
        )
        role_msg = ""
        if reward:
            try:
                await rest.put(
                    f"/guilds/{guild_id}/members/{user_id}/roles/{reward['role_id']}",
                    reason=f"Reached level {new_level}",
                )
                role_msg = f" and earned <@&{reward['role_id']}>"
            except Exception:
                pass
        try:
            await rest.post(
                f"/channels/{channel_id}/messages",
                json={"content": f"🎉 GG <@{user_id}>, you leveled up to **Level {new_level}**{role_msg}!"},
            )
        except Exception:
            pass


@slash("rank", "View your or another member's XP and level", [
    {"name": "member", "description": "Member to inspect", "type": USER, "required": False},
])
async def rank_cmd(i: Interaction, member=None):
    target_id = int(str(member).strip().strip("<@!>")) if member else int(i.user_id)
    gid = int(i.guild_id)
    row = await db.pool.fetchrow(
        "SELECT xp, level, messages FROM user_levels WHERE guild_id=$1 AND user_id=$2",
        gid, target_id,
    )
    xp = int(row["xp"]) if row else 0
    level = int(row["level"]) if row else 0
    msgs = int(row["messages"]) if row else 0
    needed = _xp_for_next_level(level)

    rank_pos = await db.pool.fetchval(
        """SELECT COUNT(*) + 1 FROM user_levels
           WHERE guild_id=$1 AND (level > $2 OR (level = $2 AND xp > $3))""",
        gid, level, xp,
    )
    pct = min(100, int((xp / max(1, needed)) * 100))
    filled = pct // 10
    bar = "🟩" * filled + "⬛" * (10 - filled)

    embed = {
        "title": "🏆 Level & Rank Card",
        "description": f"**Member:** <@{target_id}>\n**Progress:** {bar} `{xp}/{needed} XP` ({pct}%)",
        "color": 0x57F287,
        "fields": [
            {"name": "Rank", "value": f"`#{rank_pos}`", "inline": True},
            {"name": "Level", "value": f"`{level}`", "inline": True},
            {"name": "Messages", "value": f"`{msgs:,}`", "inline": True},
        ],
    }
    await i.respond(embeds=[embed])


@slash("leaderboard", "Show the top 10 XP leaderboard for this server")
async def leaderboard_cmd(i: Interaction):
    rows = await db.pool.fetch(
        """SELECT user_id, level, xp, messages FROM user_levels
           WHERE guild_id=$1 ORDER BY level DESC, xp DESC LIMIT 10""",
        int(i.guild_id),
    )
    if not rows:
        return await i.respond("📭 No XP activity recorded yet in this server.")
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines = []
    for idx, r in enumerate(rows, start=1):
        badge = medals.get(idx, f"`#{idx}`")
        lines.append(
            f"{badge} <@{r['user_id']}> — **Level {r['level']}** "
            f"(`{r['xp']} XP` • `{r['messages']:,}` msgs)"
        )
    embed = {
        "title": "🏅 Server XP Leaderboard",
        "description": "\n".join(lines),
        "color": 0xFEE75C,
    }
    await i.respond(embeds=[embed])


@slash("levelreward", "Configure a role reward for reaching a specific level", [
    {"name": "level", "description": "Level number", "type": INT, "required": True},
    {"name": "role", "description": "Role to grant", "type": ROLE, "required": True},
])
async def levelreward_cmd(i: Interaction, level: int, role):
    if not await can_manage_bot_async(i, db.pool):
        return await i.respond("❌ Only the server owner or an administrator can configure level rewards.", ephemeral=True)
    lvl = int(level)
    rid = int(str(role).strip().strip("<@&>"))
    if lvl < 1 or lvl > 500:
        return await i.respond("❌ Level must be between 1 and 500.", ephemeral=True)
    await db.pool.execute(
        """INSERT INTO level_rewards (guild_id, level, role_id)
           VALUES ($1, $2, $3)
           ON CONFLICT (guild_id, level) DO UPDATE SET role_id = EXCLUDED.role_id""",
        int(i.guild_id), lvl, rid,
    )
    await i.respond(f"✅ Members reaching **Level {lvl}** will now receive <@&{rid}>.")


# =====================================================================
# 2. AFK System (with in-memory index to avoid DB hits on normal messages)
# =====================================================================
_afk_cache: set[tuple[int, int]] | None = None


async def _ensure_afk_cache() -> set[tuple[int, int]]:
    global _afk_cache
    if _afk_cache is None:
        rows = await db.pool.fetch("SELECT guild_id, user_id FROM afk_users")
        _afk_cache = {(int(r["guild_id"]), int(r["user_id"])) for r in rows}
    return _afk_cache


@slash("afk", "Set your AFK status", [
    {"name": "reason", "description": "AFK reason", "type": STR, "required": False},
])
async def afk_cmd(i: Interaction, reason: str = "AFK"):
    clean_reason = (reason or "AFK").strip()[:200]
    gid, uid = int(i.guild_id), int(i.user_id)
    await db.pool.execute(
        """INSERT INTO afk_users (guild_id, user_id, reason, created_at)
           VALUES ($1, $2, $3, NOW())
           ON CONFLICT (guild_id, user_id)
           DO UPDATE SET reason = EXCLUDED.reason, created_at = NOW()""",
        gid, uid, clean_reason,
    )
    afk_set = await _ensure_afk_cache()
    afk_set.add((gid, uid))
    await i.respond(f"💤 <@{i.user_id}> is now AFK: **{clean_reason}**")


async def handle_afk_message(rest, d: dict) -> None:
    guild_id = int(d["guild_id"])
    channel_id = int(d["channel_id"])
    author_id = int(d["author"]["id"])
    content = (d.get("content") or "").strip()
    afk_set = await _ensure_afk_cache()

    # If the author was AFK (and isn't just invoking tct!afk), clear their AFK status
    if (guild_id, author_id) in afk_set and not content.lower().startswith("tct!afk"):
        afk_set.discard((guild_id, author_id))
        removed = await db.pool.fetchrow(
            "DELETE FROM afk_users WHERE guild_id=$1 AND user_id=$2 RETURNING created_at",
            guild_id, author_id,
        )
        if removed:
            try:
                await rest.post(
                    f"/channels/{channel_id}/messages",
                    json={"content": f"👋 Welcome back <@{author_id}>! I removed your AFK status."},
                )
            except Exception:
                pass

    # Notify if any mentioned users are AFK
    if "<@" not in content:
        return
    mentioned_ids = {
        int(m) for m in re.findall(r"<@!?(\d+)>", content)
        if (guild_id, int(m)) in afk_set
    } - {author_id}
    if not mentioned_ids:
        return
    rows = await db.pool.fetch(
        "SELECT user_id, reason, created_at FROM afk_users WHERE guild_id=$1 AND user_id = ANY($2::bigint[])",
        guild_id, list(mentioned_ids),
    )
    if rows:
        lines = [
            f"💤 <@{r['user_id']}> is AFK: **{r['reason']}** (<t:{int(r['created_at'].timestamp())}:R>)"
            for r in rows
        ]
        try:
            await rest.post(
                f"/channels/{channel_id}/messages",
                json={"content": "\n".join(lines), "allowed_mentions": {"parse": []}},
            )
        except Exception:
            pass


# =====================================================================
# 3. Reminders
# =====================================================================
@slash("remind", "Set a personal reminder", [
    {"name": "duration", "description": "e.g. 10m, 2h, 1d", "type": STR, "required": True},
    {"name": "text", "description": "What to remind you about", "type": STR, "required": True},
])
async def remind_cmd(i: Interaction, duration: str, text: str):
    secs = parse_duration(duration)
    if not secs or secs < 5 or secs > 365 * 86400:
        return await i.respond("❌ Invalid duration. Example: `tct!remind 30m Check the oven`", ephemeral=True)
    remind_ts = time.time() + secs
    remind_dt = datetime.fromtimestamp(remind_ts, tz=timezone.utc)
    await db.pool.execute(
        """INSERT INTO reminders (guild_id, channel_id, user_id, reminder, remind_at)
           VALUES ($1, $2, $3, $4, $5)""",
        int(i.guild_id or 0), int(i.channel_id), int(i.user_id), text[:500], remind_dt,
    )
    await i.respond(f"⏰ I'll remind you <t:{int(remind_ts)}:R>: **{text[:200]}**")


async def reminder_loop(rest, interval: int = 15) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            due = await db.pool.fetch(
                "SELECT * FROM reminders WHERE sent=FALSE AND remind_at <= NOW()"
            )
            for r in due:
                await db.pool.execute("UPDATE reminders SET sent=TRUE WHERE id=$1", r["id"])
                created_ts = int(r["created_at"].timestamp())
                msg = f"⏰ **Reminder** for <@{r['user_id']}> (set <t:{created_ts}:R>):\n> {r['reminder']}"
                try:
                    await rest.post(f"/channels/{r['channel_id']}/messages", json={"content": msg})
                except Exception:
                    pass
        except Exception:
            pass


# =====================================================================
# 4. Suggestions System
# =====================================================================
def _suggest_components(up_count: int = 0, down_count: int = 0, closed: bool = False) -> list[dict]:
    return [{
        "type": 1,
        "components": [
            {"type": 2, "style": 3, "label": f"Upvote ({up_count})", "emoji": {"name": "👍"}, "custom_id": "community:sug:up", "disabled": closed},
            {"type": 2, "style": 4, "label": f"Downvote ({down_count})", "emoji": {"name": "👎"}, "custom_id": "community:sug:down", "disabled": closed},
            {"type": 2, "style": 1, "label": "Approve", "emoji": {"name": "✅"}, "custom_id": "community:sug:approve", "disabled": closed},
            {"type": 2, "style": 2, "label": "Deny", "emoji": {"name": "❌"}, "custom_id": "community:sug:deny", "disabled": closed},
        ],
    }]


@slash("setsuggest", "Set the channel for member suggestions", [
    {"name": "channel", "description": "Suggestions channel", "type": CHANNEL, "required": True},
])
async def setsuggest_cmd(i: Interaction, channel):
    if not await can_manage_bot_async(i, db.pool):
        return await i.respond("❌ Only the server owner or an administrator can configure suggestions.", ephemeral=True)
    cid = int(str(channel).strip().strip("<#>"))
    await db.ensure_guild(i.guild_id)
    await db.pool.execute("UPDATE guilds SET suggest_channel=$1 WHERE id=$2", cid, int(i.guild_id))
    db.invalidate_guild(i.guild_id)
    await i.respond(f"✅ Suggestions channel set to <#{cid}>.")


@slash("suggest", "Submit a server suggestion", [
    {"name": "idea", "description": "Your suggestion", "type": STR, "required": True},
])
async def suggest_cmd(i: Interaction, idea: str):
    cfg = await db.get_guild(i.guild_id)
    target_ch = int(cfg["suggest_channel"]) if cfg and cfg.get("suggest_channel") else int(i.channel_id)
    embed = {
        "title": "💡 New Suggestion",
        "description": idea[:1500],
        "color": 0x5865F2,
        "fields": [
            {"name": "Submitted By", "value": f"<@{i.user_id}>", "inline": True},
            {"name": "Status", "value": "⏳ Pending Review", "inline": True},
        ],
    }
    msg = await i.rest.post(
        f"/channels/{target_ch}/messages",
        json={"embeds": [embed], "components": _suggest_components(0, 0)},
    )
    _suggestion_votes[int(msg["id"])] = {"up": set(), "down": set()}
    if target_ch != int(i.channel_id) or i.token:
        await i.respond(f"✅ Your suggestion was posted in <#{target_ch}>!", ephemeral=True)


async def handle_component(i: Interaction) -> None:
    cid = i.custom_id or ""
    if not cid.startswith("community:sug:"):
        return
    action = cid.split(":")[2]
    msg_id = int(i.message.get("id") or 0)
    uid = int(i.user_id)
    votes = _suggestion_votes.setdefault(msg_id, {"up": set(), "down": set()})
    embeds = i.message.get("embeds") or [{}]
    embed = dict(embeds[0])

    if action in ("up", "down"):
        other = "down" if action == "up" else "up"
        votes[other].discard(uid)
        if uid in votes[action]:
            votes[action].discard(uid)
        else:
            votes[action].add(uid)
        return await i.update(
            embeds=[embed],
            components=_suggest_components(len(votes["up"]), len(votes["down"])),
        )

    if action in ("approve", "deny"):
        if not await can_use_staff_commands(i, db.pool):
            return await i.respond("❌ Only configured staff can approve or deny suggestions.", ephemeral=True)
        approved = action == "approve"
        embed["color"] = 0x57F287 if approved else 0xED4245
        fields = [f for f in embed.get("fields", []) if f.get("name") != "Status"]
        fields.append({
            "name": "Status",
            "value": f"{'✅ Approved' if approved else '❌ Denied'} by <@{uid}>",
            "inline": True,
        })
        embed["fields"] = fields
        return await i.update(
            embeds=[embed],
            components=_suggest_components(len(votes["up"]), len(votes["down"]), closed=True),
        )


# =====================================================================
# 5. Starboard
# =====================================================================
@slash("setstarboard", "Configure the starboard channel and reaction threshold", [
    {"name": "channel", "description": "Starboard channel", "type": CHANNEL, "required": True},
    {"name": "threshold", "description": "Minimum ⭐ reactions (default: 3)", "type": INT, "required": False},
])
async def setstarboard_cmd(i: Interaction, channel, threshold: int = 3):
    if not await can_manage_bot_async(i, db.pool):
        return await i.respond("❌ Only the server owner or an administrator can configure the starboard.", ephemeral=True)
    cid = int(str(channel).strip().strip("<#>"))
    thresh = max(1, min(int(threshold or 3), 100))
    await db.ensure_guild(i.guild_id)
    await db.pool.execute(
        "UPDATE guilds SET starboard_channel=$1, starboard_threshold=$2 WHERE id=$3",
        cid, thresh, int(i.guild_id),
    )
    db.invalidate_guild(i.guild_id)
    await i.respond(f"⭐ Starboard set to <#{cid}> (threshold: **{thresh}** ⭐).")


async def handle_starboard_reaction(rest, payload: dict) -> None:
    if not payload.get("guild_id"):
        return
    emoji_name = (payload.get("emoji") or {}).get("name")
    if emoji_name not in ("⭐", "🌟"):
        return

    guild_id = int(payload["guild_id"])
    cfg = await db.get_guild(guild_id)
    if not cfg or not cfg.get("starboard_channel"):
        return
    sb_channel = int(cfg["starboard_channel"])
    threshold = int(cfg.get("starboard_threshold") or 3)
    channel_id = int(payload["channel_id"])
    message_id = int(payload["message_id"])
    if channel_id == sb_channel:
        return

    try:
        msg = await rest.get(f"/channels/{channel_id}/messages/{message_id}")
    except Exception:
        return

    star_count = 0
    for r in msg.get("reactions", []):
        if (r.get("emoji") or {}).get("name") in ("⭐", "🌟"):
            star_count = max(star_count, int(r.get("count", 0)))
    if star_count < threshold:
        return

    author = msg.get("author") or {}
    jump_url = f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"
    embed = {
        "author": {"name": author.get("username", "Unknown")},
        "description": (msg.get("content") or "")[:1800],
        "color": 0xFEE75C,
        "fields": [
            {"name": "Source", "value": f"[Jump to Message]({jump_url}) in <#{channel_id}>", "inline": True},
        ],
        "footer": {"text": f"Message ID: {message_id}"},
    }
    attachments = msg.get("attachments") or []
    if attachments and attachments[0].get("url"):
        embed["image"] = {"url": attachments[0]["url"]}

    header = f"⭐ **{star_count}** | <#{channel_id}>"
    existing = await db.pool.fetchrow(
        "SELECT starboard_message_id FROM starboard_entries WHERE guild_id=$1 AND source_message_id=$2",
        guild_id, message_id,
    )
    if existing:
        try:
            await rest.patch(
                f"/channels/{sb_channel}/messages/{existing['starboard_message_id']}",
                json={"content": header, "embeds": [embed]},
            )
            await db.pool.execute(
                "UPDATE starboard_entries SET stars=$1 WHERE guild_id=$2 AND source_message_id=$3",
                star_count, guild_id, message_id,
            )
        except Exception:
            pass
    else:
        try:
            posted = await rest.post(
                f"/channels/{sb_channel}/messages",
                json={"content": header, "embeds": [embed]},
            )
            await db.pool.execute(
                """INSERT INTO starboard_entries (guild_id, source_message_id, starboard_message_id, stars)
                   VALUES ($1, $2, $3, $4)
                   ON CONFLICT (guild_id, source_message_id) DO NOTHING""",
                guild_id, message_id, int(posted["id"]), star_count,
            )
        except Exception:
            pass
