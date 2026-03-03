import aiofiles


class FileCache:
    # cache miss を表すセンチネル
    MISS = object()

    def __init__(self):
        self.cache = {}
        self.hits = 0
        self.misses = 0

    def get_cached(self, filepath, mode="r"):
        cache_key = (filepath, mode)

        if cache_key in self.cache:
            self.hits += 1
            return self.cache[cache_key]

        return self.MISS

    async def read_from_disk(self, filepath, mode="r"):
        cache_key = (filepath, mode)
        self.misses += 1

        if mode == "r":
            async with aiofiles.open(filepath, "r", encoding="utf-8") as f:
                content = await f.read()
        else:
            async with aiofiles.open(filepath, "rb") as f:
                content = await f.read()

        self.cache[cache_key] = content
        return content

    async def read(self, filepath, mode="r"):
        content = self.get_cached(filepath, mode=mode)
        if content is not self.MISS:
            return content
        return await self.read_from_disk(filepath, mode=mode)

    def stats(self):
        return {"hits": self.hits, "misses": self.misses}

    def clear(self):
        self.cache.clear()
