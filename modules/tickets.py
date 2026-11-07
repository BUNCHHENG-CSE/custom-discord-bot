import asyncio, io, json, aiohttp
from interactions import slash, subcommand, Interaction, STR, USER
from database import db
from helpers.permissions import can_manage_bot, can_use_staff_commands

# Components: type 1=ActionRow, 2=Button, 3=StringSelect
def _panel_components(panel):
    return [{"type": 1, "components": [
        {"type": 2, "style": 1, "label": "Open Ticket",
         "emoji": {"name": "🎫"}, "custom_id": f"ticket:open:{panel}"}
    ]}]

def _ticket_controls(claimed_by_name: str | None = None):
    claim_btn = {
        "type": 2,
        "style": 2 if claimed_by_name else 1,
        "label": f"Claimed by {claimed_by_name}" if claimed_by_name else "Claim Ticket",
        "emoji": {"name": "🙋‍♂️"},
        "custom_id": "ticket:claim",
        "disabled": bool(claimed_by_name),
    }
    close_btn = {
        "type": 2,
        "style": 4,
        "label": "Close",
        "emoji": {"name": "🔒"},
        "custom_id": "ticket:close",
    }
    return [{"type": 1, "components": [claim_btn, close_btn]}]

@subcommand("ticket", "Ticket system", "panel", "Post a ticket panel here", [
    {"name": "panel", "description": "Panel id", "type": STR, "required": False},
])
async def panel(i: Interaction, panel="default"):
    if not can_manage_bot(i):
        return await i.respond("❌ Only the server owner or an administrator can post a panel.", ephemeral=True)
    embed = {"title": "Support Tickets", "description": "Click below to open a ticket.",
             "color": 0x5865F2}
    await i.rest.post(f"/channels/{i.channel_id}/messages",
                      json={"embeds": [embed], "components": _panel_components(panel)})
    await i.respond("✅ Panel posted.", ephemeral=True)

@subcommand("ticket", "Ticket system", "claim", "Claim this ticket")
async def claim(i: Interaction):
    row = await db.pool.fetchrow("SELECT * FROM tickets WHERE channel_id=$1 AND status='open'",
                                  i.channel_id)
    if not row:
        return await i.respond("❌ This is not an open ticket channel.", ephemeral=True)
    if not await can_use_staff_commands(i, db.pool):
        return await i.respond("❌ Only configured staff can claim tickets.", ephemeral=True)
    if row["claimed_by"]:
        return await i.respond(f"ℹ️ Ticket already claimed by <@{row['claimed_by']}>.", ephemeral=True)

    await db.pool.execute("UPDATE tickets SET claimed_by=$1 WHERE id=$2", int(i.user_id), row["id"])
    staff_name = i.user.get("username", "Staff")
    await i.respond(f"🙋‍♂️ Ticket claimed by <@{i.user_id}>.")

    # Try updating control message buttons if found
    try:
        msgs = await i.rest.get(f"/channels/{i.channel_id}/messages", params={"limit": 5})
        for m in msgs:
            if m.get("components"):
                await i.rest.patch(f"/channels/{i.channel_id}/messages/{m['id']}",
                                   json={"components": _ticket_controls(staff_name)})
                break
    except Exception:
        pass

@subcommand("ticket", "Ticket system", "close", "Close this ticket")
async def close(i: Interaction):
    row = await db.pool.fetchrow("SELECT * FROM tickets WHERE channel_id=$1 AND status='open'",
                                  i.channel_id)
    if not row:
        return await i.respond("❌ Not an open ticket channel.", ephemeral=True)
    if int(row["user_id"]) != int(i.user_id) and not await can_use_staff_commands(i, db.pool):
        return await i.respond("❌ Only the ticket owner or configured staff can close it.", ephemeral=True)
    await i.respond("🔒 Closing ticket in 5s...")

    # Fetch transcript
    msgs = await i.rest.get(f"/channels/{i.channel_id}/messages", params={"limit": 100})
    lines = [f"[{m.get('timestamp','')}] {m.get('author',{}).get('username','?')}: {m.get('content','')}"
             for m in reversed(msgs)]
    text = "\n".join(lines)

    # Post transcript file to guild log_channel if configured
    cfg = await db.get_guild(row["guild_id"])
    log_ch = cfg.get("log_channel") if cfg else None
    if log_ch:
        try:
            form = aiohttp.FormData()
            claimed_str = f"<@{row['claimed_by']}>" if row.get("claimed_by") else "Unclaimed"
            embed = {
                "title": f"📋 Ticket Transcript: ticket-{row['id']}",
                "description": f"**Owner:** <@{row['user_id']}>\n**Closed by:** <@{i.user_id}>\n**Claimed by:** {claimed_str}",
                "color": 0x5865F2,
            }
            form.add_field("payload_json", json.dumps({"embeds": [embed]}), content_type="application/json")
            form.add_field("files[0]", text.encode("utf-8"), filename=f"ticket-{row['id']}-transcript.txt", content_type="text/plain")
            await i.rest.post(f"/channels/{log_ch}/messages", data=form)
        except Exception:
            pass

    # Try DM to owner
    try:
        dm = await i.rest.post("/users/@me/channels", json={"recipient_id": str(row["user_id"])})
        await i.rest.post(f"/channels/{dm['id']}/messages",
                          json={"content": f"📋 Your ticket in server `{row['guild_id']}` was closed."})
    except Exception:
        pass

    await db.pool.execute("UPDATE tickets SET status='closed', closed_at=NOW() WHERE id=$1", row["id"])
    await asyncio.sleep(5)
    try:
        await i.rest.delete(f"/channels/{i.channel_id}")
    except Exception:
        pass

@subcommand("ticket", "Ticket system", "add", "Add a user to this ticket", [
    {"name": "member", "description": "Member", "type": USER, "required": True},
])
async def add_user(i: Interaction, member):
    if not await can_use_staff_commands(i, db.pool):
        return await i.respond("❌ This command is limited to configured staff.", ephemeral=True)
    await i.rest.put(f"/channels/{i.channel_id}/permissions/{member}",
                     json={"type": 1, "allow": str((1<<10)|(1<<11)), "deny": "0"})
    await i.respond(f"✅ Added <@{member}> to ticket.")


@subcommand("ticket", "Ticket system", "remove", "Remove a user from this ticket", [
    {"name": "member", "description": "Member", "type": USER, "required": True},
])
async def remove_user(i: Interaction, member):
    if not await can_use_staff_commands(i, db.pool):
        return await i.respond("❌ This command is limited to configured staff.", ephemeral=True)
    row = await db.pool.fetchrow("SELECT * FROM tickets WHERE channel_id=$1 AND status='open'", i.channel_id)
    if not row:
        return await i.respond("❌ This is not an open ticket channel.", ephemeral=True)
    target_id = str(member).strip().strip("<@!>")
    try:
        await i.rest.delete(f"/channels/{i.channel_id}/permissions/{target_id}")
    except Exception as e:
        return await i.respond(f"❌ Failed to remove user: `{e}`", ephemeral=True)
    await i.respond(f"✅ Removed <@{target_id}> from ticket.")


@subcommand("ticket", "Ticket system", "rename", "Rename this ticket channel", [
    {"name": "name", "description": "New ticket channel name", "type": STR, "required": True},
])
async def rename_ticket(i: Interaction, name: str = ""):
    if not await can_use_staff_commands(i, db.pool):
        return await i.respond("❌ This command is limited to configured staff.", ephemeral=True)
    row = await db.pool.fetchrow("SELECT * FROM tickets WHERE channel_id=$1 AND status='open'", i.channel_id)
    if not row:
        return await i.respond("❌ This is not an open ticket channel.", ephemeral=True)
    clean_name = name.strip().lower().replace(" ", "-")[:90]
    if not clean_name:
        return await i.respond("❌ Provide a valid channel name.", ephemeral=True)
    try:
        await i.rest.patch(f"/channels/{i.channel_id}", json={"name": clean_name})
    except Exception as e:
        return await i.respond(f"❌ Failed to rename ticket: `{e}`", ephemeral=True)
    await i.respond(f"✏️ Renamed ticket to **#{clean_name}**.")


# ---- Component handler (called from main for type=3 interactions) ----
async def handle_component(i: Interaction):
    cid = i.custom_id or ""
    if cid.startswith("ticket:open:"):
        panel = cid.split(":", 2)[2]
        existing = await db.pool.fetchrow(
            "SELECT * FROM tickets WHERE guild_id=$1 AND user_id=$2 AND status='open'",
            i.guild_id, i.user_id)
        if existing:
            return await i.respond(f"You already have a ticket: <#{existing['channel_id']}>",
                                    ephemeral=True)
        # create channel
        body = {"name": f"ticket-{i.user['username'][:20]}", "type": 0,
                "permission_overwrites": [
                    {"id": str(i.guild_id), "type": 0, "deny": str(1<<10), "allow": "0"},
                    {"id": str(i.user_id), "type": 1, "allow": str((1<<10)|(1<<11)|(1<<16)), "deny": "0"},
                ]}
        ch = await i.rest.post(f"/guilds/{i.guild_id}/channels", json=body)
        await db.pool.execute(
            "INSERT INTO tickets (guild_id,channel_id,user_id,panel) VALUES ($1,$2,$3,$4)",
            int(i.guild_id), int(ch["id"]), int(i.user_id), panel)
        await i.rest.post(f"/channels/{ch['id']}/messages",
                          json={"content": f"👋 Welcome <@{i.user_id}>! Staff will assist you shortly.",
                                "components": _ticket_controls()})
        await i.respond(f"✅ Ticket created: <#{ch['id']}>", ephemeral=True)
    elif cid == "ticket:claim":
        await claim(i)
    elif cid == "ticket:close":
        await close(i)

