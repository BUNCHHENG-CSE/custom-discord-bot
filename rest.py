import asyncio, time, aiohttp

API = "https://discord.com/api/v10"

class _Bucket:
    __slots__ = ("remaining", "reset_at", "lock")
    def __init__(self):
        self.remaining = 1
        self.reset_at = 0.0
        self.lock = asyncio.Lock()

class REST:
    def __init__(self, token: str):
        self.token = token
        self.session: aiohttp.ClientSession | None = None
        self._buckets: dict[str, _Bucket] = {}
        self._global_reset = 0.0
        self._global_lock = asyncio.Lock()

    async def start(self):
        self.session = aiohttp.ClientSession(headers={
            "Authorization": f"Bot {self.token}",
            "User-Agent": "RawBot/1.0",
        })

    async def close(self):
        if self.session: await self.session.close()

    async def request(self, method: str, path: str, *, json=None, data=None, params=None, reason=None):
        url = path if path.startswith("http") else API + path
        bucket = self._buckets.setdefault(f"{method}:{path.split('/')[3] if len(path.split('/'))>3 else path}",
                                           _Bucket())
        headers = {}
        if reason: headers["X-Audit-Log-Reason"] = reason[:512]

        for attempt in range(5):
            async with self._global_lock:
                now = time.time()
                if now < self._global_reset:
                    await asyncio.sleep(self._global_reset - now)
            async with bucket.lock:
                now = time.time()
                if bucket.remaining <= 0 and now < bucket.reset_at:
                    await asyncio.sleep(bucket.reset_at - now)

            async with self.session.request(method, url, json=json, data=data, params=params, headers=headers) as resp:
                rem = resp.headers.get("X-RateLimit-Remaining")
                ra = resp.headers.get("X-RateLimit-Reset-After")
                if rem is not None: bucket.remaining = int(rem)
                if ra is not None:  bucket.reset_at = time.time() + float(ra)

                if resp.status == 429:
                    data = await resp.json()
                    wait = float(data.get("retry_after", 1.0))
                    if data.get("global"): self._global_reset = time.time() + wait
                    await asyncio.sleep(wait)
                    continue
                if resp.status >= 500:
                    await asyncio.sleep(1 + attempt)
                    continue
                if resp.status == 204:
                    return None
                if resp.status >= 400:
                    body = await resp.text()
                    raise RuntimeError(f"{resp.status} {method} {path}: {body[:300]}")
                return await resp.json()
        raise RuntimeError(f"REST retries exhausted: {method} {path}")

    def get(self, p, **k):    return self.request("GET", p, **k)
    def post(self, p, **k):   return self.request("POST", p, **k)
    def patch(self, p, **k):  return self.request("PATCH", p, **k)
    def put(self, p, **k):    return self.request("PUT", p, **k)
    def delete(self, p, **k): return self.request("DELETE", p, **k)