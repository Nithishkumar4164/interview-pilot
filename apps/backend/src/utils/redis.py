"""Shared async Redis connection.

An asyncio Redis client is bound to the event loop that created it, so one
client is kept per running loop (production runs a single loop; test
clients and worker threads may run others).
"""

import asyncio
import weakref

import redis.asyncio as aioredis

from src.config import settings

_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, aioredis.Redis]" = weakref.WeakKeyDictionary()


def get_redis() -> aioredis.Redis:
    loop = asyncio.get_running_loop()
    client = _clients.get(loop)
    if client is None:
        client = aioredis.from_url(settings.redis_url, decode_responses=True)
        _clients[loop] = client
    return client
