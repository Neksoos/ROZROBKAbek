"""
Generic chat service for Kyrhanu.

This module encapsulates the common logic used by different chat rooms in
the game (e.g., tavern chat, fort chats). It relies on Redis to
store messages, track online users and enforce anti-flood limits.

Messages are stored in a sorted set per room under the key
``f"{room}:messages"`` with the message id as both the score and a
unique identifier. A separate key ``f"{room}:next_msg_id"`` stores the
incrementing id for each new message. Online status is tracked in a
sorted set ``f"{room}:online"`` with the last seen timestamp as the score.

Functions in this module are decoupled from FastAPI and can be used
from any router. Callers should convert the returned `ChatMessage`
objects into their own response models where necessary.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from db import get_pool
from routers.redis_manager import get_redis


class ChatMessage(BaseModel):
    """Internal representation of a chat message."""

    id: int
    tg_id: int
    name: str
    text: str
    timestamp: float = Field(..., description="Unix time (seconds)")
    system: bool = False
    extra: Optional[Dict[str, Any]] = None


async def _get_player_name(tg_id: int) -> str:
    """Fetch a player's name from the database; fall back to a generic placeholder."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT name FROM players WHERE tg_id = $1",
                tg_id,
            )
        if row and row["name"]:
            return str(row["name"])
    except Exception as e:
        logger.warning(f"chat: _get_player_name failed for tg_id={tg_id}: {e}")
    return f"Гравець {tg_id}"


async def _touch_online(room: str, tg_id: int, online_ttl: int = 60) -> int:
    """Update the online presence for a user in a given room and return current online count."""
    r = await get_redis()
    now = int(time.time())
    key_online = f"{room}:online"

    await r.zadd(key_online, {str(tg_id): now})
    await r.zremrangebyscore(key_online, 0, now - online_ttl)
    return int(await r.zcard(key_online))


def _safe_float_ts(payload: Dict[str, Any]) -> float:
    """
    Robust timestamp parsing:
    - supports 'timestamp' or legacy 'ts'
    - falls back to now if missing/invalid
    """
    ts_val = payload.get("timestamp", payload.get("ts", None))
    if ts_val is None:
        return time.time()
    try:
        return float(ts_val)
    except Exception:
        return time.time()


async def get_history(
    room: str,
    *,
    tg_id: int,
    since_id: int = 0,
    limit: int = 50,
    max_messages: int = 2000,
) -> Tuple[List[ChatMessage], int, int]:
    """
    Retrieve chat history for a room.

    :param room: Logical room identifier (e.g. "tavern" or "fort:123").
    :param tg_id: Requesting player's Telegram id.
    :param since_id: If > 0, return messages with id > since_id; else return the last `limit` messages.
    :param limit: Maximum number of messages to return.
    :param max_messages: Maximum messages to retain in history; older messages are trimmed automatically.
    :returns: (messages, last_id, online_count)
    """
    r = await get_redis()
    online = await _touch_online(room, tg_id)

    key_messages = f"{room}:messages"
    messages: List[ChatMessage] = []

    if since_id <= 0:
        raw = await r.zrevrange(key_messages, 0, limit - 1)
        raw = list(reversed(raw))
    else:
        raw = await r.zrangebyscore(
            key_messages, since_id + 0.0001, "+inf", start=0, num=limit
        )

    for raw_entry in raw:
        try:
            if isinstance(raw_entry, bytes):
                raw_entry = raw_entry.decode("utf-8")

            payload = json.loads(raw_entry)
            msg_id = int(payload.get("id") or 0)
            sender_id = int(payload.get("tg_id") or 0)

            # пропускаємо биті записи
            if msg_id <= 0 or sender_id <= 0:
                continue

            msg = ChatMessage(
                id=msg_id,
                tg_id=sender_id,
                name=str(payload.get("name") or ""),
                text=str(payload.get("text") or ""),
                timestamp=_safe_float_ts(payload),
                system=bool(payload.get("system", False)),
                extra=payload.get("extra"),
            )
            messages.append(msg)
        except Exception as e:
            logger.warning(f"chat: bad message payload in {room}: {e}")
            continue

    last_id = messages[-1].id if messages else since_id

    # trim з урахуванням max_messages (правильно)
    try:
        total = int(await r.zcard(key_messages))
        if total > max_messages:
            # лишаємо останні max_messages
            await r.zremrangebyrank(key_messages, 0, total - max_messages - 1)
    except Exception as e:
        logger.warning(f"chat: trim history fail for {room}: {e}")

    return messages, last_id, online


async def send_message(
    room: str,
    *,
    tg_id: int,
    text: str,
    max_length: int = 300,
    rate_limit: float = 1.5,
    extra: Optional[Dict[str, Any]] = None,
    online_ttl: int = 60,
    max_messages: int = 2000,
) -> Tuple[ChatMessage, int]:
    """
    Send a chat message in the specified room.
    """
    txt = (text or "").strip()
    if not txt:
        raise HTTPException(status_code=400, detail="EMPTY_TEXT")

    if len(txt) > max_length:
        txt = txt[:max_length].rstrip() + "…"

    r = await get_redis()
    now = time.time()

    rl_key = f"{room}:last_msg_at:{tg_id}"

    # anti-flood
    try:
        raw_last = await r.get(rl_key)
        if raw_last is not None:
            last = float(raw_last)
            if now - last < rate_limit:
                raise HTTPException(status_code=429, detail="TOO_FAST")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"chat: anti-flood read fail tg_id={tg_id}: {e}")

    name = await _get_player_name(tg_id)

    seq_key = f"{room}:next_msg_id"
    msg_id = int(await r.incr(seq_key))

    msg_payload: Dict[str, Any] = {
        "id": msg_id,
        "tg_id": tg_id,
        "name": name,
        "text": txt,
        "timestamp": now,
        "system": False,
    }
    if extra:
        msg_payload["extra"] = extra

    key_messages = f"{room}:messages"
    await r.zadd(key_messages, {json.dumps(msg_payload, ensure_ascii=False): msg_id})

    # trim з урахуванням max_messages (правильно)
    try:
        total = int(await r.zcard(key_messages))
        if total > max_messages:
            await r.zremrangebyrank(key_messages, 0, total - max_messages - 1)
    except Exception as e:
        logger.warning(f"chat: trim history fail for {room}: {e}")

    # записуємо час останнього повідомлення
    try:
        await r.set(rl_key, str(now), ex=max(int(rate_limit * 10), 60))
    except Exception as e:
        logger.warning(f"chat: set last_msg_at fail: {e}")

    online = await _touch_online(room, tg_id, online_ttl)

    msg = ChatMessage(
        id=msg_id,
        tg_id=tg_id,
        name=name,
        text=txt,
        timestamp=now,
        system=False,
        extra=extra,
    )
    return msg, online