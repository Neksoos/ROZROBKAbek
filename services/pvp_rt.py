# services/pvp_rt.py
from __future__ import annotations

import json
from typing import Optional, Tuple, Dict, Any, Union

# ✅ FIX: у тебе redis_manager лежить в routers/redis_manager.py
try:
    from routers.redis_manager import get_redis  # type: ignore
except Exception:
    # fallback (якщо раптом у тебе колись було в корені)
    from redis_manager import get_redis  # type: ignore


# --------------------------------------------------
# Redis keys
# state: perun:rt:{duel_id} -> JSON {p1,p2,hp1,hp2,turn,max_hp,max_hp1,max_hp2,state,round,last,winner,loser}
# lock:  perun:rt:lock:{duel_id} -> NX-lock для ходу
# --------------------------------------------------

STATE_TTL = 60 * 60          # 1 година
LOCK_TTL = 12                # 12 сек

JsonLike = Dict[str, Any]
RawRedis = Union[bytes, str, None]


def _key_state(duel_id: int) -> str:
    return f"perun:rt:{int(duel_id)}"


def _key_lock(duel_id: int) -> str:
    return f"perun:rt:lock:{int(duel_id)}"


def _json_load(raw: RawRedis) -> Optional[JsonLike]:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


async def init_state(
    duel_id: int,
    p1: int,
    p2: int,
    hp1: int = 30,
    hp2: int = 30
) -> None:
    """
    Ініціалізує стейт бою в Redis.
    """
    r = await get_redis()

    hp1 = int(hp1)
    hp2 = int(hp2)
    max_common = max(hp1, hp2)

    data: JsonLike = {
        "p1": int(p1),
        "p2": int(p2),
        "hp1": hp1,
        "hp2": hp2,
        "max_hp": max_common,
        "max_hp1": hp1,
        "max_hp2": hp2,
        "turn": int(p1),
        "round": 1,
        "state": "active",
        "last": None,
        "winner": None,
        "loser": None,
    }

    await r.set(_key_state(duel_id), json.dumps(data), ex=STATE_TTL)


async def load_state(duel_id: int) -> Optional[JsonLike]:
    r = await get_redis()
    raw = await r.get(_key_state(duel_id))
    return _json_load(raw)


async def save_state(duel_id: int, data: JsonLike) -> None:
    r = await get_redis()
    await r.set(_key_state(duel_id), json.dumps(data), ex=STATE_TTL)


async def touch_state(duel_id: int) -> None:
    r = await get_redis()
    await r.expire(_key_state(duel_id), STATE_TTL)


async def acquire_turn_lock(duel_id: int) -> bool:
    r = await get_redis()
    res = await r.set(_key_lock(duel_id), "1", ex=LOCK_TTL, nx=True)
    return bool(res)


async def release_turn_lock(duel_id: int) -> None:
    r = await get_redis()
    await r.delete(_key_lock(duel_id))


async def clear_duel(duel_id: int) -> None:
    r = await get_redis()
    await r.delete(_key_state(duel_id))
    await r.delete(_key_lock(duel_id))