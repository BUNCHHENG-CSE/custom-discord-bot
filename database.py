import asyncpg, redis.asyncio as redis, json
from config import Config

class DB:
    def __init__(self):
        self.pool: asyncpg.Pool | None = None
        self.redis: redis.Redis | None = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(Config.dsn(), min_size=2, max_size=10)
        self.redis = redis.Redis(host=Config.REDIS_HOST, port=Config.REDIS_PORT, decode_responses=True)
        with open("schema.sql") as f:
            await self.pool.execute(f.read())
        try:
            from helpers.permissions import sync_admin_roles
            await sync_admin_roles(self.pool)
        except Exception:
            pass

    async def close(self):
        if self.pool: await self.pool.close()
        if self.redis: await self.redis.close()

    async def ensure_guild(self, gid):
        # Discord serializes snowflakes as strings in Gateway/REST payloads,
        # while PostgreSQL BIGINT parameters require Python integers.
        gid = int(gid)
        await self.pool.execute("INSERT INTO guilds (id) VALUES ($1) ON CONFLICT DO NOTHING", gid)

    async def get_guild(self, gid):
        gid = int(gid)
        await self.ensure_guild(gid)
        return await self.pool.fetchrow("SELECT * FROM guilds WHERE id=$1", gid)

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
