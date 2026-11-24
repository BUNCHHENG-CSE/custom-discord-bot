import os
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
from dotenv import load_dotenv

load_dotenv()


class Config:
    TOKEN = os.environ["DISCORD_TOKEN"].strip()

    _guild_raw = (os.getenv("GUILD_ID") or "").split("#")[0].strip()
    GUILD_ID = int(_guild_raw) if _guild_raw else 0

    # External PostgreSQL connection URI (Neon / Supabase)
    DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()

    # Fallback individual DB vars (local dev)
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = int(os.getenv("DB_PORT", 5432))
    DB_NAME = os.getenv("DB_NAME", "discordbot")
    DB_USER = os.getenv("DB_USER", "postgres")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "")

    # Optional Redis (leave REDIS_HOST empty on Wispbyte to use in-memory fallback)
    REDIS_URL = (os.getenv("REDIS_URL") or "").strip() or None
    REDIS_HOST = (os.getenv("REDIS_HOST") or "").strip()
    REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

    # Lavalink v4 configuration (supports host/port/ssl or full LAVALINK_URI)
    LAVALINK_HOST = (os.getenv("LAVALINK_HOST") or "localhost").strip()
    LAVALINK_PORT = int(os.getenv("LAVALINK_PORT") or 2333)
    LAVALINK_PASSWORD = (os.getenv("LAVALINK_PASSWORD") or "youshallnotpass").strip()
    LAVALINK_SSL = (os.getenv("LAVALINK_SSL") or "false").strip().lower() in (
        "true",
        "1",
        "yes",
    )

    _default_ws_scheme = "wss" if LAVALINK_SSL else "ws"
    LAVALINK_URI = (
        os.getenv("LAVALINK_URI")
        or f"{_default_ws_scheme}://{LAVALINK_HOST}:{LAVALINK_PORT}"
    ).strip()

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or None

    TCT_CONTROL_PLANE_API_URL = os.getenv(
        "TCT_CONTROL_PLANE_API_URL", "http://localhost:3000/api/discord"
    ).rstrip("/")
    TCT_DISCORD_API_SECRET = (os.getenv("TCT_DISCORD_API_SECRET") or "").strip()

    OWNER_IDS = {
        int(x.strip())
        for x in (os.getenv("OWNER_IDS") or "").split("#")[0].split(",")
        if x.strip()
    }

    @classmethod
    def dsn(cls) -> str:
        if cls.DATABASE_URL:
            parsed = urlparse(cls.DATABASE_URL)
            query_params = dict(parse_qsl(parsed.query, keep_blank_values=True))
            # asyncpg looks for ~/.postgresql/root.crt when sslmode=verify-full/verify-ca
            # without an explicit sslrootcert, which fails on Windows & Wispbyte containers.
            if query_params.get("sslmode") in ("verify-full", "verify-ca") and "sslrootcert" not in query_params:
                query_params["sslmode"] = "require"
            # Strip libpq-only channel_binding param so asyncpg doesn't send it as a server GUC
            query_params.pop("channel_binding", None)
            return urlunparse(parsed._replace(query=urlencode(query_params)))
        return f"postgresql://{cls.DB_USER}:{cls.DB_PASSWORD}@{cls.DB_HOST}:{cls.DB_PORT}/{cls.DB_NAME}"
