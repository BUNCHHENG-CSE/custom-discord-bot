import asyncio, aiohttp, feedparser
from interactions import subcommand, Interaction, STR, INT
from database import db
from helpers.permissions import can_manage_bot

@subcommand("feed", "RSS feeds", "add", "Add an RSS feed", [
    {"name": "url", "description": "Feed URL", "type": STR, "required": True},
])
async def add(i: Interaction, url):
    if not can_manage_bot(i):
        return await i.respond("❌ Only the server owner or an administrator can configure feeds.", ephemeral=True)
    fid = await db.pool.fetchval(
        "INSERT INTO feeds (guild_id,channel_id,url) VALUES ($1,$2,$3) RETURNING id",
        i.guild_id, i.channel_id, url)
    await i.respond(f"✅ Feed #{fid} added.", ephemeral=True)

@subcommand("feed", "RSS feeds", "remove", "Remove an RSS feed by ID", [
    {"name": "id", "description": "Feed ID", "type": INT, "required": True},
])
async def remove_feed(i: Interaction, id):
    if not can_manage_bot(i):
        return await i.respond("❌ Only the server owner or an administrator can configure feeds.", ephemeral=True)
    res = await db.pool.execute(
        "DELETE FROM feeds WHERE id=$1 AND guild_id=$2", int(id), int(i.guild_id)
    )
    if res == "DELETE 0":
        return await i.respond(f"❌ Feed `#{id}` was not found in this server.", ephemeral=True)
    await i.respond(f"✅ Feed `#{id}` removed.")

@subcommand("feed", "RSS feeds", "list", "List feeds")
async def list_feeds(i: Interaction):
    rows = await db.pool.fetch("SELECT * FROM feeds WHERE guild_id=$1", i.guild_id)
    if not rows: return await i.respond("No feeds configured.")
    lines = [f"`#{r['id']}` → <#{r['channel_id']}> — {r['url']}" for r in rows]
    await i.respond("\n".join(lines))


async def poll_loop(rest, session: aiohttp.ClientSession, interval=300):
    while True:
        await asyncio.sleep(interval)
        try:
            feeds = await db.pool.fetch("SELECT * FROM feeds")
            for f in feeds:
                try:
                    async with session.get(f["url"], timeout=15) as r:
                        content = await r.text()
                    parsed = feedparser.parse(content)
                    if not parsed.entries: continue
                    latest = parsed.entries[0]
                    guid = latest.get("id") or latest.get("link")
                    if guid == f["last_seen"]: continue
                    embed = {"title": latest.get("title", "New post")[:250],
                             "url": latest.get("link"),
                             "description": (latest.get("summary") or "")[:400],
                             "color": 0x57F287}
                    await rest.post(f"/channels/{f['channel_id']}/messages",
                                    json={"embeds": [embed]})
                    await db.pool.execute(
                        "UPDATE feeds SET last_seen=$2, last_polled_at=NOW() WHERE id=$1",
                        f["id"], guid)
                except Exception: pass
        except Exception: pass
