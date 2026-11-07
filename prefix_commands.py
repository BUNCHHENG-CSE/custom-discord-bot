import shlex
from dataclasses import dataclass

from interactions import MessageContext, resolve_handler
from modules.welcome import configure_greeting
from database import db
from helpers.permissions import can_manage_bot, can_manage_bot_async, can_use_staff_commands


PREFIX = "tct!"


@dataclass(frozen=True)
class Command:
    handler: str
    usage: str
    fields: tuple[str, ...] = ()
    rest_field: str | None = None
    integer_fields: tuple[str, ...] = ()
    access: str = "public"  # public, staff, or admin


COMMANDS = {
    "modban": Command("ban", "tct!modban @member [reason]", ("member",), "reason", access="staff"),
    "modunban": Command("unban", "tct!modunban <user_id> [reason]", ("user_id",), "reason", access="staff"),
    "modkick": Command("kick", "tct!modkick @member [reason]", ("member",), "reason", access="staff"),
    "modtimeout": Command("timeout", "tct!modtimeout @member <10m|1h|1d> [reason]",
                          ("member", "duration"), "reason", access="staff"),
    "moduntimeout": Command("untimeout", "tct!moduntimeout @member [reason]", ("member",), "reason", access="staff"),
    "modwarn": Command("warn", "tct!modwarn @member <reason>", ("member",), "reason", access="staff"),
    "modwarnings": Command("warnings", "tct!modwarnings @member", ("member",), access="staff"),
    "moddelwarn": Command("delwarn", "tct!moddelwarn <case_id>", ("case_id",), integer_fields=("case_id",), access="staff"),
    "modclearwarns": Command("clearwarns", "tct!modclearwarns @member", ("member",), access="staff"),
    "modpurge": Command("purge", "tct!modpurge <1-100>", ("amount",),
                        integer_fields=("amount",), access="staff"),
    "lock": Command("lock", "tct!lock [#channel]", rest_field="channel", access="staff"),
    "unlock": Command("unlock", "tct!unlock [#channel]", rest_field="channel", access="staff"),
    "slowmode": Command("slowmode", "tct!slowmode <duration|off>", ("duration",), access="staff"),
    "nick": Command("nick", "tct!nick @member [nickname]", ("member",), "nickname", access="staff"),
    "ticketpanel": Command("ticket:panel", "tct!ticketpanel [panel]", rest_field="panel", access="admin"),
    "ticketclaim": Command("ticket:claim", "tct!ticketclaim", access="staff"),
    "ticketclose": Command("ticket:close", "tct!ticketclose"),
    "ticketadd": Command("ticket:add", "tct!ticketadd @member", ("member",), access="staff"),
    "ticketremove": Command("ticket:remove", "tct!ticketremove @member", ("member",), access="staff"),
    "ticketrename": Command("ticket:rename", "tct!ticketrename <name>", rest_field="name", access="staff"),
    "giveawaystart": Command("giveaway:start",
                             "tct!giveawaystart <duration> <winners> <prize>",
                             ("duration", "winners"), "prize", ("winners",), "staff"),
    "giveawayreroll": Command("giveaway:reroll", "tct!giveawayreroll <message_id>",
                              ("message_id",), access="staff"),
    "giveawayend": Command("giveaway:end", "tct!giveawayend <message_id>",
                            ("message_id",), access="staff"),
    "giveawaylist": Command("giveaway:list", "tct!giveawaylist"),
    "musicjoin": Command("music:join", "tct!musicjoin [#voice-channel]", rest_field="channel"),
    "musicleave": Command("music:leave", "tct!musicleave"),
    "musicplay": Command("music:play", "tct!musicplay <URL or search>", rest_field="query"),
    "play": Command("music:play", "tct!play <URL or search>", rest_field="query"),
    "p": Command("music:play", "tct!p <URL or search>", rest_field="query"),
    "musicpause": Command("music:pause", "tct!musicpause"),
    "pause": Command("music:pause", "tct!pause"),
    "musicskip": Command("music:skip", "tct!musicskip"),
    "skip": Command("music:skip", "tct!skip"),
    "s": Command("music:skip", "tct!s"),
    "musicstop": Command("music:stop", "tct!musicstop"),
    "stop": Command("music:stop", "tct!stop"),
    "musicnowplaying": Command("music:nowplaying", "tct!musicnowplaying"),
    "np": Command("music:nowplaying", "tct!np"),
    "musicqueue": Command("music:queue", "tct!musicqueue"),
    "queue": Command("music:queue", "tct!queue"),
    "musicvolume": Command("music:volume", "tct!musicvolume <0-200>", ("volume",), integer_fields=("volume",)),
    "volume": Command("music:volume", "tct!volume <0-200>", ("volume",), integer_fields=("volume",)),
    "musicloop": Command("music:loop", "tct!musicloop <off|track|queue>", ("mode",)),
    "loop": Command("music:loop", "tct!loop <off|track|queue>", ("mode",)),
    "musicshuffle": Command("music:shuffle", "tct!musicshuffle"),
    "shuffle": Command("music:shuffle", "tct!shuffle"),
    "musicremove": Command("music:remove", "tct!musicremove <index>", ("index",), integer_fields=("index",)),
    "musicclear": Command("music:clear", "tct!musicclear"),
    "musicseek": Command("music:seek", "tct!musicseek <time>", ("time",)),
    "musicfilter": Command("music:filter", "tct!musicfilter <preset|clear>", ("preset",)),
    "musicradio": Command("music:radio", "tct!musicradio [on|off]", rest_field="mode"),
    "radio": Command("music:radio", "tct!radio [on|off]", rest_field="mode"),
    "musichistory": Command("music:history", "tct!musichistory"),
    "history": Command("music:history", "tct!history"),
    "music247": Command("music:247", "tct!music247 [on|off]", rest_field="mode"),
    "247": Command("music:247", "tct!247 [on|off]", rest_field="mode"),
    "aiask": Command("ai:ask", "tct!aiask <question>", rest_field="prompt"),
    "aiimagine": Command("ai:imagine", "tct!aiimagine <prompt>", rest_field="prompt"),
    "feedadd": Command("feed:add", "tct!feedadd <URL>", rest_field="url", access="admin"),
    "feedremove": Command("feed:remove", "tct!feedremove <id>", ("id",), integer_fields=("id",), access="admin"),
    "feedlist": Command("feed:list", "tct!feedlist"),
    "userinfo": Command("userinfo", "tct!userinfo [@member]", rest_field="member"),
    "avatar": Command("avatar", "tct!avatar [@member]", rest_field="member"),
    "banner": Command("banner", "tct!banner [@member]", rest_field="member"),
    "roleinfo": Command("roleinfo", "tct!roleinfo @Role", ("role",)),
    "serverinfo": Command("serverinfo", "tct!serverinfo"),
    "botstats": Command("botstats", "tct!botstats"),
    "snipe": Command("snipe", "tct!snipe"),
    "editsnipe": Command("editsnipe", "tct!editsnipe"),
    "say": Command("say", "tct!say <message>", rest_field="message", access="staff"),
    "embed": Command("embed", "tct!embed <title> | <description>", rest_field="content", access="staff"),
    "steal": Command("steal", "tct!steal <custom_emoji> [name]", ("emoji",), "name", access="staff"),
    "rank": Command("rank", "tct!rank [@member]", rest_field="member"),
    "level": Command("rank", "tct!level [@member]", rest_field="member"),
    "leaderboard": Command("leaderboard", "tct!leaderboard"),
    "lb": Command("leaderboard", "tct!lb"),
    "levelreward": Command("levelreward", "tct!levelreward <level> @Role", ("level", "role"), integer_fields=("level",), access="admin"),
    "afk": Command("afk", "tct!afk [reason]", rest_field="reason"),
    "remind": Command("remind", "tct!remind <duration> <text>", ("duration",), "text"),
    "remindme": Command("remind", "tct!remindme <duration> <text>", ("duration",), "text"),
    "setsuggest": Command("setsuggest", "tct!setsuggest #channel", ("channel",), access="admin"),
    "suggest": Command("suggest", "tct!suggest <idea>", rest_field="idea"),
    "setstarboard": Command("setstarboard", "tct!setstarboard #channel [threshold]", ("channel",), "threshold", integer_fields=("threshold",), access="admin"),
    "poll": Command("poll", "tct!poll <question> | <option1> | <option2> ...", rest_field="spec"),
    "tictactoe": Command("tictactoe", "tct!tictactoe @member", ("member",)),
    "ttt": Command("tictactoe", "tct!ttt @member", ("member",)),
    "rps": Command("rps", "tct!rps [@member]", rest_field="member"),
    "8ball": Command("8ball", "tct!8ball <question>", rest_field="question"),
    "coinflip": Command("coinflip", "tct!coinflip"),
    "cf": Command("coinflip", "tct!cf"),
    "roll": Command("roll", "tct!roll [dice]", rest_field="dice"),
    "tag": Command("tag:get", "tct!tag <name>", ("name",)),
    "tagadd": Command("tag:add", "tct!tagadd <name> <content>", ("name",), "content", access="staff"),
    "tagdel": Command("tag:delete", "tct!tagdel <name>", ("name",), access="staff"),
    "taglist": Command("tag:list", "tct!taglist"),
    "ping": Command("ping", "tct!ping"),
    "setlog": Command("setlog", "tct!setlog #channel", ("channel",), access="admin"),
    "setmodrole": Command("setmodrole", "tct!setmodrole @Role [mod|admin]", ("role",), rest_field="role_type", access="admin"),
    "setadminrole": Command("setadminrole", "tct!setadminrole @Role", ("role",), access="admin"),
    "removemodrole": Command("removemodrole", "tct!removemodrole @Role", ("role",), access="admin"),
    "removeadminrole": Command("removemodrole", "tct!removeadminrole @Role", ("role",), access="admin"),
    "modroles": Command("modroles", "tct!modroles", access="admin"),
    "adminroles": Command("modroles", "tct!adminroles", access="admin"),
    "autorole": Command("autorole", "tct!autorole <@Role|off>", rest_field="value", access="admin"),
    "reactionrole": Command("reactionrole", "tct!reactionrole <message_id> <emoji> @Role",
                            ("message_id", "emoji", "role"), access="admin"),
    "removereactionrole": Command("removereactionrole",
                                  "tct!removereactionrole <message_id> <emoji>",
                                  ("message_id", "emoji"), access="admin"),
    "reactionroles": Command("reactionroles", "tct!reactionroles", access="admin"),
    "welcome": Command("welcome", "tct!welcome [test|message|title|color|footer|thumbnail|fields|ping|image|off|reset]", access="admin"),
    "setwelcome": Command("setwelcome", "tct!setwelcome #channel", ("channel",), access="admin"),
    "welcomechannel": Command("setwelcome", "tct!welcomechannel #channel", ("channel",), access="admin"),
    "goodbye": Command("goodbye", "tct!goodbye [test|message|title|color|footer|thumbnail|fields|ping|image|off|reset]", access="admin"),
    "setgoodbye": Command("setgoodbye", "tct!setgoodbye #channel", ("channel",), access="admin"),
    "goodbyechannel": Command("setgoodbye", "tct!goodbyechannel #channel", ("channel",), access="admin"),
    "vpnregions": Command("vpn:regions", "tct!vpnregions"),
    "vpncreate": Command("vpn:create", "tct!vpncreate <region> [device]", ("region",), "device"),
    "vpnstatus": Command("vpn:status", "tct!vpnstatus"),
    "vpnprofile": Command("vpn:my-profile", "tct!vpnprofile"),
    "vpnrevoke": Command("vpn:revoke", "tct!vpnrevoke <peer_id>", ("peer_id",)),
    "help": Command("help", "tct!help [category]", rest_field="category"),
}


def _snowflake(value: str) -> str:
    value = value.strip()
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1].lstrip("@#!&")
    if not value.isdigit():
        raise ValueError(f"Expected a Discord mention or ID, got `{value}`.")
    return value


def _arguments(command: Command, text: str) -> dict:
    try:
        tokens = shlex.split(text)
    except ValueError as exc:
        raise ValueError(f"Invalid quotes: {exc}") from exc

    required = len(command.fields)
    if len(tokens) < required:
        raise ValueError(f"Usage: `{command.usage}`")
    if not command.rest_field and len(tokens) > required:
        raise ValueError(f"Too many arguments. Usage: `{command.usage}`")

    result = {name: tokens[index] for index, name in enumerate(command.fields)}
    if command.rest_field:
        remainder = " ".join(tokens[required:]).strip()
        optional_rest = {"reason", "panel", "channel", "member", "nickname", "mode", "dice", "threshold", "role_type", "name", "category", "device"}
        if not remainder and command.rest_field not in optional_rest:
            raise ValueError(f"Usage: `{command.usage}`")
        if remainder:
            result[command.rest_field] = remainder

    for field in ("member", "channel", "role", "message_id"):
        if field in result:
            result[field] = _snowflake(result[field])
    for field in command.integer_fields:
        if field in result:
            try:
                result[field] = int(result[field])
            except ValueError as exc:
                raise ValueError(f"`{field}` must be a number. Usage: `{command.usage}`") from exc
    return result


async def handle_prefix_message(rest, message: dict, cache) -> bool:
    content = (message.get("content") or "").strip()
    if not content.lower().startswith(PREFIX):
        return False

    parts = content.split(maxsplit=1)
    head = parts[0]
    tail = parts[1] if len(parts) > 1 else ""
    name = head[len(PREFIX):].lower()
    ctx = MessageContext(rest, message, cache)

    command = COMMANDS.get(name)
    if not command:
        await ctx.respond("Unknown command. Use `tct!help` to see all commands.")
        return True

    if name != "help":
        from modules.utility import is_command_disabled
        if await is_command_disabled(ctx.guild_id, name):
            await ctx.respond(
                f"❌ `tct!{name}` is disabled in this server. "
                "An administrator can re-enable it via `tct!help`."
            )
            return True

    if command.access == "admin" and not await can_manage_bot_async(ctx, db.pool):
        await ctx.respond("❌ Only the server owner or an administrator can use this command.")
        return True
    if command.access == "staff" and not await can_use_staff_commands(ctx, db.pool):
        await ctx.respond(
            "❌ Only the server owner, administrators, or a configured moderator role "
            "can use this command."
        )
        return True


    if name in ("welcome", "welcomedesign"):
        await configure_greeting(ctx, "welcome", tail)
        return True
    if name in ("setwelcome", "welcomechannel"):
        if not tail.strip():
            await ctx.respond("❌ Mention a channel. Usage: `tct!setwelcome #channel`")
            return True
        await configure_greeting(ctx, "welcome", tail)
        return True
    if name in ("goodbye", "goodbyedesign"):
        await configure_greeting(ctx, "goodbye", tail)
        return True
    if name in ("setgoodbye", "goodbyechannel"):
        if not tail.strip():
            await ctx.respond("❌ Mention a channel. Usage: `tct!setgoodbye #channel`")
            return True
        await configure_greeting(ctx, "goodbye", tail)
        return True

    handler = resolve_handler(command.handler)
    if not handler:
        await ctx.respond("❌ That command is currently unavailable.")
        return True

    try:
        kwargs = _arguments(command, tail)
        await handler(ctx, **kwargs)
    except ValueError as exc:
        await ctx.respond(f"❌ {exc}")
    except Exception:
        await ctx.respond("❌ Command failed. Check the bot log for details.")
        raise
    return True
