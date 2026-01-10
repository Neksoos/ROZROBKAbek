"""
Generic chat service for Kyrhanu.

Redis layout:
- Messages:  ZSET  f"{room}:messages"  score=id, member=json payload
- Next id:   STR   f"{room}:next_msg_id"
- Online:    ZSET  f"{room}:online"    score=last_seen_ts, member=tg_id
- Rate:      STR   f"{room}:last_msg_at:{tg_id}"
- Mute:      STR   f"{room}:mute:{tg_id}" -> "1" with TTL
- Join spam guard: STR f"{room}:join_announce:{tg_id}" -> "1" with TTL

This module is decoupled from FastAPI routers; routers adapt outputs.
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
    id: int
    tg_id: int
    name: str
    text: str
    timestamp: float = Field(..., description="Unix time (seconds)")
    system: bool = False
    extra: Optional[Dict[str, Any]] = None


async def _get_player_name(tg_id: int) -> str:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT name FROM players WHERE tg_id = $1", tg_id)
        if row and row["name"]:
            return str(row["name"])
    except Exception as e:
        logger.warning(f"chat: _get_player_name failed tg_id={tg_id}: {e}")
    return f"Гравець {tg_id}"


def _safe_float_ts(payload: Dict[str, Any]) -> float:
    ts_val = payload.get("timestamp", payload.get("ts", None))
    if ts_val is None:
        return time.time()
    try:
        return float(ts_val)
    except Exception:
        return time.time()


async def _is_muted(room: str, tg_id: int) -> bool:
    r = await get_redis()
    key = f"{room}:mute:{tg_id}"
    try:
        return (await r.get(key)) is not None
    except Exception as e:
        logger.warning(f"chat: mute check fail {room} tg_id={tg_id}: {e}")
        return False


async def mute_user(room: str, tg_id: int, seconds: int) -> None:
    """
    Mute user in room for N seconds. (Simple moderation primitive)
    """
    r = await get_redis()
    seconds = max(1, int(seconds))
    await r.set(f"{room}:mute:{tg_id}", "1", ex=seconds)


async def _touch_online(
    room: str,
    tg_id: int,
    online_ttl: int = 60,
    announce_join: bool = True,
    join_announce_cooldown: int = 90,
) -> int:
    """
    Update presence and return online count.
    Optionally announces join as a system message (rate-limited per user).
    """
    r = await get_redis()
    now = int(time.time())
    key_online = f"{room}:online"

    await r.zadd(key_online, {str(tg_id): now})
    await r.zremrangebyscore(key_online, 0, now - online_ttl)

    if announce_join:
        try:
            guard_key = f"{room}:join_announce:{tg_id}"
            # setnx-like via SET with NX
            ok = await r.set(guard_key, "1", ex=join_announce_cooldown, nx=True)
            if ok:
                name = await _get_player_name(tg_id)
                await send_system(room, text=f"👋 {name} у чаті.", max_messages=2000)
        except Exception as e:
            logger.warning(f"chat: join announce fail {room} tg_id={tg_id}: {e}")

    return int(await r.zcard(key_online))


async def get_online(
    room: str,
    *,
    tg_id: int,
    online_ttl: int = 60,
    limit: int = 50,
) -> Tuple[int, List[Dict[str, Any]]]:
    """
    Returns (online_count, list[{tg_id,last_seen}]) sorted by last_seen desc.
    Touches presence for requester.
    """
    r = await get_redis()
    online_count = await _touch_online(room, tg_id, online_ttl, announce_join=False)

    now = int(time.time())
    key_online = f"{room}:online"
    await r.zremrangebyscore(key_online, 0, now - online_ttl)

    raw = await r.zrevrange(key_online, 0, max(0, int(limit) - 1), withscores=True)
    out: List[Dict[str, Any]] = []
    for member, score in raw:
        try:
            if isinstance(member, bytes):
                member = member.decode("utf-8")
            out.append({"tg_id": int(member), "last_seen": int(score)})
        except Exception:
            continue
    return online_count, out


async def get_history(
    room: str,
    *,
    tg_id: int,
    since_id: int = 0,
    limit: int = 50,
    max_messages: int = 2000,
    online_ttl: int = 60,
) -> Tuple[List[ChatMessage], int, int]:
    r = await get_redis()
    online = await _touch_online(room, tg_id, online_ttl)

    key_messages = f"{room}:messages"
    messages: List[ChatMessage] = []
    limit = max(1, min(int(limit), 200))

    if since_id <= 0:
        raw = await r.zrevrange(key_messages, 0, limit - 1)
        raw = list(reversed(raw))
    else:
        # ✅ score is int id -> since_id+1 is correct
        raw = await r.zrangebyscore(key_messages, since_id + 1, "+inf", start=0, num=limit)

    for raw_entry in raw:
        try:
            if isinstance(raw_entry, bytes):
                raw_entry = raw_entry.decode("utf-8")
            payload = json.loads(raw_entry)

            msg_id = int(payload.get("id") or 0)
            sender_id = int(payload.get("tg_id") or 0)
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
            logger.warning(f"chat: bad payload in {room}: {e}")
            continue

    last_id = messages[-1].id if messages else since_id

    # trim
    try:
        total = int(await r.zcard(key_messages))
        if total > max_messages:
            await r.zremrangebyrank(key_messages, 0, total - max_messages - 1)
    except Exception as e:
        logger.warning(f"chat: trim fail {room}: {e}")

    return messages, last_id, online


async def send_system(
    room: str,
    *,
    text: str,
    max_length: int = 220,
    max_messages: int = 2000,
) -> ChatMessage:
    """
    System messages are stored like normal messages but system=True, tg_id=0.
    """
    txt = (text or "").strip()
    if not txt:
        raise HTTPException(status_code=400, detail="EMPTY_TEXT")
    if len(txt) > max_length:
        txt = txt[:max_length].rstrip() + "…"

    r = await get_redis()
    now = time.time()
    seq_key = f"{room}:next_msg_id"
    msg_id = int(await r.incr(seq_key))

    payload: Dict[str, Any] = {
        "id": msg_id,
        "tg_id": 0,
        "name": "Система",
        "text": txt,
        "timestamp": now,
        "system": True,
    }

    key_messages = f"{room}:messages"
    await r.zadd(key_messages, {json.dumps(payload, ensure_ascii=False): msg_id})

    try:
        total = int(await r.zcard(key_messages))
        if total > max_messages:
            await r.zremrangebyrank(key_messages, 0, total - max_messages - 1)
    except Exception as e:
        logger.warning(f"chat: trim fail {room}: {e}")

    return ChatMessage(
        id=msg_id,
        tg_id=0,
        name="Система",
        text=txt,
        timestamp=now,
        system=True,
        extra=None,
    )


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
    txt = (text or "").strip()
    if not txt:
        raise HTTPException(status_code=400, detail="EMPTY_TEXT")

    # ✅ mute check
    if await _is_muted(room, tg_id):
        raise HTTPException(status_code=403, detail="MUTED")

    if len(txt) > max_length:
        txt = txt[:max_length].rstrip() + "…"

    r = await get_redis()
    now = time.time()

    # anti-flood
    rl_key = f"{room}:last_msg_at:{tg_id}"
    try:
        raw_last = await r.get(rl_key)
        if raw_last is not None:
            last = float(raw_last)
            if now - last < rate_limit:
                raise HTTPException(status_code=429, detail="TOO_FAST")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"chat: anti-flood read fail {room} tg_id={tg_id}: {e}")

    name = await _get_player_name(tg_id)

    seq_key = f"{room}:next_msg_id"
    msg_id = int(await r.incr(seq_key))

    payload: Dict[str, Any] = {
        "id": msg_id,
        "tg_id": tg_id,
        "name": name,
        "text": txt,
        "timestamp": now,
        "system": False,
    }
    if extra:
        payload["extra"] = extra

    key_messages = f"{room}:messages"
    await r.zadd(key_messages, {json.dumps(payload, ensure_ascii=False): msg_id})

    # trim
    try:
        total = int(await r.zcard(key_messages))
        if total > max_messages:
            await r.zremrangebyrank(key_messages, 0, total - max_messages - 1)
    except Exception as e:
        logger.warning(f"chat: trim fail {room}: {e}")

    # write last time
    try:
        await r.set(rl_key, str(now), ex=max(int(rate_limit * 10), 60))
    except Exception as e:
        logger.warning(f"chat: set last_msg_at fail {room} tg_id={tg_id}: {e}")

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