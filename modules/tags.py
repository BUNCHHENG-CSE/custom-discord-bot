"""Custom tags / FAQs system."""

from database import db
from helpers.permissions import can_use_staff_commands
from interactions import Interaction, STR, slash, subcommand


@subcommand("tag", "Custom tag system", "get", "Display a saved tag", [
    {"name": "name", "description": "Tag name", "type": STR, "required": True},
])
async def tag_get(i: Interaction, name: str):
    name = name.strip().lower()
    row = await db.pool.fetchrow(
        "SELECT content FROM tags WHERE guild_id=$1 AND LOWER(name)=$2",
        int(i.guild_id), name
    )
    if not row:
        return await i.respond(f"❌ Tag `{name}` not found. Use `tct!taglist` to view all tags.", ephemeral=True)
    await i.respond(row["content"])


@subcommand("tag", "Custom tag system", "add", "Create or edit a server tag", [
    {"name": "name", "description": "Tag name", "type": STR, "required": True},
    {"name": "content", "description": "Tag content", "type": STR, "required": True},
])
async def tag_add(i: Interaction, name: str, content: str):
    if not await can_use_staff_commands(i, db.pool):
        return await i.respond("❌ Only configured staff or administrators can create tags.", ephemeral=True)
    name = name.strip().lower()
    if not name or len(name) > 32:
        return await i.respond("❌ Tag name must be between 1 and 32 characters.", ephemeral=True)
    if not content:
        return await i.respond("❌ Tag content cannot be empty.", ephemeral=True)

    await db.ensure_guild(i.guild_id)
    await db.pool.execute(
        """
        INSERT INTO tags (guild_id, name, content, author_id, created_at)
        VALUES ($1, $2, $3, $4, NOW())
        ON CONFLICT (guild_id, name)
        DO UPDATE SET content = $3, author_id = $4, created_at = NOW()
        """,
        int(i.guild_id), name, content, int(i.user_id),
    )
    await i.respond(f"✅ Tag `{name}` saved.")


@subcommand("tag", "Custom tag system", "delete", "Delete a server tag", [
    {"name": "name", "description": "Tag name", "type": STR, "required": True},
])
async def tag_del(i: Interaction, name: str):
    if not await can_use_staff_commands(i, db.pool):
        return await i.respond("❌ Only configured staff or administrators can delete tags.", ephemeral=True)
    name = name.strip().lower()
    result = await db.pool.execute(
        "DELETE FROM tags WHERE guild_id=$1 AND LOWER(name)=$2",
        int(i.guild_id), name
    )
    if result == "DELETE 0":
        return await i.respond(f"❌ Tag `{name}` does not exist.", ephemeral=True)
    await i.respond(f"✅ Tag `{name}` deleted.")


@subcommand("tag", "Custom tag system", "list", "List all tags in this server")
async def tag_list(i: Interaction):
    rows = await db.pool.fetch(
        "SELECT name FROM tags WHERE guild_id=$1 ORDER BY name ASC",
        int(i.guild_id)
    )
    if not rows:
        return await i.respond("ℹ️ No tags found in this server. Create one with `tct!tagadd <name> <content>`.")
    tag_names = [f"`{r['name']}`" for r in rows]
    embed = {
        "title": f"🏷️ Server Tags ({len(rows)})",
        "description": ", ".join(tag_names),
        "color": 0x5865F2,
        "footer": {"text": "Use tct!tag <name> to view any tag"},
    }
    await i.respond(embeds=[embed])
