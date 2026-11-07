import json
import time
import sys
from dataclasses import dataclass
from interactions import slash, Interaction, MessageContext, CHANNEL, ROLE, USER, STR
from database import db
from helpers.permissions import can_manage_bot, can_manage_bot_async, can_use_staff_commands
import state
from lavalink import lavalink


@dataclass(frozen=True)
class HelpItem:
    key: str
    label: str
    syntax: str
    desc: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class HelpCategory:
    key: str
    title: str
    short_title: str
    emoji: str
    items: tuple[HelpItem, ...]


HELP_CATEGORIES: tuple[HelpCategory, ...] = (
    HelpCategory(
        key="utility",
        title="🌍 Everyone & Utility",
        short_title="Everyone & Utility",
        emoji="🌍",
        items=(
            HelpItem("ping", "tct!ping", "`tct!ping`", "Check latency and response", ("ping",)),
            HelpItem("userinfo", "tct!userinfo", "`tct!userinfo [@member]`", "View user and member card", ("userinfo",)),
            HelpItem("avatar", "tct!avatar", "`tct!avatar [@member]`", "View user full-size avatar", ("avatar",)),
            HelpItem("banner", "tct!banner", "`tct!banner [@member]`", "View user profile banner", ("banner",)),
            HelpItem("roleinfo", "tct!roleinfo", "`tct!roleinfo @Role`", "View role details", ("roleinfo",)),
            HelpItem("serverinfo", "tct!serverinfo", "`tct!serverinfo`", "View server statistics", ("serverinfo",)),
            HelpItem("botstats", "tct!botstats", "`tct!botstats`", "Bot diagnostics & uptime", ("botstats",)),
            HelpItem("snipe", "tct!snipe", "`tct!snipe`", "View last deleted message", ("snipe",)),
            HelpItem("editsnipe", "tct!editsnipe", "`tct!editsnipe`", "View last edited message", ("editsnipe",)),
            HelpItem("tag", "tct!tag / tct!taglist", "`tct!tag <name>` / `tct!taglist`", "View or list server tags", ("tag", "taglist")),
            HelpItem("aiask", "tct!aiask", "`tct!aiask <question>`", "AI chat & Q&A", ("aiask",)),
            HelpItem("aiimagine", "tct!aiimagine", "`tct!aiimagine <prompt>`", "AI image generation", ("aiimagine",)),
            HelpItem("feedlist", "tct!feedlist", "`tct!feedlist`", "List configured RSS feeds", ("feedlist",)),
            HelpItem("ticketclose", "tct!ticketclose", "`tct!ticketclose`", "Close current ticket", ("ticketclose",)),
        ),
    ),
    HelpCategory(
        key="community",
        title="🏆 Community & Games",
        short_title="Community & Games",
        emoji="🏆",
        items=(
            HelpItem("rank", "tct!rank / tct!level", "`tct!rank [@member]`", "View XP level & progress", ("rank", "level")),
            HelpItem("leaderboard", "tct!leaderboard / tct!lb", "`tct!leaderboard` (`tct!lb`)", "Server XP leaderboard", ("leaderboard", "lb")),
            HelpItem("afk", "tct!afk", "`tct!afk [reason]`", "Set your AFK status", ("afk",)),
            HelpItem("remind", "tct!remind / tct!remindme", "`tct!remind <duration> <text>`", "Set a personal reminder", ("remind", "remindme")),
            HelpItem("suggest", "tct!suggest", "`tct!suggest <idea>`", "Submit a server suggestion", ("suggest",)),
            HelpItem("poll", "tct!poll", "`tct!poll <q> | <opt1> | <opt2>`", "Interactive button poll", ("poll",)),
            HelpItem("tictactoe", "tct!tictactoe / tct!ttt", "`tct!tictactoe @member`", "Interactive Tic-Tac-Toe", ("tictactoe", "ttt")),
            HelpItem("rps", "tct!rps", "`tct!rps [@member]`", "Interactive Rock Paper Scissors", ("rps",)),
            HelpItem("8ball", "tct!8ball", "`tct!8ball <question>`", "Ask the magic 8-ball", ("8ball",)),
            HelpItem("coinflip", "tct!coinflip / tct!cf", "`tct!coinflip` (`tct!cf`)", "Flip a coin", ("coinflip", "cf")),
            HelpItem("roll", "tct!roll", "`tct!roll [dice]`", "Roll dice (e.g. 2d6)", ("roll",)),
        ),
    ),
    HelpCategory(
        key="music",
        title="🎵 Music & Voice",
        short_title="Music & Voice",
        emoji="🎵",
        items=(
            HelpItem("musicjoin", "tct!musicjoin", "`tct!musicjoin [#channel]`", "Connect to voice channel", ("musicjoin",)),
            HelpItem("musicleave", "tct!musicleave", "`tct!musicleave`", "Disconnect from voice", ("musicleave",)),
            HelpItem("play", "tct!play / tct!musicplay", "`tct!play <URL|query>` (`tct!musicplay`)", "Play or queue track", ("play", "musicplay", "p")),
            HelpItem("pause", "tct!pause / tct!musicpause", "`tct!pause`", "Pause or resume playback", ("pause", "musicpause")),
            HelpItem("skip", "tct!skip / tct!musicskip", "`tct!skip` (`tct!s`)", "Skip current track", ("skip", "musicskip", "s")),
            HelpItem("stop", "tct!stop / tct!musicstop", "`tct!stop`", "Stop playback & clear queue", ("stop", "musicstop")),
            HelpItem("np", "tct!np / tct!musicnowplaying", "`tct!np` (`tct!musicnowplaying`)", "Current track + controls", ("np", "musicnowplaying")),
            HelpItem("queue", "tct!queue / tct!musicqueue", "`tct!queue`", "View upcoming tracks", ("queue", "musicqueue")),
            HelpItem("history", "tct!history / tct!musichistory", "`tct!history`", "View recently played tracks", ("history", "musichistory")),
            HelpItem("volume", "tct!volume / tct!musicvolume", "`tct!volume <0-200>`", "Adjust playback volume", ("volume", "musicvolume")),
            HelpItem("loop", "tct!loop / tct!musicloop", "`tct!loop <off|track|queue>`", "Set repeat mode", ("loop", "musicloop")),
            HelpItem("shuffle", "tct!shuffle / tct!musicshuffle", "`tct!shuffle`", "Shuffle the queue", ("shuffle", "musicshuffle")),
            HelpItem("musicremove", "tct!musicremove", "`tct!musicremove <index>`", "Remove track from queue", ("musicremove",)),
            HelpItem("musicclear", "tct!musicclear", "`tct!musicclear`", "Clear all queued tracks", ("musicclear",)),
            HelpItem("musicseek", "tct!musicseek", "`tct!musicseek <time>`", "Seek to position in track", ("musicseek",)),
            HelpItem("musicfilter", "tct!musicfilter", "`tct!musicfilter <preset|clear>`", "Audio filters (8D, Bassboost...)", ("musicfilter",)),
            HelpItem("radio", "tct!radio / tct!musicradio", "`tct!radio [on|off]`", "Toggle autoplay radio", ("radio", "musicradio")),
            HelpItem("247", "tct!247 / tct!music247", "`tct!247 [on|off]`", "Toggle 24/7 stay-in-voice mode", ("247", "music247")),
        ),
    ),
    HelpCategory(
        key="moderation",
        title="🛡️ Moderator / Staff",
        short_title="Moderator / Staff",
        emoji="🛡️",
        items=(
            HelpItem("modban", "tct!modban", "`tct!modban @member [reason]`", "Ban a member", ("modban",)),
            HelpItem("modunban", "tct!modunban", "`tct!modunban <user_id> [reason]`", "Unban a user by ID", ("modunban",)),
            HelpItem("modkick", "tct!modkick", "`tct!modkick @member [reason]`", "Kick a member", ("modkick",)),
            HelpItem("modtimeout", "tct!modtimeout", "`tct!modtimeout @member <dur> [reason]`", "Timeout a member", ("modtimeout",)),
            HelpItem("moduntimeout", "tct!moduntimeout", "`tct!moduntimeout @member`", "Remove member timeout", ("moduntimeout",)),
            HelpItem("modwarn", "tct!modwarn", "`tct!modwarn @member <reason>`", "Warn a member", ("modwarn",)),
            HelpItem("modwarnings", "tct!modwarnings", "`tct!modwarnings @member`", "View member warnings", ("modwarnings",)),
            HelpItem("moddelwarn", "tct!moddelwarn", "`tct!moddelwarn <case_id>`", "Delete a warning case", ("moddelwarn",)),
            HelpItem("modclearwarns", "tct!modclearwarns", "`tct!modclearwarns @member`", "Clear member warnings", ("modclearwarns",)),
            HelpItem("modpurge", "tct!modpurge", "`tct!modpurge <1-100>`", "Bulk delete messages", ("modpurge",)),
            HelpItem("slowmode", "tct!slowmode", "`tct!slowmode <duration|off>`", "Set channel slowmode", ("slowmode",)),
            HelpItem("lock", "tct!lock / tct!unlock", "`tct!lock [#channel]` / `tct!unlock`", "Lock or unlock channel", ("lock", "unlock")),
            HelpItem("nick", "tct!nick", "`tct!nick @member [nickname]`", "Change member nickname", ("nick",)),
            HelpItem("say", "tct!say", "`tct!say <text>`", "Send message as bot", ("say",)),
            HelpItem("embed", "tct!embed", "`tct!embed <title> | <desc>`", "Post a custom embed", ("embed",)),
            HelpItem("steal", "tct!steal", "`tct!steal <emoji> [name]`", "Clone custom emoji", ("steal",)),
            HelpItem("ticketclaim", "tct!ticketclaim / add / remove / rename", "`tct!ticketclaim` / `ticketadd` / `ticketremove` / `ticketrename`", "Manage open tickets", ("ticketclaim", "ticketadd", "ticketremove", "ticketrename")),
            HelpItem("tagadd", "tct!tagadd / tct!tagdel", "`tct!tagadd <name> <content>` / `tct!tagdel`", "Create or delete tags", ("tagadd", "tagdel")),
            HelpItem("giveaway", "tct!giveawaystart / end / reroll / list", "`tct!giveawaystart` / `giveawayend` / `giveawayreroll` / `giveawaylist`", "Manage giveaways", ("giveawaystart", "giveawayend", "giveawayreroll", "giveawaylist")),
        ),
    ),
    HelpCategory(
        key="config",
        title="⚙️ Server Config & Setup (Admin)",
        short_title="Server Config & Setup",
        emoji="⚙️",
        items=(
            HelpItem("setmodrole", "tct!setmodrole / setadminrole / modroles", "`tct!setmodrole @Role [mod|admin]` / `tct!setadminrole` / `tct!removemodrole` / `tct!modroles`", "Configure staff & admin roles", ("setmodrole", "setadminrole", "removemodrole", "removeadminrole", "modroles", "adminroles")),
            HelpItem("autorole", "tct!autorole", "`tct!autorole <@Role|off>`", "Auto-assign role on join", ("autorole",)),
            HelpItem("reactionrole", "tct!reactionrole / removereactionrole", "`tct!reactionrole <msg_id> <emoji> @Role` / `tct!removereactionrole` / `tct!reactionroles`", "Manage reaction roles", ("reactionrole", "removereactionrole", "reactionroles")),
            HelpItem("setlog", "tct!setlog", "`tct!setlog #channel`", "Set mod & audit log channel", ("setlog",)),
            HelpItem("welcome", "tct!welcome / tct!setwelcome", "`tct!welcome [...]` / `tct!setwelcome #channel`", "Design welcome message", ("welcome", "welcomedesign", "setwelcome", "welcomechannel")),
            HelpItem("goodbye", "tct!goodbye / tct!setgoodbye", "`tct!goodbye [...]` / `tct!setgoodbye #channel`", "Design goodbye message", ("goodbye", "goodbyedesign", "setgoodbye", "goodbyechannel")),
            HelpItem("levelreward", "tct!levelreward", "`tct!levelreward <level> @Role`", "Role reward on level-up", ("levelreward",)),
            HelpItem("setsuggest", "tct!setsuggest", "`tct!setsuggest #channel`", "Set suggestions channel", ("setsuggest",)),
            HelpItem("setstarboard", "tct!setstarboard", "`tct!setstarboard #channel [stars]`", "Set starboard channel", ("setstarboard",)),
            HelpItem("ticketpanel", "tct!ticketpanel", "`tct!ticketpanel [panel]`", "Post interactive ticket panel", ("ticketpanel",)),
            HelpItem("feedadd", "tct!feedadd / tct!feedremove", "`tct!feedadd <URL>` / `tct!feedremove <id>`", "Add or remove RSS feeds", ("feedadd", "feedremove")),
        ),
    ),
)

CATEGORY_MAP: dict[str, HelpCategory] = {c.key: c for c in HELP_CATEGORIES}
ALIAS_TO_ITEM_KEY: dict[str, str] = {}
ALL_ITEM_KEYS: set[str] = set()
for _cat in HELP_CATEGORIES:
    for _item in _cat.items:
        ALL_ITEM_KEYS.add(_item.key)
        for _alias in _item.aliases:
            ALIAS_TO_ITEM_KEY[_alias] = _item.key

CATEGORY_ALIASES: dict[str, str] = {
    "all": "all",
    "overview": "all",
    "utility": "utility",
    "everyone": "utility",
    "general": "utility",
    "community": "community",
    "games": "community",
    "game": "community",
    "music": "music",
    "voice": "music",
    "moderation": "moderation",
    "mod": "moderation",
    "staff": "moderation",
    "config": "config",
    "setup": "config",
    "admin": "config",
}

# In-memory cache of disabled command keys per guild: { guild_id: set[str] }
_disabled_commands_cache: dict[int, set[str]] = {}


def _parse_disabled_list(raw) -> set[str]:
    if not raw:
        return set()
    if isinstance(raw, (list, tuple, set)):
        return {str(x) for x in raw if str(x) in ALL_ITEM_KEYS}
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return {str(x) for x in parsed if str(x) in ALL_ITEM_KEYS}
        except Exception:
            pass
    return set()


async def get_disabled_commands(guild_id: int | None) -> set[str]:
    """Return the set of disabled canonical command keys for a guild."""
    if not guild_id:
        return set()
    gid = int(guild_id)
    if gid in _disabled_commands_cache:
        return set(_disabled_commands_cache[gid])
    if not db.pool:
        return set()
    try:
        row = await db.get_guild(gid)
        disabled = _parse_disabled_list(row.get("disabled_commands") if row else None)
        _disabled_commands_cache[gid] = disabled
        return set(disabled)
    except Exception:
        return set()


async def set_disabled_commands(guild_id: int, disabled: set[str]) -> set[str]:
    """Persist the set of disabled canonical command keys for a guild."""
    gid = int(guild_id)
    clean = {k for k in disabled if k in ALL_ITEM_KEYS}
    _disabled_commands_cache[gid] = set(clean)
    if db.pool:
        try:
            await db.ensure_guild(gid)
            await db.pool.execute(
                "UPDATE guilds SET disabled_commands = $1::jsonb WHERE id = $2",
                json.dumps(sorted(clean)),
                gid,
            )
        except Exception:
            pass
    return clean


async def is_command_disabled(guild_id: int | None, command_name: str) -> bool:
    """Return True if a prefix/slash command name is disabled in the given guild."""
    if not guild_id:
        return False
    name = command_name.lower().strip()
    if name == "help":
        return False
    item_key = ALIAS_TO_ITEM_KEY.get(name, name)
    disabled = await get_disabled_commands(guild_id)
    return item_key in disabled


def _resolve_category(raw: str | None) -> str:
    if not raw:
        return "all"
    cleaned = raw.strip().lower()
    return CATEGORY_ALIASES.get(cleaned, "all")


def _build_help_view(
    guild_id: int | None,
    selected_cat: str,
    disabled: set[str],
    *,
    is_admin: bool = True,
) -> tuple[dict, list[dict]]:
    selected_cat = selected_cat if selected_cat in CATEGORY_MAP else "all"
    total_cmds = len(ALL_ITEM_KEYS)
    enabled_total = total_cmds - len(disabled)

    if selected_cat == "all":
        fields = []
        for cat in HELP_CATEGORIES:
            enabled_items = [item for item in cat.items if item.key not in disabled]
            lines = []
            if cat.key == "utility":
                lines.append("`tct!help [category]` — Show this command list & customize commands")
            for item in enabled_items:
                lines.append(f"{item.syntax} — {item.desc}")
            if not lines:
                continue
            val = "\n".join(lines)
            if len(val) > 1024:
                val = val[:1020].rsplit("\n", 1)[0] + "\n..."
            count_tag = (
                f" ({len(enabled_items)}/{len(cat.items)})"
                if len(enabled_items) < len(cat.items)
                else ""
            )
            fields.append({"name": f"{cat.title}{count_tag}", "value": val, "inline": False})

        desc_parts = [
            "Select a **category** below to view detailed usage"
            + (" or **uncheck commands** your server doesn't use." if is_admin else ".")
        ]
        if disabled:
            desc_parts.append(
                f"⚙️ **{enabled_total}/{total_cmds}** commands active in this server "
                f"(*{len(disabled)} unchecked/hidden*)."
            )
        embed = {
            "title": "Commands List",
            "description": "\n".join(desc_parts),
            "color": 0x5865F2,
            "fields": fields,
            "footer": {
                "text": (
                    f"Showing {enabled_total}/{total_cmds} enabled commands • "
                    + (
                        "Use the dropdowns below to filter or uncheck unused commands"
                        if is_admin
                        else "Use the dropdown below to filter commands by category"
                    )
                )
            },
        }
    else:
        cat = CATEGORY_MAP[selected_cat]
        enabled_items = [item for item in cat.items if item.key not in disabled]
        disabled_items = [item for item in cat.items if item.key in disabled]

        lines = []
        if cat.key == "utility":
            lines.append("• `tct!help [category]` — Show this command list & customize commands *(Always enabled)*")
        for item in enabled_items:
            lines.append(f"• {item.syntax} — {item.desc}")

        if not lines:
            lines.append("*All commands in this category are currently unchecked/disabled for this server.*")

        fields = []
        if disabled_items and is_admin:
            unchecked_str = ", ".join(f"`tct!{item.key}`" for item in disabled_items)
            fields.append({
                "name": f"🚫 Unchecked / Disabled in Server ({len(disabled_items)})",
                "value": unchecked_str[:1024],
                "inline": False,
            })

        header_note = (
            "Use the **Check / Uncheck Commands** menu below to uncheck commands this server does not use.\n\n"
            if is_admin
            else ""
        )
        embed = {
            "title": f"{cat.title} — Commands ({len(enabled_items)}/{len(cat.items)} Enabled)",
            "description": header_note + "\n".join(lines),
            "color": 0x5865F2,
            "fields": fields,
            "footer": {
                "text": (
                    f"Category: {cat.short_title} • "
                    + (
                        "Owner & Admin roles can uncheck unused commands below"
                        if is_admin
                        else "Select another category below"
                    )
                )
            },
        }

    # Row 1: Category navigation dropdown (available to everyone)
    cat_options = [
        {
            "label": "All Categories (Overview)",
            "value": "all",
            "description": f"View all enabled commands ({enabled_total}/{total_cmds} enabled)",
            "emoji": {"name": "📋"},
            "default": selected_cat == "all",
        }
    ]
    for cat in HELP_CATEGORIES:
        en_count = sum(1 for item in cat.items if item.key not in disabled)
        cat_options.append({
            "label": f"{cat.short_title} ({en_count}/{len(cat.items)})",
            "value": cat.key,
            "description": f"View & check/uncheck {cat.short_title} commands" if is_admin else f"View {cat.short_title} commands",
            "emoji": {"name": cat.emoji},
            "default": selected_cat == cat.key,
        })

    components: list[dict] = [
        {
            "type": 1,
            "components": [
                {
                    "type": 3,
                    "custom_id": "help:cat",
                    "placeholder": "📂 Select a category to view or customize commands...",
                    "min_values": 1,
                    "max_values": 1,
                    "options": cat_options,
                }
            ],
        }
    ]

    # Row 2 & Row 3: Multi-select menu & quick actions (Admin & Owner roles only)
    if is_admin:
        if selected_cat == "all":
            cat_toggle_options = []
            for cat in HELP_CATEGORIES:
                en_count = sum(1 for item in cat.items if item.key not in disabled)
                cat_toggle_options.append({
                    "label": cat.short_title,
                    "value": cat.key,
                    "description": f"{en_count}/{len(cat.items)} commands enabled — Uncheck to disable category",
                    "emoji": {"name": cat.emoji},
                    "default": en_count > 0,
                })
            components.append({
                "type": 1,
                "components": [
                    {
                        "type": 3,
                        "custom_id": "help:toggle_cats",
                        "placeholder": "✅ Check / Uncheck categories used in this server (Owner / Admin)",
                        "min_values": 0,
                        "max_values": len(cat_toggle_options),
                        "options": cat_toggle_options,
                    }
                ],
            })
            if disabled:
                components.append({
                    "type": 1,
                    "components": [
                        {
                            "type": 2,
                            "style": 3,
                            "label": f"Enable All Commands ({len(disabled)} disabled)",
                            "custom_id": "help:reset_all",
                            "emoji": {"name": "🔄"},
                        }
                    ],
                })
        else:
            cat = CATEGORY_MAP[selected_cat]
            cmd_options = []
            for item in cat.items:
                is_enabled = item.key not in disabled
                cmd_options.append({
                    "label": item.label[:100],
                    "value": item.key,
                    "description": item.desc[:100],
                    "default": is_enabled,
                })
            components.append({
                "type": 1,
                "components": [
                    {
                        "type": 3,
                        "custom_id": f"help:toggle_cmds:{cat.key}",
                        "placeholder": f"✅ Uncheck {cat.short_title} commands not used in this server",
                        "min_values": 0,
                        "max_values": len(cmd_options),
                        "options": cmd_options,
                    }
                ],
            })
            components.append({
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 3,
                        "label": "Check All in Category",
                        "custom_id": f"help:cat_all:{cat.key}:on",
                        "emoji": {"name": "✅"},
                    },
                    {
                        "type": 2,
                        "style": 4,
                        "label": "Uncheck All in Category",
                        "custom_id": f"help:cat_all:{cat.key}:off",
                        "emoji": {"name": "🚫"},
                    },
                    {
                        "type": 2,
                        "style": 2,
                        "label": "Reset All Server Commands",
                        "custom_id": "help:reset_all",
                        "emoji": {"name": "🔄"},
                    },
                ],
            })

    return embed, components


@slash("help", "Show help", [
    {"name": "category", "description": "Optional category (utility, community, music, moderation, config)", "type": STR, "required": False},
])
async def help_cmd(i: Interaction, category: str | None = None):
    disabled = await get_disabled_commands(i.guild_id)
    selected_cat = _resolve_category(category)
    is_admin = await can_manage_bot_async(i, db.pool) if i.guild_id else False
    embed, components = _build_help_view(i.guild_id, selected_cat, disabled, is_admin=is_admin)
    await i.respond(embeds=[embed], components=components)


async def handle_help_component(i: Interaction) -> None:
    cid = i.custom_id or ""
    is_admin = await can_manage_bot_async(i, db.pool) if i.guild_id else False

    # Anyone can switch the viewed category in the help menu
    if cid == "help:cat":
        chosen = i.values[0] if getattr(i, "values", None) else "all"
        disabled = await get_disabled_commands(i.guild_id)
        embed, components = _build_help_view(i.guild_id, chosen, disabled, is_admin=is_admin)
        return await i.update(embeds=[embed], components=components)

    # Modifying enabled/disabled commands requires Server Owner or Admin/Owner role
    if not i.guild_id:
        return await i.respond("❌ Server command customization is only available inside a server.", ephemeral=True)
    if not is_admin:
        return await i.respond(
            "❌ Only the server owner or users with an Owner / Admin role can check or uncheck commands for this server.",
            ephemeral=True,
        )

    disabled = await get_disabled_commands(i.guild_id)

    if cid == "help:toggle_cats":
        checked_cats = set(getattr(i, "values", None) or [])
        for cat in HELP_CATEGORIES:
            cat_keys = {item.key for item in cat.items}
            if cat.key not in checked_cats:
                disabled.update(cat_keys)
            else:
                if cat_keys.issubset(disabled):
                    disabled.difference_update(cat_keys)
        disabled = await set_disabled_commands(i.guild_id, disabled)
        embed, components = _build_help_view(i.guild_id, "all", disabled, is_admin=True)
        return await i.update(embeds=[embed], components=components)

    if cid.startswith("help:toggle_cmds:"):
        cat_key = cid.split(":", 2)[2]
        cat = CATEGORY_MAP.get(cat_key)
        if not cat:
            return await i.respond("❌ Unknown category.", ephemeral=True)
        checked_keys = set(getattr(i, "values", None) or [])
        for item in cat.items:
            if item.key in checked_keys:
                disabled.discard(item.key)
            else:
                disabled.add(item.key)
        disabled = await set_disabled_commands(i.guild_id, disabled)
        embed, components = _build_help_view(i.guild_id, cat_key, disabled, is_admin=True)
        return await i.update(embeds=[embed], components=components)

    if cid.startswith("help:cat_all:"):
        parts = cid.split(":")
        cat_key = parts[2] if len(parts) > 2 else ""
        mode = parts[3] if len(parts) > 3 else "on"
        cat = CATEGORY_MAP.get(cat_key)
        if not cat:
            return await i.respond("❌ Unknown category.", ephemeral=True)
        for item in cat.items:
            if mode == "on":
                disabled.discard(item.key)
            else:
                disabled.add(item.key)
        disabled = await set_disabled_commands(i.guild_id, disabled)
        embed, components = _build_help_view(i.guild_id, cat_key, disabled, is_admin=True)
        return await i.update(embeds=[embed], components=components)

    if cid == "help:reset_all":
        disabled = await set_disabled_commands(i.guild_id, set())
        embed, components = _build_help_view(i.guild_id, "all", disabled, is_admin=True)
        return await i.update(embeds=[embed], components=components)




@slash("ping", "Latency")
async def ping(i: Interaction):
    await i.respond("🏓 Pong!")

@slash("setlog", "Set modlog channel", [
    {"name": "channel", "description": "Channel", "type": CHANNEL, "required": True},
])
async def setlog(i: Interaction, channel):
    if not can_manage_bot(i):
        return await i.respond("❌ Only the server owner or an administrator can configure the bot.", ephemeral=True)
    await db.ensure_guild(i.guild_id)
    await db.pool.execute("UPDATE guilds SET log_channel=$1 WHERE id=$2", int(channel), i.guild_id)
    await i.respond(f"✅ Log channel set to <#{channel}>", ephemeral=True)


@slash("setmodrole", "Allow a role to use staff or admin commands", [
    {"name": "role", "description": "Role to configure", "type": ROLE, "required": True},
    {"name": "role_type", "description": "Level: mod or admin (default: mod)", "type": STR, "required": False},
])
async def setmodrole(i: Interaction, role, role_type="mod"):
    """Handle tct!setmodrole and upsert the mentioned role for this guild."""
    if not can_manage_bot(i):
        return await i.respond(
            "❌ Only the server owner or an administrator can configure staff roles.",
            ephemeral=True,
        )
    if isinstance(i, MessageContext):
        mentioned_roles = i.raw.get("mention_roles", [])
        if str(role) not in mentioned_roles:
            return await i.respond("❌ Mention a role. Usage: `tct!setmodrole @Role [mod|admin]`")
    if int(role) == int(i.guild_id):
        return await i.respond("❌ `@everyone` cannot be configured as a staff role.")

    # Auto-detect if role name itself indicates admin (e.g. TCT-Admin, [Admin])
    role_obj = i.cache.roles.get((int(i.guild_id), int(role)))
    role_name = role_obj.get("name") if role_obj else ""

    raw_type = str(role_type or "").lower().strip()
    from helpers.permissions import add_cached_admin_role, remove_cached_admin_role, is_admin_role_name

    if "admin" in raw_type or ("mod" not in raw_type and is_admin_role_name(role_name)):
        actual_type = "admin"
    else:
        actual_type = "mod"

    await db.ensure_guild(i.guild_id)
    await db.pool.execute(
        """
        INSERT INTO guild_staff_roles (guild_id, role_id, is_staff, role_type)
        VALUES ($1, $2, TRUE, $3)
        ON CONFLICT (guild_id, role_id)
        DO UPDATE SET is_staff = TRUE, role_type = $3, updated_at = NOW()
        """,
        int(i.guild_id), int(role), actual_type,
    )

    if actual_type == "admin":
        add_cached_admin_role(int(i.guild_id), int(role))
        await i.respond(f"👑 <@&{role}> is now configured as an **Administrator** role (full bot access).")
    else:
        remove_cached_admin_role(int(i.guild_id), int(role))
        await i.respond(f"🛡️ <@&{role}> can now use **Staff / Moderator** commands.")


@slash("setadminrole", "Allow a role to use administrator and bot configuration commands", [
    {"name": "role", "description": "Administrator role", "type": ROLE, "required": True},
])
async def setadminrole(i: Interaction, role):
    return await setmodrole(i, role, role_type="admin")


@slash("removemodrole", "Remove a role from bot staff/admin", [
    {"name": "role", "description": "Role to remove", "type": ROLE, "required": True},
])
async def removemodrole(i: Interaction, role):
    if not can_manage_bot(i):
        return await i.respond(
            "❌ Only the server owner or an administrator can configure staff roles.",
            ephemeral=True,
        )
    if isinstance(i, MessageContext):
        mentioned_roles = i.raw.get("mention_roles", [])
        if str(role) not in mentioned_roles:
            return await i.respond("❌ Mention a role. Usage: `tct!removemodrole @Role`")
    result = await db.pool.execute(
        "DELETE FROM guild_staff_roles WHERE guild_id = $1 AND role_id = $2",
        int(i.guild_id), int(role),
    )
    from helpers.permissions import remove_cached_admin_role
    remove_cached_admin_role(int(i.guild_id), int(role))

    if result == "DELETE 0":
        return await i.respond(f"ℹ️ <@&{role}> was not a configured staff role.")
    await i.respond(f"✅ <@&{role}> removed from configured staff/admin roles.")


@slash("modroles", "List configured bot staff and administrator roles")
async def modroles(i: Interaction):
    if not can_manage_bot(i):
        return await i.respond(
            "❌ Only the server owner or an administrator can view this configuration.",
            ephemeral=True,
        )
    rows = await db.pool.fetch(
        "SELECT role_id, role_type FROM guild_staff_roles "
        "WHERE guild_id = $1 AND is_staff = TRUE ORDER BY role_type, role_id",
        int(i.guild_id),
    )
    if not rows:
        return await i.respond("ℹ️ No staff or administrator roles configured. Use `tct!setmodrole @Role` or `tct!setadminrole @Role`.")

    admins = [f"• <@&{r['role_id']}>" for r in rows if r.get("role_type") == "admin"]
    mods = [f"• <@&{r['role_id']}>" for r in rows if r.get("role_type") != "admin"]

    parts = []
    if admins:
        parts.append("**👑 Administrator Roles:**\n" + "\n".join(admins))
    if mods:
        parts.append("**🛡️ Moderator Roles:**\n" + "\n".join(mods))
    await i.respond("\n\n".join(parts))


def _user_avatar(user: dict) -> str:
    user_id = int(user.get("id") or 0)
    avatar = user.get("avatar")
    if avatar:
        ext = "gif" if avatar.startswith("a_") else "png"
        return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.{ext}?size=256"
    discrim = user.get("discriminator", "0")
    idx = int(discrim) % 5 if discrim != "0" else (user_id >> 22) % 6
    return f"https://cdn.discordapp.com/embed/avatars/{idx}.png"


def _snowflake_created_at(sid: int) -> int:
    return int(((sid >> 22) + 1420070400000) / 1000)


@slash("userinfo", "View user information", [
    {"name": "member", "description": "Member to inspect", "type": USER, "required": False},
])
async def userinfo(i: Interaction, member=None):
    target_id = int(str(member).strip().strip("<@!>")) if member else int(i.user_id)
    try:
        mem = await i.rest.get(f"/guilds/{i.guild_id}/members/{target_id}")
    except Exception:
        mem = None

    if not mem:
        try:
            usr = await i.rest.get(f"/users/{target_id}")
            mem = {"user": usr, "roles": []}
        except Exception:
            return await i.respond(f"❌ User `{target_id}` not found.", ephemeral=True)

    usr = mem.get("user") or {}
    avatar_url = _user_avatar(usr)
    created_ts = _snowflake_created_at(target_id)
    joined_ts = None
    if mem.get("joined_at"):
        try:
            from datetime import datetime
            joined_dt = datetime.fromisoformat(mem["joined_at"].replace("Z", "+00:00"))
            joined_ts = int(joined_dt.timestamp())
        except Exception:
            pass

    roles = [f"<@&{r}>" for r in mem.get("roles", [])]
    roles_str = ", ".join(roles) if roles else "None"
    if len(roles_str) > 1000:
        roles_str = f"{len(roles)} roles"

    bot_tag = " `[BOT]`" if usr.get("bot") else ""
    embed = {
        "title": f"👤 {usr.get('username')}{bot_tag}",
        "thumbnail": {"url": avatar_url},
        "color": 0x5865F2,
        "fields": [
            {"name": "Display Name", "value": usr.get("global_name") or usr.get("username", "—"), "inline": True},
            {"name": "User ID", "value": f"`{target_id}`", "inline": True},
            {"name": "Mention", "value": f"<@{target_id}>", "inline": True},
            {"name": "Account Created", "value": f"<t:{created_ts}:F>\n(<t:{created_ts}:R>)", "inline": True},
        ],
    }
    if joined_ts:
        embed["fields"].append({"name": "Joined Server", "value": f"<t:{joined_ts}:F>\n(<t:{joined_ts}:R>)", "inline": True})


    embed["fields"].append({"name": f"Roles ({len(mem.get('roles', []))})", "value": roles_str, "inline": False})
    await i.respond(embeds=[embed])


@slash("serverinfo", "View server information")
async def serverinfo(i: Interaction):
    try:
        g = await i.rest.get(f"/guilds/{i.guild_id}?with_counts=true")
    except Exception:
        g = i.cache.guilds.get(int(i.guild_id), {})

    gid = int(i.guild_id)
    created_ts = _snowflake_created_at(gid)
    icon = g.get("icon")
    icon_url = f"https://cdn.discordapp.com/icons/{gid}/{icon}.png?size=256" if icon else None

    channels = g.get("channels", [])
    text_ch = sum(1 for c in channels if c.get("type") in (0, 5))
    voice_ch = sum(1 for c in channels if c.get("type") == 2)
    cat_ch = sum(1 for c in channels if c.get("type") == 4)

    embed = {
        "title": f"🏰 {g.get('name', 'Server Info')}",
        "color": 0x5865F2,
        "fields": [
            {"name": "Server ID", "value": f"`{gid}`", "inline": True},
            {"name": "Owner", "value": f"<@{g.get('owner_id')}>", "inline": True},
            {"name": "Created At", "value": f"<t:{created_ts}:F>\n(<t:{created_ts}:R>)", "inline": True},
            {"name": "Members", "value": f"👥 **{g.get('approximate_member_count') or g.get('member_count', '—')}** total", "inline": True},
            {"name": "Channels", "value": f"💬 {text_ch} text • 🔊 {voice_ch} voice • 📁 {cat_ch} categories", "inline": True},
            {"name": "Roles Count", "value": f"🎭 {len(g.get('roles', []))} roles", "inline": True},
            {"name": "Boost Status", "value": f"🚀 Tier {g.get('premium_tier', 0)} ({g.get('premium_subscription_count', 0)} boosts)", "inline": True},
        ],
    }
    if icon_url:
        embed["thumbnail"] = {"url": icon_url}
    await i.respond(embeds=[embed])


@slash("botstats", "View bot stats and system diagnostics")
async def botstats(i: Interaction):
    uptime_s = int(time.time() - state.start_time)
    days, rem = divmod(uptime_s, 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)
    uptime_str = f"{days}d {hours}h {mins}m {secs}s" if days else f"{hours}h {mins}m {secs}s"

    active_guilds = len(i.cache.guilds)
    cached_members = len(i.cache.members)
    active_players = sum(1 for p in lavalink.players.values() if p.playing)

    embed = {
        "title": "🤖 Bot Status & Diagnostics",
        "color": 0x57F287,
        "fields": [
            {"name": "⏱️ Uptime", "value": f"`{uptime_str}`", "inline": True},
            {"name": "🌐 Active Servers", "value": f"`{active_guilds}`", "inline": True},
            {"name": "👥 Cached Members", "value": f"`{cached_members}`", "inline": True},
            {"name": "🎵 Voice Players", "value": f"`{active_players}` active", "inline": True},
            {"name": "🐍 Python Version", "value": f"`{sys.version.split()[0]}`", "inline": True},
            {"name": "🗄️ Database", "value": "PostgreSQL & Redis connected", "inline": True},
        ],
    }
    await i.respond(embeds=[embed])


@slash("avatar", "View a user's full-size avatar", [
    {"name": "member", "description": "Member to inspect", "type": USER, "required": False},
])
async def avatar_cmd(i: Interaction, member=None):
    target_id = int(str(member).strip().strip("<@!>")) if member else int(i.user_id)
    try:
        usr = await i.rest.get(f"/users/{target_id}")
    except Exception:
        return await i.respond(f"❌ User `{target_id}` not found.", ephemeral=True)

    av = usr.get("avatar")
    if av:
        ext = "gif" if av.startswith("a_") else "png"
        url = f"https://cdn.discordapp.com/avatars/{target_id}/{av}.{ext}?size=1024"
        links = [
            f"[PNG](https://cdn.discordapp.com/avatars/{target_id}/{av}.png?size=1024)",
            f"[WEBP](https://cdn.discordapp.com/avatars/{target_id}/{av}.webp?size=1024)",
        ]
        if ext == "gif":
            links.insert(0, f"[GIF]({url})")
        desc = " • ".join(links)
    else:
        url = _user_avatar(usr)
        desc = f"[Default Avatar]({url})"

    embed = {
        "title": f"🖼️ Avatar — {usr.get('username', target_id)}",
        "description": desc,
        "image": {"url": url},
        "color": 0x5865F2,
    }
    await i.respond(embeds=[embed])


@slash("banner", "View a user's profile banner", [
    {"name": "member", "description": "Member to inspect", "type": USER, "required": False},
])
async def banner_cmd(i: Interaction, member=None):
    target_id = int(str(member).strip().strip("<@!>")) if member else int(i.user_id)
    try:
        usr = await i.rest.get(f"/users/{target_id}")
    except Exception:
        return await i.respond(f"❌ User `{target_id}` not found.", ephemeral=True)

    bn = usr.get("banner")
    color = usr.get("accent_color") or 0x5865F2
    if not bn:
        hex_str = f"#{color:06X}" if usr.get("accent_color") else "None"
        return await i.respond(
            f"ℹ️ **{usr.get('username', target_id)}** does not have a custom banner "
            f"(Accent color: `{hex_str}`).",
            ephemeral=True,
        )
    ext = "gif" if bn.startswith("a_") else "png"
    url = f"https://cdn.discordapp.com/banners/{target_id}/{bn}.{ext}?size=1024"
    embed = {
        "title": f"🎨 Banner — {usr.get('username', target_id)}",
        "description": f"[Download Banner]({url})",
        "image": {"url": url},
        "color": color,
    }
    await i.respond(embeds=[embed])


@slash("roleinfo", "Inspect a server role", [
    {"name": "role", "description": "Role to inspect", "type": ROLE, "required": True},
])
async def roleinfo_cmd(i: Interaction, role):
    rid = int(str(role).strip().strip("<@&>"))
    gid = int(i.guild_id)
    r = i.cache.roles.get((gid, rid))
    if not r:
        try:
            roles = await i.rest.get(f"/guilds/{gid}/roles")
            for item in roles:
                i.cache.roles[(gid, int(item["id"]))] = item
            r = i.cache.roles.get((gid, rid))
        except Exception:
            r = None
    if not r:
        return await i.respond(f"❌ Role `{rid}` not found.", ephemeral=True)

    color_val = int(r.get("color") or 0)
    created_ts = _snowflake_created_at(rid)
    member_count = sum(
        1 for (g, _), m in i.cache.members.items()
        if g == gid and str(rid) in [str(x) for x in m.get("roles", [])]
    )
    embed = {
        "title": f"🎭 Role: {r.get('name', 'Unknown')}",
        "color": color_val or 0x5865F2,
        "fields": [
            {"name": "Role ID", "value": f"`{rid}`", "inline": True},
            {"name": "Mention", "value": f"<@&{rid}>", "inline": True},
            {"name": "Color", "value": f"`#{color_val:06X}`", "inline": True},
            {"name": "Position", "value": f"`{r.get('position', 0)}`", "inline": True},
            {"name": "Hoisted", "value": "✅ Yes" if r.get("hoist") else "❌ No", "inline": True},
            {"name": "Mentionable", "value": "✅ Yes" if r.get("mentionable") else "❌ No", "inline": True},
            {"name": "Cached Members", "value": f"👥 `{member_count}`", "inline": True},
            {"name": "Created", "value": f"<t:{created_ts}:D> (<t:{created_ts}:R>)", "inline": True},
        ],
    }
    await i.respond(embeds=[embed])


@slash("snipe", "View the most recently deleted message in this channel")
async def snipe_cmd(i: Interaction):
    data = state.snipe_cache.get(int(i.channel_id))
    if not data:
        return await i.respond("📭 Nothing to snipe in this channel.")
    embed = {
        "title": "🎯 Sniped Deleted Message",
        "description": data.get("content") or "*[No text content]*",
        "color": 0xED4245,
        "fields": [
            {"name": "Author", "value": f"<@{data['author_id']}> ({data['author_name']})", "inline": True},
            {"name": "Deleted", "value": f"<t:{data['timestamp']}:R>", "inline": True},
        ],
    }
    await i.respond(embeds=[embed])


@slash("editsnipe", "View the most recently edited message in this channel")
async def editsnipe_cmd(i: Interaction):
    data = state.editsnipe_cache.get(int(i.channel_id))
    if not data:
        return await i.respond("📭 No recently edited messages cached in this channel.")
    embed = {
        "title": "✏️ Sniped Edited Message",
        "color": 0xFEE75C,
        "fields": [
            {"name": "Author", "value": f"<@{data['author_id']}> ({data['author_name']})", "inline": True},
            {"name": "Edited", "value": f"<t:{data['timestamp']}:R>", "inline": True},
            {"name": "Before", "value": data.get("before", "—")[:1000], "inline": False},
            {"name": "After", "value": data.get("after", "—")[:1000], "inline": False},
        ],
    }
    await i.respond(embeds=[embed])


@slash("say", "Make the bot send a message", [
    {"name": "message", "description": "Message text", "type": STR, "required": True},
])
async def say_cmd(i: Interaction, message: str):
    if not await can_use_staff_commands(i, db.pool):
        return await i.respond("❌ Only configured staff can use `tct!say`.", ephemeral=True)
    await i.respond(message, allowed_mentions={"parse": []})


@slash("embed", "Post a custom embed (Title | Description)", [
    {"name": "content", "description": "Title | Description", "type": STR, "required": True},
])
async def embed_cmd(i: Interaction, content: str):
    if not await can_use_staff_commands(i, db.pool):
        return await i.respond("❌ Only configured staff can use `tct!embed`.", ephemeral=True)
    if "|" in content:
        title, desc = [p.strip() for p in content.split("|", 1)]
    else:
        title, desc = None, content.strip()
    embed = {"description": desc, "color": 0x5865F2}
    if title:
        embed["title"] = title[:256]
    await i.respond(embeds=[embed])


@slash("steal", "Clone a custom emoji into this server", [
    {"name": "emoji", "description": "Custom emoji (<:name:id> or <a:name:id>)", "type": STR, "required": True},
    {"name": "name", "description": "Optional new emoji name", "type": STR, "required": False},
])
async def steal_cmd(i: Interaction, emoji: str, name: str | None = None):
    if not await can_use_staff_commands(i, db.pool):
        return await i.respond("❌ Only configured staff can steal emojis.", ephemeral=True)
    import base64
    import re
    m = re.match(r"^<(a?):([a-zA-Z0-9_]+):(\d+)>$", emoji.strip())
    if not m:
        return await i.respond("❌ Provide a custom Discord emoji like `<:name:123456>`.", ephemeral=True)
    animated, orig_name, emoji_id = bool(m.group(1)), m.group(2), m.group(3)
    final_name = (name.strip() if name else orig_name)[:32]
    ext = "gif" if animated else "png"
    url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}"
    try:
        async with i.rest.session.get(url) as resp:
            if resp.status != 200:
                return await i.respond("❌ Could not download that emoji image.", ephemeral=True)
            raw_bytes = await resp.read()
        b64 = base64.b64encode(raw_bytes).decode("ascii")
        data_uri = f"data:image/{ext};base64,{b64}"
        created = await i.rest.post(
            f"/guilds/{i.guild_id}/emojis",
            json={"name": final_name, "image": data_uri},
            reason=f"Stolen by {i.user_id}",
        )
        prefix = "a" if created.get("animated") else ""
        await i.respond(f"✅ Added emoji <{prefix}:{created['name']}:{created['id']}> (`:{created['name']}:`)!")
    except Exception as e:
        await i.respond(f"❌ Failed to add emoji: `{e}`", ephemeral=True)


