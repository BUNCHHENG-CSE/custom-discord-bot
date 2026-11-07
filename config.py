import os
from dotenv import load_dotenv
load_dotenv()

class Config:
    TOKEN = os.environ["DISCORD_TOKEN"]

    _guild_raw = (os.getenv("GUILD_ID") or "").split("#")[0].strip()
    GUILD_ID = int(_guild_raw) if _guild_raw else 0

    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = int(os.getenv("DB_PORT", 5432))
    DB_NAME = os.getenv("DB_NAME", "discordbot")
    DB_USER = os.getenv("DB_USER", "postgres")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "")

    REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

    LAVALINK_URI = os.getenv("LAVALINK_URI", "ws://localhost:2333")
    LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD", "youshallnotpass")

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or None

    TCT_CONTROL_PLANE_API_URL = os.getenv(
        "TCT_CONTROL_PLANE_API_URL", "http://localhost:3000/api/discord"
    ).rstrip("/")
    TCT_DISCORD_API_SECRET = os.getenv(
        "TCT_DISCORD_API_SECRET", "tct_discord_sec_4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d"
    ).strip()

    OWNER_IDS = {
        int(x.strip())
        for x in (os.getenv("OWNER_IDS") or "").split("#")[0].split(",")
        if x.strip()
    }

    @classmethod
    def dsn(cls):
        return f"postgresql://{cls.DB_USER}:{cls.DB_PASSWORD}@{cls.DB_HOST}:{cls.DB_PORT}/{cls.DB_NAME}"