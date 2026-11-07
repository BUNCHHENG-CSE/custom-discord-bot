"""Automatic join roles and reaction-role event handling."""

from urllib.parse import quote

from database import db
from helpers.permissions import can_manage_bot
from interactions import Interaction, MessageContext, ROLE, STR, slash


def _payload_emoji(emoji: dict) -> str:
    """Convert a Gateway emoji object to Discord's reaction identifier."""
    return f"{emoji['name']}:{emoji['id']}" if emoji.get("id") else emoji["name"]


def _command_emoji(value: str) -> str:
    """Accept Unicode or custom emoji mentions such as <:python:123>."""
    value = value.strip()
    if value.startswith("<") and value.endswith(">"):
        parts = value[1:-1].split(":")
        if len(parts) == 3 and parts[0] in {"", "a"} and parts[1] and parts[2].isdigit():
            return f"{parts[1]}:{parts[2]}"
    if value:
        return value
    raise ValueError("Emoji cannot be empty.")


def _require_mentioned_role(i: Interaction, role: str) -> bool:
    """Prefix setup commands must contain a real Discord role mention."""
    return not isinstance(i, MessageContext) or str(role) in i.raw.get("mention_roles", [])


async def _find_message_channel(i: Interaction, message_id: int) -> int | None:
    """Find a message in the current guild, starting with the command channel."""
    candidates = [int(i.channel_id)]
    guild = i.cache.guilds.get(int(i.guild_id), {})
    for channel in guild.get("channels", []):
        # Normal text and announcement channels can contain role-panel messages.
        if channel.get("type") in (0, 5):
            channel_id = int(channel["id"])
            if channel_id not in candidates:
                candidates.append(channel_id)

    for channel_id in candidates:
        try:
            await i.rest.get(f"/channels/{channel_id}/messages/{message_id}")
            return channel_id
        except RuntimeError as exc:
            if str(exc).startswith(("403 ", "404 ")):
                continue
            raise
    return None


@slash("autorole", "Configure a role automatically assigned to new members", [
    {"name": "role", "description": "Role to assign, or off", "type": STR, "required": True},
])
async def autorole(i: Interaction, value):
    if not can_manage_bot(i):
        return await i.respond("❌ Only the server owner or an administrator can configure autoroles.", ephemeral=True)

    if value.lower() in {"off", "disable", "clear"}:
        await db.pool.execute("DELETE FROM guild_autoroles WHERE guild_id = $1", int(i.guild_id))
        return await i.respond("✅ Autoroles disabled for this server.")

    mentioned = i.raw.get("mention_roles", [])
    if not mentioned:
        return await i.respond("❌ Mention a role. Usage: `tct!autorole @Member`")
    role = mentioned[0]
    if int(role) == int(i.guild_id):
        return await i.respond("❌ `@everyone` cannot be used as an autorole.")

    await db.ensure_guild(i.guild_id)
    # This command sets the server's join role. Running it again replaces the
    # previous role, which keeps setup predictable for server administrators.
    async with db.pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                "DELETE FROM guild_autoroles WHERE guild_id = $1", int(i.guild_id)
            )
            await connection.execute(
                "INSERT INTO guild_autoroles (guild_id, role_id) VALUES ($1, $2)",
                int(i.guild_id), int(role),
            )
    await i.respond(f"✅ New members will automatically receive <@&{role}>.")


@slash("reactionrole", "Connect a message reaction to a role", [
    {"name": "message_id", "description": "Message ID", "type": STR, "required": True},
    {"name": "emoji", "description": "Unicode or custom emoji", "type": STR, "required": True},
    {"name": "role", "description": "Role to assign", "type": ROLE, "required": True},
])
async def reactionrole(i: Interaction, message_id, emoji, role):
    if not can_manage_bot(i):
        return await i.respond("❌ Only the server owner or an administrator can configure reaction roles.", ephemeral=True)
    if not _require_mentioned_role(i, role):
        return await i.respond("❌ Mention a role. Usage: `tct!reactionrole <message_id> <emoji> @Role`")
    if int(role) == int(i.guild_id):
        return await i.respond("❌ `@everyone` cannot be used as a reaction role.")

    emoji_key = _command_emoji(emoji)
    target_channel = await _find_message_channel(i, int(message_id))
    if target_channel is None:
        return await i.respond(
            "❌ I couldn't find that message in this server. Check the message ID "
            "and make sure I can **View Channel** and **Read Message History** there."
        )
    await db.pool.execute(
        """
        INSERT INTO reaction_roles (guild_id, channel_id, message_id, emoji, role_id)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (guild_id, message_id, emoji)
        DO UPDATE SET channel_id = EXCLUDED.channel_id, role_id = EXCLUDED.role_id
        """,
        int(i.guild_id), target_channel, int(message_id), emoji_key, int(role),
    )
    try:
        await i.rest.put(
            f"/channels/{target_channel}/messages/{message_id}/reactions/{quote(emoji_key, safe='')}/@me"
        )
    except RuntimeError as exc:
        await db.pool.execute(
            "DELETE FROM reaction_roles WHERE guild_id=$1 AND message_id=$2 AND emoji=$3",
            int(i.guild_id), int(message_id), emoji_key,
        )
        error = str(exc)
        if "Unknown Emoji" in error or '"code": 10014' in error:
            return await i.respond(
                "❌ Discord does not recognize that emoji, or the bot cannot use it. "
                "Try a normal Unicode emoji or an emoji available to the bot."
            )
        if error.startswith("403 "):
            return await i.respond(
                "❌ I need **View Channel**, **Read Message History**, and "
                "**Add Reactions** permissions in the target channel."
            )
        if error.startswith("404 "):
            return await i.respond("❌ The target message or channel no longer exists.")
        raise
    await i.respond(f"✅ Reacting with {emoji} on message `{message_id}` now gives <@&{role}>.")


@slash("removereactionrole", "Remove a reaction-role mapping", [
    {"name": "message_id", "description": "Message ID", "type": STR, "required": True},
    {"name": "emoji", "description": "Unicode or custom emoji", "type": STR, "required": True},
])
async def remove_reactionrole(i: Interaction, message_id, emoji):
    if not can_manage_bot(i):
        return await i.respond("❌ Only the server owner or an administrator can configure reaction roles.", ephemeral=True)
    emoji_key = _command_emoji(emoji)
    deleted = await db.pool.fetchrow(
        "DELETE FROM reaction_roles WHERE guild_id=$1 AND message_id=$2 AND emoji=$3 "
        "RETURNING channel_id",
        int(i.guild_id), int(message_id), emoji_key,
    )
    if not deleted:
        return await i.respond("ℹ️ That reaction role was not configured.")
    try:
        await i.rest.delete(
            f"/channels/{deleted['channel_id']}/messages/{message_id}/reactions/{quote(emoji_key, safe='')}/@me"
        )
    except Exception:
        pass
    await i.respond("✅ Reaction-role mapping removed.")


@slash("reactionroles", "List reaction roles configured in this server")
async def list_reactionroles(i: Interaction):
    if not can_manage_bot(i):
        return await i.respond("❌ Only the server owner or an administrator can view this configuration.", ephemeral=True)
    rows = await db.pool.fetch(
        "SELECT channel_id, message_id, emoji, role_id FROM reaction_roles "
        "WHERE guild_id=$1 ORDER BY channel_id, message_id, emoji",
        int(i.guild_id),
    )
    if not rows:
        return await i.respond("No reaction roles configured.")
    lines = [
        f"{row['emoji']} → <@&{row['role_id']}> — "
        f"[message](https://discord.com/channels/{i.guild_id}/{row['channel_id']}/{row['message_id']})"
        for row in rows[:30]
    ]
    await i.respond("**Reaction roles:**\n" + "\n".join(lines))


async def assign_autoroles(rest, member: dict) -> None:
    """Assign all configured autoroles for a GUILD_MEMBER_ADD payload."""
    if member.get("user", {}).get("bot"):
        return
    guild_id = int(member["guild_id"])
    user_id = int(member["user"]["id"])
    rows = await db.pool.fetch(
        "SELECT role_id FROM guild_autoroles WHERE guild_id=$1", guild_id
    )
    for row in rows:
        await rest.put(
            f"/guilds/{guild_id}/members/{user_id}/roles/{row['role_id']}",
            reason="Configured automatic member role",
        )


async def handle_reaction(rest, payload: dict, *, added: bool) -> None:
    """Grant or remove a configured role for a raw reaction event."""
    if not payload.get("guild_id"):
        return
    guild_id = int(payload["guild_id"])
    row = await db.pool.fetchrow(
        """
        SELECT role_id FROM reaction_roles
        WHERE guild_id=$1 AND channel_id=$2 AND message_id=$3 AND emoji=$4
        """,
        guild_id,
        int(payload["channel_id"]),
        int(payload["message_id"]),
        _payload_emoji(payload["emoji"]),
    )
    if not row:
        return
    path = f"/guilds/{guild_id}/members/{payload['user_id']}/roles/{row['role_id']}"
    if added:
        await rest.put(path, reason="Reaction role added")
    else:
        await rest.delete(path, reason="Reaction role removed")
