"""Welcome and goodbye messages with full server-level customization."""

import json
import re
from typing import Any

from database import db
from helpers.permissions import can_manage_bot
from interactions import Interaction


BLURPLE = 0x5865F2
RED = 0xED4245

COLOR_NAMES = {
    "blurple": 0x5865F2,
    "red": 0xED4245,
    "green": 0x57F287,
    "yellow": 0xFEE75C,
    "gold": 0xF1C40F,
    "purple": 0x9B59B6,
    "pink": 0xEB459E,
    "fuchsia": 0xEB459E,
    "blue": 0x3498DB,
    "cyan": 0x1ABC9C,
    "aqua": 0x1ABC9C,
    "orange": 0xE67E22,
    "white": 0xFFFFFF,
    "black": 0x23272A,
    "dark": 0x2B2D31,
}


def _avatar_url(user: dict) -> str:
    user_id = int(user["id"])
    avatar = user.get("avatar")
    if avatar:
        extension = "gif" if avatar.startswith("a_") else "png"
        return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.{extension}?size=256"
    discriminator = user.get("discriminator", "0")
    index = int(discriminator) % 5 if discriminator != "0" else (user_id >> 22) % 6
    return f"https://cdn.discordapp.com/embed/avatars/{index}.png"


def _guild_icon_url(guild_id: int, icon: str | None) -> str | None:
    if not icon:
        return None
    extension = "gif" if icon.startswith("a_") else "png"
    return f"https://cdn.discordapp.com/icons/{guild_id}/{icon}.{extension}?size=128"


def _member_count(cache, guild_id: int, change: int = 0) -> int:
    guild = cache.guilds.setdefault(guild_id, {})
    current = max(0, int(guild.get("member_count", 0)) + change)
    guild["member_count"] = current
    return current


def _parse_config(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


async def _save_config(guild_id: int, kind: str, config: dict) -> None:
    await db.ensure_guild(guild_id)
    await db.pool.execute(
        f"UPDATE guilds SET {kind}_config = $1::jsonb WHERE id = $2",
        json.dumps(config),
        int(guild_id),
    )


def _parse_color(raw: str) -> int:
    raw = raw.strip().lower()
    if raw in COLOR_NAMES:
        return COLOR_NAMES[raw]
    clean = raw.lstrip("#")
    if clean.startswith("0x"):
        clean = clean[2:]
    if len(clean) == 6:
        try:
            return int(clean, 16)
        except ValueError:
            pass
    raise ValueError(
        f"Invalid color `{raw}`. Use a hex code (e.g. `#5865F2`) or a color name "
        f"({', '.join(list(COLOR_NAMES.keys())[:6])})."
    )


def _is_valid_url(url: str) -> bool:
    return url.startswith("http://") or url.startswith("https://")


def _format_placeholders(template: str, user: dict, guild_name: str, count: int) -> str:
    username = user.get("global_name") or user.get("username", "Member")
    mention = f"<@{user['id']}>"
    created_at = (int(user["id"]) >> 22) // 1000 + 1420070400

    replacements = {
        "{mention}": mention,
        "{user}": username,
        "{username}": username,
        "{server}": guild_name,
        "{guild}": guild_name,
        "{count}": f"{count:,}",
        "{member_number}": f"#{count:,}",
        "{created_at}": f"<t:{created_at}:R>",
        "{created_date}": f"<t:{created_at}:D>",
    }

    result = template
    for key, val in replacements.items():
        pattern = re.compile(re.escape(key), re.IGNORECASE)
        result = pattern.sub(str(val), result)
    return result


def _render_payload(
    kind: str,
    user: dict,
    guild_id: int,
    cache,
    count: int,
    config: dict | None = None,
) -> dict:
    guild = cache.guilds.get(guild_id, {})
    guild_name = guild.get("name", "the server")
    username = user.get("global_name") or user.get("username", "Member")
    mention = f"<@{user['id']}>"
    created_at = (int(user["id"]) >> 22) // 1000 + 1420070400

    config = config or {}

    # Color
    if "color" in config and config["color"] is not None:
        color = config["color"]
    else:
        color = BLURPLE if kind == "welcome" else RED

    # Title
    if "title" in config:
        raw_title = config["title"]
        title = _format_placeholders(raw_title, user, guild_name, count) if raw_title else None
    else:
        title = f"👋 Welcome, {username}!" if kind == "welcome" else f"👋 Goodbye, {username}"

    # Description
    if "message" in config and config["message"]:
        description = _format_placeholders(config["message"], user, guild_name, count)
    else:
        if kind == "welcome":
            description = (
                f"Hey {mention}, welcome to **{guild_name}**! 🎉\n"
                "Please check the server rules and enjoy your stay."
            )
        else:
            description = f"{mention} has left **{guild_name}**. We hope to see you again!"

    embed: dict[str, Any] = {"color": color}
    if title:
        embed["title"] = title
    if description:
        embed["description"] = description

    # Fields (Member stats)
    show_fields = config.get("fields", True if kind == "welcome" else False)
    if show_fields:
        embed["fields"] = [
            {"name": "Member", "value": f"`#{count:,}`", "inline": True},
            {"name": "Account created", "value": f"<t:{created_at}:R>", "inline": True},
        ]

    # Thumbnail
    show_thumbnail = config.get("thumbnail", True)
    if show_thumbnail:
        embed["thumbnail"] = {"url": _avatar_url(user)}

    # Image Banner
    if config.get("image"):
        embed["image"] = {"url": config["image"]}

    # Footer
    if "footer" in config:
        raw_footer = config["footer"]
        if raw_footer:
            footer_text = _format_placeholders(raw_footer, user, guild_name, count)
            embed["footer"] = {"text": footer_text}
            icon_url = _guild_icon_url(guild_id, guild.get("icon"))
            if icon_url:
                embed["footer"]["icon_url"] = icon_url
    else:
        # Default footer matching default design
        if kind == "welcome":
            icon_url = _guild_icon_url(guild_id, guild.get("icon"))
            if icon_url:
                embed["footer"] = {"text": guild_name, "icon_url": icon_url}
        else:
            embed["footer"] = {"text": f"{count:,} members remain"}

    payload: dict[str, Any] = {"embeds": [embed]}

    # User ping in text content (outside embed so notification triggers)
    if config.get("ping"):
        payload["content"] = mention
        payload["allowed_mentions"] = {"users": [str(user["id"])]}
    else:
        payload["allowed_mentions"] = {"parse": []}

    return payload


def _embed(kind: str, user: dict, guild_id: int, cache, count: int, config: dict | None = None) -> dict:
    """Backward-compatible helper returning the embed object."""
    return _render_payload(kind, user, guild_id, cache, count, config)["embeds"][0]


def _build_dashboard(
    kind: str,
    guild_id: int,
    channel_id: int | None,
    config: dict,
    guild: dict,
) -> tuple[dict, list]:
    guild_name = guild.get("name", "Server")
    default_color = BLURPLE if kind == "welcome" else RED
    color_val = config.get("color", default_color)
    color_hex = f"#{color_val:06X}"

    if channel_id and channel_id > 0:
        channel_status = f"<#{channel_id}>"
    elif channel_id == 0:
        channel_status = "🔴 `Disabled`"
    else:
        channel_status = f"🟡 Not set (fallback: `#{kind}` if present)"

    ping_status = "🔔 `Enabled` (pings member)" if config.get("ping") else "🔕 `Disabled` (embed only)"
    thumb_status = "🖼️ `Enabled` (member avatar)" if config.get("thumbnail", True) else "❌ `Disabled`"
    fields_status = "📊 `Enabled` (member # & age)" if config.get("fields", kind == "welcome") else "❌ `Disabled`"
    img_status = f"[Banner Link]({config['image']})" if config.get("image") else "None"

    title_status = f"`{config['title']}`" if config.get("title") is not None else "*Default*"
    if config.get("title") == "":
        title_status = "*Disabled (None)*"

    msg_preview = config.get("message")
    if msg_preview:
        clean_preview = msg_preview.replace("\n", " ")
        if len(clean_preview) > 80:
            clean_preview = clean_preview[:77] + "..."
        msg_status = f"`{clean_preview}`"
    else:
        msg_status = "*Default*"

    footer_status = f"`{config['footer']}`" if config.get("footer") is not None else "*Default*"
    if config.get("footer") == "":
        footer_status = "*Disabled (None)*"

    embed = {
        "title": f"⚙️ {kind.title()} Message Designer & Settings",
        "description": (
            f"Configure and customize the **{kind}** announcement message for **{guild_name}**.\n"
            f"Use the commands below or interactive buttons to design your layout."
        ),
        "color": color_val,
        "fields": [
            {
                "name": "📌 Current Settings",
                "value": (
                    f"• **Channel:** {channel_status}\n"
                    f"• **Embed Color:** `{color_hex}`\n"
                    f"• **Ping Member:** {ping_status}\n"
                    f"• **Avatar Thumbnail:** {thumb_status}\n"
                    f"• **Stats Fields:** {fields_status}\n"
                    f"• **Banner Image:** {img_status}\n"
                    f"• **Title:** {title_status}\n"
                    f"• **Message:** {msg_status}\n"
                    f"• **Footer:** {footer_status}"
                ),
                "inline": False,
            },
            {
                "name": "🏷️ Available Placeholders",
                "value": (
                    "`{mention}` — Mention member (`@User`)\n"
                    "`{user}` — Username (`User`)\n"
                    "`{server}` — Server name (`Server`)\n"
                    "`{count}` — Member count (`3`)\n"
                    "`{member_number}` — Number with # (`#3`)\n"
                    "`{created_at}` — Account age (`5 years ago`)\n"
                    "`{created_date}` — Account creation date"
                ),
                "inline": False,
            },
            {
                "name": "🛠️ Customization Commands",
                "value": (
                    f"`tct!{kind} #channel` — Set announcement channel\n"
                    f"`tct!{kind} message <text>` — Customize message description\n"
                    f"`tct!{kind} title <text|none|default>` — Customize or remove title\n"
                    f"`tct!{kind} color <#hex|color|default>` — Set embed color\n"
                    f"`tct!{kind} footer <text|none|default>` — Set embed footer\n"
                    f"`tct!{kind} thumbnail <on|off>` — Toggle member avatar\n"
                    f"`tct!{kind} fields <on|off>` — Toggle member stats fields\n"
                    f"`tct!{kind} ping <on|off>` — Toggle member ping\n"
                    f"`tct!{kind} image <url|none>` — Set banner image\n"
                    f"`tct!{kind} test` — Preview message in this channel\n"
                    f"`tct!{kind} off` — Disable greetings\n"
                    f"`tct!{kind} reset` — Reset design back to default"
                ),
                "inline": False,
            },
        ],
        "footer": {"text": f"TCT-Bot Greeting System • {guild_name}"},
    }

    components = [
        {
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 1,
                    "label": "Test Preview",
                    "custom_id": f"{kind}:test",
                    "emoji": {"name": "🧪"},
                },
                {
                    "type": 2,
                    "style": 2,
                    "label": "Reset Default",
                    "custom_id": f"{kind}:reset",
                    "emoji": {"name": "🔄"},
                },
                {
                    "type": 2,
                    "style": 4,
                    "label": "Disable",
                    "custom_id": f"{kind}:disable",
                    "emoji": {"name": "⏹️"},
                },
            ],
        }
    ]
    return embed, components


async def _configured_channel(guild_id: int, kind: str, cache) -> int | None:
    row = await db.get_guild(guild_id)
    if not row:
        return None
    channel = row[f"{kind}_channel"]
    if channel is not None:
        return int(channel) or None

    for item in cache.guilds.get(guild_id, {}).get("channels", []):
        if item.get("type") == 0 and item.get("name", "").lower() == kind:
            return int(item["id"])
    return None


async def handle_member_join(rest, member: dict, cache) -> None:
    guild_id = int(member["guild_id"])
    count = _member_count(cache, guild_id, 1)
    channel_id = await _configured_channel(guild_id, "welcome", cache)
    if channel_id:
        row = await db.get_guild(guild_id)
        config = _parse_config(row["welcome_config"]) if row else {}
        payload = _render_payload("welcome", member["user"], guild_id, cache, count, config)
        await rest.post(
            f"/channels/{channel_id}/messages",
            json=payload,
        )


async def handle_member_remove(rest, member: dict, cache) -> None:
    guild_id = int(member["guild_id"])
    count = _member_count(cache, guild_id, -1)
    channel_id = await _configured_channel(guild_id, "goodbye", cache)
    if channel_id:
        row = await db.get_guild(guild_id)
        config = _parse_config(row["goodbye_config"]) if row else {}
        payload = _render_payload("goodbye", member["user"], guild_id, cache, count, config)
        await rest.post(
            f"/channels/{channel_id}/messages",
            json=payload,
        )


async def handle_component(i: Interaction) -> None:
    if not can_manage_bot(i):
        return await i.respond("❌ Only administrators can configure greetings.", ephemeral=True)

    parts = (i.custom_id or "").split(":", 1)
    if len(parts) != 2:
        return
    kind, action = parts[0], parts[1]
    if kind not in ("welcome", "goodbye"):
        return

    guild_id = int(i.guild_id)
    guild = i.cache.guilds.get(guild_id, {})
    count = int(guild.get("member_count", 0))

    if action == "test":
        row = await db.get_guild(guild_id)
        config = _parse_config(row[f"{kind}_config"]) if row else {}
        payload = _render_payload(kind, i.user, guild_id, i.cache, count, config)
        test_content = f"🧪 **Test {kind.title()} Preview:**"
        if payload.get("content"):
            test_content += f"\n{payload['content']}"
        await i.respond(
            content=test_content,
            embeds=payload.get("embeds"),
            allowed_mentions=payload.get("allowed_mentions"),
            ephemeral=True,
        )
        return

    if action == "reset":
        await db.ensure_guild(guild_id)
        await db.pool.execute(
            f"UPDATE guilds SET {kind}_config = '{{}}'::jsonb WHERE id = $1",
            guild_id,
        )
        row = await db.get_guild(guild_id)
        channel_id = row[f"{kind}_channel"]
        if channel_id is not None:
            channel_id = int(channel_id)
        embed, components = _build_dashboard(kind, guild_id, channel_id, {}, guild)
        await i.update(
            content=f"✅ **{kind.title()} design has been reset to default.**",
            embeds=[embed],
            components=components,
        )
        return

    if action == "disable":
        await db.ensure_guild(guild_id)
        await db.pool.execute(
            f"UPDATE guilds SET {kind}_channel = 0 WHERE id = $1",
            guild_id,
        )
        row = await db.get_guild(guild_id)
        config = _parse_config(row[f"{kind}_config"]) if row else {}
        embed, components = _build_dashboard(kind, guild_id, 0, config, guild)
        await i.update(
            content=f"✅ **{kind.title()} messages have been disabled.**",
            embeds=[embed],
            components=components,
        )
        return


async def configure_greeting(ctx, kind: str, argument: str) -> None:
    if not ctx.guild_id:
        return await ctx.respond("❌ This command can only be used in a server.")
    if not can_manage_bot(ctx):
        return await ctx.respond("❌ Only the server owner or an administrator can configure greetings.")

    guild_id = int(ctx.guild_id)
    guild = ctx.cache.guilds.get(guild_id, {})
    row = await db.get_guild(guild_id)
    config = _parse_config(row[f"{kind}_config"]) if row else {}
    channel_col = f"{kind}_channel"
    current_channel = row[channel_col] if row else None
    if current_channel is not None:
        current_channel = int(current_channel)

    arg = argument.strip()

    # 1. No arguments or help/config: show designer dashboard
    if not arg or arg.lower() in ("config", "dashboard", "help", "settings", "status"):
        embed, components = _build_dashboard(kind, guild_id, current_channel, config, guild)
        return await ctx.respond(embeds=[embed], components=components)

    # 2. Test
    if arg.lower() == "test":
        count = int(guild.get("member_count", 0))
        payload = _render_payload(kind, ctx.user, guild_id, ctx.cache, count, config)
        test_content = f"🧪 **Test {kind.title()} Preview:**"
        if payload.get("content"):
            test_content += f"\n{payload['content']}"
        return await ctx.respond(
            content=test_content,
            embeds=payload.get("embeds"),
            allowed_mentions=payload.get("allowed_mentions"),
        )

    # 3. Off / Disable
    if arg.lower() in ("off", "disable", "stop"):
        await db.ensure_guild(guild_id)
        await db.pool.execute(f"UPDATE guilds SET {channel_col}=0 WHERE id=$1", guild_id)
        return await ctx.respond(f"✅ {kind.title()} messages disabled.")

    # 4. Reset design
    if arg.lower() in ("reset", "default"):
        await db.ensure_guild(guild_id)
        await db.pool.execute(f"UPDATE guilds SET {kind}_config='{{}}'::jsonb WHERE id=$1", guild_id)
        return await ctx.respond(f"✅ {kind.title()} design reset to default.")

    parts = arg.split(maxsplit=1)
    subcmd = parts[0].lower()
    subarg = parts[1].strip() if len(parts) > 1 else ""

    # 5. Message / Msg / Desc
    if subcmd in ("message", "msg", "desc", "description"):
        if not subarg:
            return await ctx.respond(
                f"❌ Please provide the message text.\n"
                f"Example: `tct!{kind} message Hey {{mention}}, welcome to **{{server}}**! 🎉`"
            )
        config["message"] = subarg
        await _save_config(guild_id, kind, config)
        sample = _format_placeholders(subarg, ctx.user, guild.get("name", "Server"), 1)
        return await ctx.respond(
            f"✅ **{kind.title()} message updated!**\nPreview:\n> {sample}"
        )

    # 6. Title
    if subcmd == "title":
        if not subarg:
            return await ctx.respond(
                f"❌ Please provide a title, `none` to disable, or `default`.\n"
                f"Example: `tct!{kind} title Welcome to {{server}}!`"
            )
        if subarg.lower() in ("none", "off", "disable", "remove"):
            config["title"] = ""
            await _save_config(guild_id, kind, config)
            return await ctx.respond(f"✅ Embed title removed.")
        elif subarg.lower() == "default":
            config.pop("title", None)
            await _save_config(guild_id, kind, config)
            return await ctx.respond(f"✅ Embed title reset to default.")
        else:
            config["title"] = subarg
            await _save_config(guild_id, kind, config)
            sample = _format_placeholders(subarg, ctx.user, guild.get("name", "Server"), 1)
            return await ctx.respond(f"✅ **{kind.title()} title updated!**\nTitle: `{sample}`")

    # 7. Color
    if subcmd in ("color", "colour"):
        if not subarg:
            return await ctx.respond(
                f"❌ Please provide a hex color code or preset name.\n"
                f"Example: `tct!{kind} color #5865F2`, `tct!{kind} color green`, or `tct!{kind} color default`"
            )
        if subarg.lower() == "default":
            config.pop("color", None)
            await _save_config(guild_id, kind, config)
            return await ctx.respond(f"✅ Embed color reset to default.")
        try:
            color_int = _parse_color(subarg)
            config["color"] = color_int
            await _save_config(guild_id, kind, config)
            return await ctx.respond(f"✅ **Embed color updated to `#{color_int:06X}`!**")
        except ValueError as err:
            return await ctx.respond(f"❌ {err}")

    # 8. Footer
    if subcmd == "footer":
        if not subarg:
            return await ctx.respond(
                f"❌ Please provide footer text, `none`, or `default`.\n"
                f"Example: `tct!{kind} footer {{server}} • Enjoy your stay!`"
            )
        if subarg.lower() in ("none", "off", "remove"):
            config["footer"] = ""
            await _save_config(guild_id, kind, config)
            return await ctx.respond(f"✅ Embed footer removed.")
        elif subarg.lower() == "default":
            config.pop("footer", None)
            await _save_config(guild_id, kind, config)
            return await ctx.respond(f"✅ Embed footer reset to default.")
        else:
            config["footer"] = subarg
            await _save_config(guild_id, kind, config)
            sample = _format_placeholders(subarg, ctx.user, guild.get("name", "Server"), 1)
            return await ctx.respond(f"✅ **Embed footer updated!**\nFooter: `{sample}`")

    # 9. Thumbnail
    if subcmd in ("thumbnail", "thumb", "avatar"):
        if subarg.lower() in ("on", "true", "enable", "yes"):
            config["thumbnail"] = True
            await _save_config(guild_id, kind, config)
            return await ctx.respond(f"✅ Avatar thumbnail enabled.")
        elif subarg.lower() in ("off", "false", "disable", "no"):
            config["thumbnail"] = False
            await _save_config(guild_id, kind, config)
            return await ctx.respond(f"✅ Avatar thumbnail disabled.")
        else:
            return await ctx.respond(f"❌ Usage: `tct!{kind} thumbnail on` or `off`.")

    # 10. Fields (Member count and Account age)
    if subcmd in ("fields", "stats"):
        if subarg.lower() in ("on", "true", "enable", "yes"):
            config["fields"] = True
            await _save_config(guild_id, kind, config)
            return await ctx.respond(f"✅ Member stats fields enabled.")
        elif subarg.lower() in ("off", "false", "disable", "no"):
            config["fields"] = False
            await _save_config(guild_id, kind, config)
            return await ctx.respond(f"✅ Member stats fields disabled.")
        else:
            return await ctx.respond(f"❌ Usage: `tct!{kind} fields on` or `off`.")

    # 11. Ping
    if subcmd == "ping":
        if subarg.lower() in ("on", "true", "enable", "yes"):
            config["ping"] = True
            await _save_config(guild_id, kind, config)
            return await ctx.respond(f"✅ User mention ping enabled (member will be notified).")
        elif subarg.lower() in ("off", "false", "disable", "no"):
            config["ping"] = False
            await _save_config(guild_id, kind, config)
            return await ctx.respond(f"✅ User mention ping disabled (embed only).")
        else:
            return await ctx.respond(f"❌ Usage: `tct!{kind} ping on` or `off`.")

    # 12. Image / Banner
    if subcmd in ("image", "banner"):
        if not subarg or subarg.lower() in ("none", "off", "remove"):
            config.pop("image", None)
            await _save_config(guild_id, kind, config)
            return await ctx.respond(f"✅ Banner image removed.")
        if not _is_valid_url(subarg):
            return await ctx.respond(f"❌ Please provide a valid `http://` or `https://` image URL.")
        config["image"] = subarg
        await _save_config(guild_id, kind, config)
        return await ctx.respond(f"✅ **Banner image set!**\n<{subarg}>")

    # 13. Channel assignment (e.g. #general, <#123456>, or 123456)
    channel = arg.strip("<>#")
    if channel.isdigit():
        try:
            channel_data = await ctx.rest.get(f"/channels/{channel}")
        except Exception:
            return await ctx.respond("❌ I couldn't access that channel.")
        if (str(channel_data.get("guild_id")) != str(guild_id)
                or channel_data.get("type") not in (0, 5)):
            return await ctx.respond("❌ Please choose a text or announcement channel in this server.")

        await db.ensure_guild(guild_id)
        await db.pool.execute(
            f"UPDATE guilds SET {channel_col}=$1 WHERE id=$2", int(channel), guild_id
        )
        return await ctx.respond(f"✅ {kind.title()} messages will be sent in <#{channel}>.")

    # Unknown option
    return await ctx.respond(
        f"❌ Unknown option `{arg}`.\n"
        f"Use `tct!{kind}` without arguments to view your current configuration and design options."
    )
