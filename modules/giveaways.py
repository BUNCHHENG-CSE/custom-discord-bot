import asyncio, random, time
from datetime import datetime, timezone
from interactions import slash, Interaction, STR, INT
from database import db
from modules.moderation import parse_duration

def _giveaway_components():
    return [{"type": 1, "components": [
        {"type": 2, "style": 3, "label": "Enter", "emoji": {"name": "🎉"},
         "custom_id": "giveaway:enter"}
    ]}]

async def _do_end(gw, rest):
    entries = await db.pool.fetch(
        "SELECT user_id FROM giveaway_entries WHERE giveaway_id=$1", gw["id"])
    ids = [r["user_id"] for r in entries]
    winners = random.sample(ids, min(gw["winners"], len(ids))) if ids else []
    desc = f"**Prize:** {gw['prize']}\n**Winners:** " + (
        ", ".join(f"<@{w}>" for w in winners) if winners else "No valid entries.")
    try:
        await rest.patch(f"/channels/{gw['channel_id']}/messages/{gw['message_id']}",
                         json={"embeds": [{"title": "🎉 GIVEAWAY ENDED",
                                            "description": desc, "color": 0xE0A80D}],
                               "components": []})
        if winners:
            await rest.post(f"/channels/{gw['channel_id']}/messages",
                            json={"content": "🎉 Congratulations " +
                                  ", ".join(f"<@{w}>" for w in winners) +
                                  f"! You won **{gw['prize']}**!"})
    except Exception: pass
    await db.pool.execute("UPDATE giveaways SET ended=TRUE WHERE id=$1", gw["id"])

@slash("giveaway", "Giveaway commands", [
    {"name": "start", "description": "Start a giveaway", "type": 1, "options": [
        {"name": "duration", "description": "e.g. 1h, 2d", "type": STR, "required": True},
        {"name": "winners", "description": "Number of winners", "type": INT, "required": True},
        {"name": "prize", "description": "Prize", "type": STR, "required": True},
    ]},
])
async def giveaway_root(i: Interaction):
    # This exists only for the top-level /giveaway group when using subcommands.
    # Real handlers are the subcommands below.
    await i.respond("Use `tct!giveawaystart` or `tct!giveawayreroll`.", ephemeral=True)

# Register subcommands
from interactions import subcommand, _specs
from helpers.permissions import can_use_staff_commands
# Remove the auto-generated group root from the "slash" registration above by
# registering subcommands instead. Cleaner: skip the `slash` above and use only:
_specs[:] = [s for s in _specs if s["name"] != "giveaway"]

@subcommand("giveaway", "Giveaway commands", "start", "Start a giveaway", [
    {"name": "duration", "description": "e.g. 1h, 2d", "type": STR, "required": True},
    {"name": "winners", "description": "Number of winners", "type": INT, "required": True},
    {"name": "prize", "description": "Prize", "type": STR, "required": True},
])
async def start(i: Interaction, duration, winners, prize):
    if not await can_use_staff_commands(i, db.pool):
        return await i.respond("❌ This command is limited to configured staff.", ephemeral=True)
    secs = parse_duration(duration)
    if not secs:
        return await i.respond("❌ Invalid duration.", ephemeral=True)
    ends = time.time() + secs
    embed = {"title": "🎉 GIVEAWAY 🎉",
             "description": f"**Prize:** {prize}\n**Winners:** {winners}\n**Ends:** <t:{int(ends)}:R>",
             "color": 0xFEE75C, "footer": {"text": f"Hosted by {i.user['username']}"}}
    msg = await i.rest.post(f"/channels/{i.channel_id}/messages",
                             json={"embeds": [embed], "components": _giveaway_components()})
    await db.pool.execute(
        """INSERT INTO giveaways (guild_id,channel_id,message_id,prize,winners,ends_at,host_id)
           VALUES ($1,$2,$3,$4,$5,$6,$7)""",
        int(i.guild_id), int(i.channel_id), int(msg["id"]), prize, int(winners),
        datetime.fromtimestamp(ends, tz=timezone.utc), int(i.user_id))
    await i.respond("✅ Giveaway started.", ephemeral=True)

@subcommand("giveaway", "Giveaway commands", "reroll", "Reroll a winner", [
    {"name": "message_id", "description": "Giveaway message ID", "type": STR, "required": True},
])
async def reroll(i: Interaction, message_id):
    if not await can_use_staff_commands(i, db.pool):
        return await i.respond("❌ This command is limited to configured staff.", ephemeral=True)
    gw = await db.pool.fetchrow("SELECT * FROM giveaways WHERE message_id=$1", int(message_id))
    if not gw:
        return await i.respond("❌ Giveaway not found.", ephemeral=True)
    ids = [r["user_id"] for r in await db.pool.fetch(
        "SELECT user_id FROM giveaway_entries WHERE giveaway_id=$1", gw["id"])]
    if not ids:
        return await i.respond("No entries.", ephemeral=True)
    await i.respond(f"🎉 New winner: <@{random.choice(ids)}>!")


@subcommand("giveaway", "Giveaway commands", "end", "End an active giveaway immediately", [
    {"name": "message_id", "description": "Giveaway message ID", "type": STR, "required": True},
])
async def end_giveaway(i: Interaction, message_id):
    if not await can_use_staff_commands(i, db.pool):
        return await i.respond("❌ This command is limited to configured staff.", ephemeral=True)
    gw = await db.pool.fetchrow(
        "SELECT * FROM giveaways WHERE message_id=$1 AND guild_id=$2",
        int(message_id), int(i.guild_id),
    )
    if not gw:
        return await i.respond("❌ Giveaway not found in this server.", ephemeral=True)
    if gw["ended"]:
        return await i.respond("ℹ️ That giveaway has already ended.", ephemeral=True)
    await _do_end(gw, i.rest)
    await i.respond(f"🏁 Ended giveaway for **{gw['prize']}**.")


@subcommand("giveaway", "Giveaway commands", "list", "List active giveaways in this server")
async def list_giveaways(i: Interaction):
    rows = await db.pool.fetch(
        "SELECT * FROM giveaways WHERE guild_id=$1 AND ended=FALSE ORDER BY ends_at ASC",
        int(i.guild_id),
    )
    if not rows:
        return await i.respond("ℹ️ No active giveaways in this server.")
    lines = []
    for r in rows[:20]:
        end_ts = int(r["ends_at"].timestamp())
        link = f"https://discord.com/channels/{i.guild_id}/{r['channel_id']}/{r['message_id']}"
        lines.append(
            f"• **[`{r['message_id']}`]({link})** — **{r['prize']}** "
            f"({r['winners']} winner(s)) • ends <t:{end_ts}:R>"
        )
    embed = {
        "title": f"🎉 Active Giveaways ({len(rows)})",
        "description": "\n".join(lines),
        "color": 0xFEE75C,
    }
    await i.respond(embeds=[embed])


async def handle_component(i: Interaction):
    if i.custom_id == "giveaway:enter":
        gw = await db.pool.fetchrow("SELECT * FROM giveaways WHERE message_id=$1",
                                     int(i.message["id"]))
        if not gw or gw["ended"]:
            return await i.respond("Giveaway ended.", ephemeral=True)
        try:
            await db.pool.execute(
                "INSERT INTO giveaway_entries (giveaway_id,user_id) VALUES ($1,$2)",
                gw["id"], i.user_id)
            await i.respond("✅ You're in!", ephemeral=True)
        except Exception:
            await i.respond("You're already entered.", ephemeral=True)

async def end_loop(rest, interval=20):
    while True:
        await asyncio.sleep(interval)
        try:
            due = await db.pool.fetch(
                "SELECT * FROM giveaways WHERE ended=FALSE AND ends_at <= NOW()")
            for gw in due:
                await _do_end(gw, rest)
        except Exception: pass
