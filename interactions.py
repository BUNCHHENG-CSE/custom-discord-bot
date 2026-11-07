import asyncio
import json
import aiohttp
from typing import Callable, Any

# --- Slash command registry ---
_specs: list[dict] = []
_handlers: dict[str, Callable] = {}

# Discord option types
STR, INT, BOOL, USER, CHANNEL, ROLE, MENTIONABLE, NUMBER = 3, 4, 5, 6, 7, 8, 9, 10
SUB_COMMAND, SUB_GROUP = 1, 2

def slash(name: str, description: str, options: list[dict] | None = None):
    """Register a top-level slash command. `options` follow Discord API format."""
    def deco(fn):
        _specs.append({"name": name, "description": description, "options": options or []})
        _handlers[name] = fn
        return fn
    return deco

def subcommand(group_name: str, group_desc: str, name: str, description: str,
               options: list[dict] | None = None):
    """Register a subcommand under an existing group (creates group if missing)."""
    def deco(fn):
        grp = next((s for s in _specs if s["name"] == group_name), None)
        if not grp:
            grp = {"name": group_name, "description": group_desc,
                   "options": [{"type": SUB_COMMAND, "name": name,
                                "description": description, "options": options or []}]}
            _specs.append(grp)
        else:
            grp["options"].append({"type": SUB_COMMAND, "name": name,
                                   "description": description, "options": options or []})
        _handlers[f"{group_name}:{name}"] = fn
        return fn
    return deco

def all_specs(): return _specs

def resolve_handler(full_name: str): return _handlers.get(full_name)


# --- Interaction wrapper ---
class Interaction:
    def __init__(self, rest, data: dict, cache):
        self.rest = rest
        self.raw = data
        self.cache = cache
        self.id = data["id"]
        self.token = data["token"]
        self.app_id = data["application_id"]
        self.type = data["type"]
        self.guild_id = int(data["guild_id"]) if data.get("guild_id") else None
        self.channel_id = int(data["channel_id"]) if data.get("channel_id") else None
        self.member = data.get("member") or {}
        self.user = self.member.get("user") or data.get("user") or {}
        self.user_id = int(self.user["id"]) if self.user.get("id") else None
        self._responded = False
        self.custom_id = None  # for component interactions

        if self.type == 2:  # APPLICATION_COMMAND
            self.data = data["data"]
            self.name = self.data["name"]
            self.opts, self.sub = self._parse(self.data.get("options", []))
        elif self.type == 3:  # MESSAGE_COMPONENT
            self.data = data["data"]
            self.custom_id = self.data.get("custom_id")
            self.component_type = self.data.get("component_type")
            self.values = self.data.get("values", [])
            self.message = data.get("message") or self.data.get("message") or {}
        else:
            self.data = data.get("data") or {}

    def _parse(self, options):
        result, sub = {}, None
        for o in options:
            if o["type"] in (SUB_COMMAND, SUB_GROUP):
                sub = o["name"]
                inner, _ = self._parse(o.get("options", []))
                result.update(inner)
            else:
                result[o["name"]] = o.get("value")
        return result, sub

    # ---- Responses ----
    async def respond(self, content=None, *, embeds=None, ephemeral=False,
                      components=None, allowed_mentions=None, files=None):
        data: dict[str, Any] = {"content": content}
        if embeds: data["embeds"] = embeds
        if components: data["components"] = components
        if allowed_mentions: data["allowed_mentions"] = allowed_mentions
        if ephemeral: data["flags"] = 64

        if files:
            form = aiohttp.FormData()
            form.add_field("payload_json", json.dumps({"type": 4, "data": data}), content_type="application/json")
            for idx, item in enumerate(files):
                fname, fbytes, ctype = item if len(item) == 3 else (item[0], item[1], "application/octet-stream")
                form.add_field(f"files[{idx}]", fbytes, filename=fname, content_type=ctype)
            await self.rest.post(f"/interactions/{self.id}/{self.token}/callback", data=form)
        else:
            payload = {"type": 4, "data": data}
            await self.rest.post(f"/interactions/{self.id}/{self.token}/callback", json=payload)
        self._responded = True

    async def update(self, content=None, *, embeds=None, components=None):
        """Update the message a component belongs to."""
        data: dict[str, Any] = {}
        if content is not None: data["content"] = content
        if embeds is not None: data["embeds"] = embeds
        if components is not None: data["components"] = components
        await self.rest.post(f"/interactions/{self.id}/{self.token}/callback",
                             json={"type": 7, "data": data})
        self._responded = True

    async def defer(self, *, ephemeral=False, update=False):
        payload = {"type": 6 if update else 5}
        if ephemeral: payload["data"] = {"flags": 64}
        await self.rest.post(f"/interactions/{self.id}/{self.token}/callback", json=payload)
        self._responded = True

    async def followup(self, content=None, *, embeds=None, ephemeral=False, components=None, files=None):
        data: dict[str, Any] = {}
        if content is not None: data["content"] = content
        if embeds: data["embeds"] = embeds
        if components: data["components"] = components
        if ephemeral: data["flags"] = 64

        if files:
            form = aiohttp.FormData()
            form.add_field("payload_json", json.dumps(data), content_type="application/json")
            for idx, item in enumerate(files):
                fname, fbytes, ctype = item if len(item) == 3 else (item[0], item[1], "application/octet-stream")
                form.add_field(f"files[{idx}]", fbytes, filename=fname, content_type=ctype)
            return await self.rest.post(f"/webhooks/{self.app_id}/{self.token}", data=form)
        return await self.rest.post(f"/webhooks/{self.app_id}/{self.token}", json=data)

    # ---- Permission helper ----
    def has_permission(self, perm: str) -> bool:
        """perm: 'ban_members', 'kick_members', 'manage_guild', 'moderate_members', ..."""
        if not self.guild_id:
            return True
        if int(self.user_id) in self.cache.owner_ids:
            return True
        # Administrators have all perms
        perms = int(self.cache.member_permissions(self.guild_id, self.user_id))
        ADMIN = 1 << 3
        if perms & ADMIN: return True
        return bool(perms & _PERM_BITS[perm])


class MessageContext:
    """Interaction-compatible wrapper for commands typed in a text channel."""

    def __init__(self, rest, message: dict, cache):
        self.rest = rest
        self.raw = message
        self.cache = cache
        self.id = message["id"]
        self.guild_id = int(message["guild_id"]) if message.get("guild_id") else None
        self.channel_id = int(message["channel_id"])
        self.member = message.get("member") or {}
        self.user = message["author"]
        self.user_id = int(self.user["id"])
        self.app_id = None
        self.token = None
        self._responded = False

    async def respond(self, content=None, *, embeds=None, ephemeral=False,
                      components=None, allowed_mentions=None, files=None):
        data: dict[str, Any] = {}
        if content is not None:
            data["content"] = content
        if embeds:
            data["embeds"] = embeds
        if components:
            data["components"] = components
        if allowed_mentions:
            data["allowed_mentions"] = allowed_mentions

        if files:
            form = aiohttp.FormData()
            form.add_field("payload_json", json.dumps(data), content_type="application/json")
            for idx, item in enumerate(files):
                fname, fbytes, ctype = item if len(item) == 3 else (item[0], item[1], "application/octet-stream")
                form.add_field(f"files[{idx}]", fbytes, filename=fname, content_type=ctype)
            result = await self.rest.post(f"/channels/{self.channel_id}/messages", data=form)
        else:
            result = await self.rest.post(f"/channels/{self.channel_id}/messages", json=data)
        self._responded = True
        return result

    async def followup(self, content=None, *, embeds=None, ephemeral=False,
                       components=None, files=None):
        return await self.respond(content, embeds=embeds, components=components, files=files)

    async def defer(self, *, ephemeral=False, update=False):
        # Message commands do not have Discord's three-second interaction limit.
        return None

    def has_permission(self, perm: str) -> bool:
        if not self.guild_id:
            return True
        if int(self.user_id) in self.cache.owner_ids:
            return True
        perms = int(self.cache.member_permissions(self.guild_id, self.user_id))
        return bool(perms & (1 << 3) or perms & _PERM_BITS[perm])


_PERM_BITS = {
    "administrator": 1 << 3,
    "ban_members": 1 << 2, "kick_members": 1 << 1,
    "manage_channels": 1 << 4, "manage_guild": 1 << 5,
    "manage_messages": 1 << 13, "manage_roles": 1 << 28,
    "moderate_members": 1 << 40,
}
