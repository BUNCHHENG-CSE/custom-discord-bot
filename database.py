import time
import logging
import asyncpg
import redis.asyncio as redis
from config import Config

log = logging.getLogger("database")

class DB:
    def __init__(self):
        self.pool: asyncpg.Pool | None = None
        self.redis: redis.Redis | None = None
        self._known_guilds: set[int] = set()
        self._guild_cache: dict[int, tuple[float, asyncpg.Record | None]] = {}

    async def connect(self):
        # Tuned for Wispbyte 512 MB RAM container & Neon serverless PostgreSQL
        self.pool = await asyncpg.create_pool(
            Config.dsn(),
            min_size=1,
            max_size=5,
            command_timeout=60,
            max_inactive_connection_lifetime=60,
        )
        if Config.REDIS_URL:
            self.redis = redis.from_url(Config.REDIS_URL, decode_responses=True)
        elif Config.REDIS_HOST and Config.REDIS_HOST.lower() not in ("none", "disabled", "false"):
            try:
                r = redis.Redis(
                    host=Config.REDIS_HOST,
                    port=Config.REDIS_PORT,
                    decode_responses=True,
                    socket_connect_timeout=2,
                )
                await r.ping()
                self.redis = r
            except Exception:
                log.info("Redis unavailable at %s:%s — using in-memory fallback.", Config.REDIS_HOST, Config.REDIS_PORT)
                self.redis = None
        with open("schema.sql", encoding="utf-8") as f:
            await self.pool.execute(f.read())
        try:
            from helpers.permissions import sync_admin_roles
            await sync_admin_roles(self.pool)
        except Exception:
            pass

    async def close(self):
        if self.pool: await self.pool.close()
        if self.redis: await self.redis.close()

    def invalidate_guild(self, gid: int | str | None) -> None:
        if gid is not None:
            self._guild_cache.pop(int(gid), None)

    async def ensure_guild(self, gid):
        # Discord serializes snowflakes as strings in Gateway/REST payloads,
        # while PostgreSQL BIGINT parameters require Python integers.
        gid = int(gid)
        self._guild_cache.pop(gid, None)
        if gid in self._known_guilds:
            return
        await self.pool.execute("INSERT INTO guilds (id) VALUES ($1) ON CONFLICT DO NOTHING", gid)
        self._known_guilds.add(gid)

    async def get_guild(self, gid):
        gid = int(gid)
        now = time.monotonic()
        cached = self._guild_cache.get(gid)
        if cached and (now - cached[0]) < 30.0:
            return cached[1]
        if gid not in self._known_guilds:
            await self.pool.execute("INSERT INTO guilds (id) VALUES ($1) ON CONFLICT DO NOTHING", gid)
            self._known_guilds.add(gid)
        row = await self.pool.fetchrow("SELECT * FROM guilds WHERE id=$1", gid)
        self._guild_cache[gid] = (now, row)
        return row

    async def add_case(self, gid, uid, mod_id, action, reason=None, duration=None, expires_at=None):
        return await self.pool.fetchval(
            """INSERT INTO mod_cases (guild_id, user_id, moderator_id, action, reason, duration, expires_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id""",
            int(gid), int(uid), int(mod_id), action, reason, duration, expires_at,
        )

    async def get_cases(self, gid, uid):
        return await self.pool.fetch(
            "SELECT * FROM mod_cases WHERE guild_id=$1 AND user_id=$2 ORDER BY id DESC", int(gid), int(uid))

db = DB()
