from interactions import subcommand, Interaction, STR
from config import Config

try:
    from openai import AsyncOpenAI
    _client = AsyncOpenAI(api_key=Config.OPENAI_API_KEY) if Config.OPENAI_API_KEY else None
except Exception:
    _client = None

SYS = "You are a helpful, concise Discord assistant. Answers under 1500 characters."

@subcommand("ai", "AI commands", "ask", "Ask the AI", [
    {"name": "prompt", "description": "Question", "type": STR, "required": True},
])
async def ask(i: Interaction, prompt):
    if not _client:
        return await i.respond("❌ OpenAI not configured.", ephemeral=True)
    await i.defer()
    try:
        r = await _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": SYS}, {"role": "user", "content": prompt}],
            max_tokens=600)
        await i.followup(r.choices[0].message.content[:1900])
    except Exception as e:
        await i.followup(f"❌ AI error: `{e}`")

@subcommand("ai", "AI commands", "imagine", "Generate an image", [
    {"name": "prompt", "description": "Prompt", "type": STR, "required": True},
])
async def imagine(i: Interaction, prompt):
    if not _client:
        return await i.respond("❌ OpenAI not configured.", ephemeral=True)
    await i.defer()
    try:
        r = await _client.images.generate(model="dall-e-3", prompt=prompt, size="1024x1024", n=1)
        await i.followup(embeds=[{"title": f"🎨 {prompt[:240]}",
                                   "image": {"url": r.data[0].url}, "color": 0x9B59B6}])
    except Exception as e:
        await i.followup(f"❌ Image error: `{e}`")