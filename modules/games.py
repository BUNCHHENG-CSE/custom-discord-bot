"""Interactive button games, polls, and mini-games."""

import random
import re
from interactions import slash, Interaction, USER, STR

# In-memory active states for interactive button games & polls
_polls: dict[int, dict] = {}
_ttt_games: dict[int, dict] = {}
_rps_games: dict[int, dict] = {}

_8BALL_ANSWERS = [
    "🟢 It is certain.",
    "🟢 Without a doubt.",
    "🟢 Yes — definitely!",
    "🟢 Most likely.",
    "🟢 Outlook good.",
    "🟡 Reply hazy, try again.",
    "🟡 Ask again later.",
    "🟡 Cannot predict now.",
    "🔴 Don't count on it.",
    "🔴 My reply is no.",
    "🔴 My sources say no.",
    "🔴 Very doubtful.",
]


@slash("coinflip", "Flip a coin")
async def coinflip(i: Interaction):
    side = random.choice(("Heads 🪙", "Tails 🪙"))
    await i.respond(f"🎲 <@{i.user_id}> flipped a coin and got **{side}**!")


@slash("roll", "Roll dice (e.g. 2d6, d20, or 100)", [
    {"name": "dice", "description": "Dice notation like 2d6 or max number (default: 1d6)", "type": STR, "required": False},
])
async def roll(i: Interaction, dice: str = "1d6"):
    raw = (dice or "1d6").strip().lower()
    if raw.isdigit():
        upper = max(2, min(int(raw), 1_000_000))
        val = random.randint(1, upper)
        return await i.respond(f"🎲 <@{i.user_id}> rolled **{val}** (1–{upper}).")

    m = re.match(r"^(\d*)d(\d+)$", raw)
    if not m:
        return await i.respond("❌ Invalid format. Use `1d6`, `2d20`, or a number like `100`.", ephemeral=True)
    count = int(m.group(1) or 1)
    sides = int(m.group(2))
    if not (1 <= count <= 25 and 2 <= sides <= 10000):
        return await i.respond("❌ Roll up to `25` dice with `2`–`10000` sides.", ephemeral=True)
    rolls = [random.randint(1, sides) for _ in range(count)]
    if count == 1:
        await i.respond(f"🎲 <@{i.user_id}> rolled `1d{sides}` and got **{rolls[0]}**!")
    else:
        rolls_str = ", ".join(str(r) for r in rolls)
        await i.respond(f"🎲 <@{i.user_id}> rolled `{count}d{sides}`: [{rolls_str}] = **{sum(rolls)}**!")


@slash("8ball", "Ask the magic 8-ball a question", [
    {"name": "question", "description": "Your question", "type": STR, "required": True},
])
async def eightball(i: Interaction, question: str):
    ans = random.choice(_8BALL_ANSWERS)
    embed = {
        "title": "🎱 Magic 8-Ball",
        "color": 0x5865F2,
        "fields": [
            {"name": "Question", "value": question[:500], "inline": False},
            {"name": "Answer", "value": ans, "inline": False},
        ],
    }
    await i.respond(embeds=[embed])


# =====================================================================
# Interactive Polls (tct!poll Question | Option 1 | Option 2 ...)
# =====================================================================
def _render_poll_embed(poll: dict) -> dict:
    total = sum(len(voters) for voters in poll["votes"].values())
    lines = []
    for idx, opt in enumerate(poll["options"]):
        count = len(poll["votes"][idx])
        pct = int(round((count / total) * 100)) if total > 0 else 0
        filled = pct // 10
        bar = "🟦" * filled + "⬜" * (10 - filled)
        lines.append(f"**{idx + 1}. {opt}**\n{bar} `{count}` vote(s) ({pct}%)")
    return {
        "title": f"📊 {poll['question']}",
        "description": "\n\n".join(lines),
        "color": 0x5865F2,
        "footer": {"text": f"Total votes: {total} • Hosted by {poll['author']}"},
    }


def _poll_components(options: list[str]) -> list[dict]:
    buttons = [
        {
            "type": 2,
            "style": 1,
            "label": f"{idx + 1}. {opt[:60]}",
            "custom_id": f"game:poll:{idx}",
        }
        for idx, opt in enumerate(options[:5])
    ]
    return [{"type": 1, "components": buttons}]


@slash("poll", "Create an interactive button poll (Question | Opt1 | Opt2...)", [
    {"name": "spec", "description": "Question | Option 1 | Option 2 ...", "type": STR, "required": True},
])
async def poll_cmd(i: Interaction, spec: str):
    parts = [p.strip() for p in spec.split("|") if p.strip()]
    if len(parts) < 3:
        return await i.respond(
            "❌ Provide a question and at least 2 options separated by `|`.\n"
            "Example: `tct!poll Best game? | Minecraft | Valorant | Terraria`",
            ephemeral=True,
        )
    question = parts[0][:250]
    options = parts[1:6]
    poll_data = {
        "question": question,
        "options": options,
        "votes": {idx: set() for idx in range(len(options))},
        "author": i.user.get("username", "User"),
    }
    embed = _render_poll_embed(poll_data)
    components = _poll_components(options)
    msg = await i.rest.post(
        f"/channels/{i.channel_id}/messages",
        json={"embeds": [embed], "components": components},
    )
    _polls[int(msg["id"])] = poll_data
    if i.token:
        await i.respond("✅ Poll created!", ephemeral=True)


# =====================================================================
# Interactive Rock-Paper-Scissors (tct!rps [@member])
# =====================================================================
_RPS_CHOICES = {"rock": "🪨 Rock", "paper": "📄 Paper", "scissors": "✂️ Scissors"}
_RPS_BEATS = {"rock": "scissors", "scissors": "paper", "paper": "rock"}


def _rps_components(disabled: bool = False) -> list[dict]:
    return [{
        "type": 1,
        "components": [
            {"type": 2, "style": 1, "label": "Rock", "emoji": {"name": "🪨"}, "custom_id": "game:rps:rock", "disabled": disabled},
            {"type": 2, "style": 1, "label": "Paper", "emoji": {"name": "📄"}, "custom_id": "game:rps:paper", "disabled": disabled},
            {"type": 2, "style": 1, "label": "Scissors", "emoji": {"name": "✂️"}, "custom_id": "game:rps:scissors", "disabled": disabled},
        ],
    }]


@slash("rps", "Play Rock-Paper-Scissors with the bot or a member", [
    {"name": "member", "description": "Optional opponent", "type": USER, "required": False},
])
async def rps_cmd(i: Interaction, member=None):
    p1 = int(i.user_id)
    p2 = int(str(member).strip().strip("<@!>")) if member else None
    if p2 == p1:
        return await i.respond("❌ You can't challenge yourself!", ephemeral=True)

    desc = (
        f"<@{p1}> vs <@{p2}> — both players click a button below!"
        if p2 else f"<@{p1}> vs **Bot** — choose your move below!"
    )
    embed = {"title": "🪨📄✂️ Rock Paper Scissors", "description": desc, "color": 0x5865F2}
    msg = await i.rest.post(
        f"/channels/{i.channel_id}/messages",
        json={"embeds": [embed], "components": _rps_components()},
    )
    _rps_games[int(msg["id"])] = {"p1": p1, "p2": p2, "moves": {}}
    if i.token:
        await i.respond("✅ Game started!", ephemeral=True)


# =====================================================================
# Interactive Tic-Tac-Toe (tct!tictactoe @member)
# =====================================================================
_WIN_LINES = (
    (0, 1, 2), (3, 4, 5), (6, 7, 8),
    (0, 3, 6), (1, 4, 7), (2, 5, 8),
    (0, 4, 8), (2, 4, 6),
)


def _ttt_winner(board: list[int]) -> int | None:
    for a, b, c in _WIN_LINES:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    if all(cell != 0 for cell in board):
        return 0  # draw
    return None


def _ttt_components(board: list[int], ended: bool = False) -> list[dict]:
    rows = []
    for r in range(3):
        comps = []
        for c in range(3):
            idx = r * 3 + c
            val = board[idx]
            label = "❌" if val == 1 else ("⭕" if val == 2 else "·")
            style = 4 if val == 1 else (3 if val == 2 else 2)
            comps.append({
                "type": 2,
                "style": style,
                "label": label,
                "custom_id": f"game:ttt:{idx}",
                "disabled": ended or val != 0,
            })
        rows.append({"type": 1, "components": comps})
    return rows


@slash("tictactoe", "Play 2-player Tic-Tac-Toe", [
    {"name": "member", "description": "Opponent to challenge", "type": USER, "required": True},
])
async def tictactoe_cmd(i: Interaction, member):
    p1 = int(i.user_id)
    p2 = int(str(member).strip().strip("<@!>"))
    if p1 == p2:
        return await i.respond("❌ Challenge another member to play Tic-Tac-Toe!", ephemeral=True)
    board = [0] * 9
    content = f"🎮 **Tic-Tac-Toe:** <@{p1}> (❌) vs <@{p2}> (⭕)\n👉 Turn: <@{p1}> (❌)"
    msg = await i.rest.post(
        f"/channels/{i.channel_id}/messages",
        json={"content": content, "components": _ttt_components(board)},
    )
    _ttt_games[int(msg["id"])] = {"p1": p1, "p2": p2, "turn": 1, "board": board}
    if i.token:
        await i.respond("✅ Game started!", ephemeral=True)


async def handle_component(i: Interaction):
    cid = i.custom_id or ""
    msg_id = int(i.message.get("id") or 0)
    uid = int(i.user_id)

    # 1. Poll vote
    if cid.startswith("game:poll:"):
        poll = _polls.get(msg_id)
        if not poll:
            return await i.respond("❌ This poll is no longer active.", ephemeral=True)
        opt_idx = int(cid.split(":")[2])
        already_in = uid in poll["votes"].get(opt_idx, set())
        for voters in poll["votes"].values():
            voters.discard(uid)
        if not already_in:
            poll["votes"][opt_idx].add(uid)
        return await i.update(embeds=[_render_poll_embed(poll)], components=_poll_components(poll["options"]))

    # 2. RPS move
    if cid.startswith("game:rps:"):
        game = _rps_games.get(msg_id)
        if not game:
            return await i.respond("❌ This game has expired.", ephemeral=True)
        choice = cid.split(":")[2]
        if game["p2"] is None:
            if uid != game["p1"]:
                return await i.respond("❌ Start your own game with `tct!rps`!", ephemeral=True)
            bot_choice = random.choice(list(_RPS_CHOICES.keys()))
            if choice == bot_choice:
                outcome = "🤝 **It's a tie!**"
            elif _RPS_BEATS[choice] == bot_choice:
                outcome = f"🎉 **<@{uid}> wins!**"
            else:
                outcome = "🤖 **Bot wins!**"
            desc = f"<@{uid}> chose **{_RPS_CHOICES[choice]}**\n**Bot** chose **{_RPS_CHOICES[bot_choice]}**\n\n{outcome}"
            _rps_games.pop(msg_id, None)
            return await i.update(
                embeds=[{"title": "🪨📄✂️ Rock Paper Scissors", "description": desc, "color": 0x57F287}],
                components=_rps_components(disabled=True),
            )
        else:
            if uid not in (game["p1"], game["p2"]):
                return await i.respond("❌ You are not a player in this match.", ephemeral=True)
            game["moves"][uid] = choice
            if len(game["moves"]) < 2:
                return await i.respond(f"✅ You locked in **{_RPS_CHOICES[choice]}**! Waiting for opponent...", ephemeral=True)
            p1, p2 = game["p1"], game["p2"]
            m1, m2 = game["moves"][p1], game["moves"][p2]
            if m1 == m2:
                outcome = "🤝 **It's a tie!**"
            elif _RPS_BEATS[m1] == m2:
                outcome = f"🎉 **<@{p1}> wins!**"
            else:
                outcome = f"🎉 **<@{p2}> wins!**"
            desc = f"<@{p1}> chose **{_RPS_CHOICES[m1]}**\n<@{p2}> chose **{_RPS_CHOICES[m2]}**\n\n{outcome}"
            _rps_games.pop(msg_id, None)
            return await i.update(
                embeds=[{"title": "🪨📄✂️ Rock Paper Scissors", "description": desc, "color": 0x57F287}],
                components=_rps_components(disabled=True),
            )

    # 3. Tic-Tac-Toe move
    if cid.startswith("game:ttt:"):
        game = _ttt_games.get(msg_id)
        if not game:
            return await i.respond("❌ This game has expired.", ephemeral=True)
        expected_uid = game["p1"] if game["turn"] == 1 else game["p2"]
        if uid != expected_uid:
            return await i.respond("❌ It's not your turn!", ephemeral=True)
        idx = int(cid.split(":")[2])
        if game["board"][idx] != 0:
            return await i.respond("❌ That square is already taken.", ephemeral=True)
        game["board"][idx] = game["turn"]
        winner = _ttt_winner(game["board"])
        p1, p2 = game["p1"], game["p2"]
        if winner is not None:
            _ttt_games.pop(msg_id, None)
            if winner == 0:
                status = "🤝 **Draw!** Nobody won."
            else:
                win_uid = p1 if winner == 1 else p2
                mark = "❌" if winner == 1 else "⭕"
                status = f"🎉 **Winner:** <@{win_uid}> ({mark})!"
            content = f"🎮 **Tic-Tac-Toe:** <@{p1}> (❌) vs <@{p2}> (⭕)\n{status}"
            return await i.update(content=content, components=_ttt_components(game["board"], ended=True))
        game["turn"] = 2 if game["turn"] == 1 else 1
        next_uid = p1 if game["turn"] == 1 else p2
        mark = "❌" if game["turn"] == 1 else "⭕"
        content = f"🎮 **Tic-Tac-Toe:** <@{p1}> (❌) vs <@{p2}> (⭕)\n👉 Turn: <@{next_uid}> ({mark})"
        return await i.update(content=content, components=_ttt_components(game["board"]))
