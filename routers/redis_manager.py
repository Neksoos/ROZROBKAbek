import os
import redis.asyncio as redis

_redis = None

async def get_redis():
    global _redis
    if _redis:
        return _redis

    url = os.getenv("REDIS_URL")
    if not url:
        raise RuntimeError("REDIS_URL is missing")

    _redis = redis.from_url(
        url,
        encoding="utf-8",
        decode_responses=True
    )

    return _redis


async def close_redis():
    global _redis
    if _redis:
        await _redis.close()
        _redis = None