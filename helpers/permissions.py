"""Database-backed permission checks for raw Discord payloads."""

from collections.abc import Mapping
import re
from typing import Any

import asyncpg


# Keywords identifying admin/owner roles regardless of prefix, suffix, or decorations
ADMIN_KEYWORDS = {
    "admin", "administrator", "admins", "administrators",
    "owner", "owners", "coowner", "coowners",
    "founder", "founders",
}
ADMIN_SUBSTRINGS = ("admin", "owner", "founder")

# In-memory cache of configured admin role IDs: { guild_id: {role_id, ...} }
_cached_admin_roles: dict[int, set[int]] = {}


def is_admin_role_name(name: str | None) -> bool:
    """Check if a role name indicates admin or owner status (handles prefixes, suffixes, symbols)."""
    if not name:
        return False
    tokens = re.findall(r"[a-zA-Z0-9]+", name.lower())
    return any(
        token in ADMIN_KEYWORDS or any(sub in token for sub in ADMIN_SUBSTRINGS)
        for token in tokens
    )


def add_cached_admin_role(guild_id: int, role_id: int) -> None:
    _cached_admin_roles.setdefault(int(guild_id), set()).add(int(role_id))


def remove_cached_admin_role(guild_id: int, role_id: int) -> None:
    if int(guild_id) in _cached_admin_roles:
        _cached_admin_roles[int(guild_id)].discard(int(role_id))


async def sync_admin_roles(db_pool: asyncpg.Pool | None) -> None:
    """Preload all configured admin roles into memory from the database."""
    if not db_pool:
        return
    try:
        rows = await db_pool.fetch(
            "SELECT guild_id, role_id FROM guild_staff_roles WHERE role_type = 'admin' AND is_staff = TRUE"
        )
        _cached_admin_roles.clear()
        for row in rows:
            add_cached_admin_role(row["guild_id"], row["role_id"])
    except Exception:
        pass


def _member_role_ids(ctx: Any) -> set[int]:
    """Extract member role IDs from ctx.member and fallback to gateway cache."""
    roles = set()
    if getattr(ctx, "member", None):
        for r in ctx.member.get("roles", []):
            try:
                roles.add(int(r))
            except (TypeError, ValueError):
                pass
    if getattr(ctx, "guild_id", None) and getattr(ctx, "user_id", None) and getattr(ctx, "cache", None):
        cached_mem = ctx.cache.members.get((int(ctx.guild_id), int(ctx.user_id))) or {}
        for r in cached_mem.get("roles", []):
            try:
                roles.add(int(r))
            except (TypeError, ValueError):
                pass
    return roles


def is_bot_owner(ctx: Any) -> bool:
    """Check if user is configured in Config.OWNER_IDS."""
    if not ctx.user_id:
        return False
    try:
        from config import Config
        uid = int(ctx.user_id)
        return uid in Config.OWNER_IDS or uid in getattr(ctx.cache, "owner_ids", set())
    except Exception:
        return False


def is_guild_owner(ctx: Any) -> bool:
    """Check the owner ID cached from Discord's GUILD_CREATE payload."""
    if not ctx.guild_id or not ctx.user_id:
        return False
    guild = ctx.cache.guilds.get(int(ctx.guild_id), {})
    return str(guild.get("owner_id", "")) == str(ctx.user_id)


async def check_is_guild_owner(ctx: Any) -> bool:
    """Check owner with API fallback if gateway cache is cold."""
    if not ctx.guild_id or not ctx.user_id:
        return False
    if is_guild_owner(ctx):
        return True
    try:
        g = await ctx.rest.get(f"/guilds/{ctx.guild_id}")
        if g and g.get("owner_id"):
            ctx.cache.guilds.setdefault(int(ctx.guild_id), {})["owner_id"] = str(g["owner_id"])
            return str(g["owner_id"]) == str(ctx.user_id)
    except Exception:
        pass
    return False


def is_administrator(ctx: Any) -> bool:
    """Check Discord's Administrator permission (server context only)."""
    if not ctx.guild_id or not ctx.user_id:
        return False
    permissions = ctx.member.get("permissions") if getattr(ctx, "member", None) else None
    if permissions is not None and bool(int(permissions or 0) & (1 << 3)):
        return True
    gid = int(ctx.guild_id)
    perms = int(ctx.cache.member_permissions(gid, ctx.user_id) or 0)
    for role_id in _member_role_ids(ctx):
        role = ctx.cache.roles.get((gid, role_id))
        if role:
            perms |= int(role.get("permissions", "0"))
    return bool(perms & (1 << 3))


def has_admin_role_by_name(ctx: Any) -> bool:
    """Check if member has any role whose name matches admin/owner (prefix/suffix tolerant)."""
    if not ctx.guild_id:
        return False
    gid = int(ctx.guild_id)
    for role_id in _member_role_ids(ctx):
        role = ctx.cache.roles.get((gid, role_id))
        if role and is_admin_role_name(role.get("name")):
            return True
    return False


def has_configured_admin_role(ctx: Any) -> bool:
    """Check if member has a role configured as admin in guild_staff_roles."""
    if not ctx.guild_id:
        return False
    gid = int(ctx.guild_id)
    member_roles = _member_role_ids(ctx)
    configured = _cached_admin_roles.get(gid, set())
    return bool(member_roles & configured)


def can_manage_bot(ctx: Any) -> bool:
    """Allow if user is bot owner, server owner, Discord admin, configured admin role, or has an admin/owner-named role."""
    if not ctx.guild_id:
        return False
    return bool(
        is_bot_owner(ctx)
        or is_guild_owner(ctx)
        or is_administrator(ctx)
        or has_configured_admin_role(ctx)
        or has_admin_role_by_name(ctx)
    )


async def can_manage_bot_async(ctx: Any, db_pool: asyncpg.Pool | None = None) -> bool:
    """Async check for Admin / Owner roles with DB and REST cache warm-up fallback."""
    if not ctx.guild_id:
        return False
    if can_manage_bot(ctx):
        return True
    if await check_is_guild_owner(ctx):
        return True

    gid = int(ctx.guild_id)
    member_roles = _member_role_ids(ctx)

    # If member has roles not yet in cache.roles, fetch guild roles once
    if member_roles and any((gid, rid) not in ctx.cache.roles for rid in member_roles):
        try:
            roles = await ctx.rest.get(f"/guilds/{gid}/roles")
            if isinstance(roles, list):
                for item in roles:
                    ctx.cache.roles[(gid, int(item["id"]))] = item
        except Exception:
            pass
        if is_administrator(ctx) or has_admin_role_by_name(ctx):
            return True

    # Check configured admin roles in DB
    if member_roles and db_pool:
        try:
            rows = await db_pool.fetch(
                """
                SELECT role_id FROM guild_staff_roles
                WHERE guild_id = $1 AND is_staff = TRUE AND role_type = 'admin'
                """,
                gid,
            )
            for row in rows:
                add_cached_admin_role(gid, int(row["role_id"]))
            if has_configured_admin_role(ctx):
                return True
        except Exception:
            pass

    return False



async def is_staff(
    guild_id: int | str | None,
    member_data: Mapping[str, Any] | None,
    db_pool: asyncpg.Pool | None,
) -> bool:
    """Return whether a member has an enabled staff role in ``guild_id``."""
    if guild_id is None or member_data is None or db_pool is None:
        return False

    try:
        member_role_ids = {int(role_id) for role_id in member_data.get("roles", [])}
        if not member_role_ids:
            return False

        configured_role_ids = await db_pool.fetch(
            """
            SELECT role_id
            FROM guild_staff_roles
            WHERE guild_id = $1 AND is_staff = TRUE
            """,
            int(guild_id),
        )
    except (TypeError, ValueError):
        return False

    return any(int(row["role_id"]) in member_role_ids for row in configured_role_ids)


async def can_use_staff_commands(ctx: Any, db_pool: asyncpg.Pool | None) -> bool:
    """Allow the guild owner, administrators, or a configured staff role."""
    if not ctx.guild_id:
        return False
    cache_key = "_can_use_staff_commands"
    if hasattr(ctx, cache_key):
        return getattr(ctx, cache_key)

    # Any administrator or bot owner is automatically authorized for staff commands
    allowed = (
        await can_manage_bot_async(ctx, db_pool)
        or await is_staff(ctx.guild_id, ctx.member, db_pool)
    )
    setattr(ctx, cache_key, allowed)
    return allowed
