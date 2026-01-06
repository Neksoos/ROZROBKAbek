# services/area_presence.py
from __future__ import annotations

from typing import List, Tuple, Dict
from loguru import logger

try:
    from ..database import get_pool  # type: ignore
except Exception:
    get_pool = None  # type: ignore

# Скільки секунд вважаємо гравця «присутнім» у локації
TTL_SECONDS = 120

# ────────────────────────────────────────────────────────────────────
# СХЕМА
# ────────────────────────────────────────────────────────────────────

_SCHEMA_OK = False

async def ensure_schema() -> bool:
    """
    Таблиця area_presence:
      - tg_id BIGINT PRIMARY KEY
      - area_key TEXT NOT NULL
      - updated_at TIMESTAMP NOT NULL DEFAULT now()
    Індекс для швидкого пошуку по (area_key, updated_at).
    """
    global _SCHEMA_OK
    if _SCHEMA_OK:
        return True
    if not get_pool:
        logger.warning("area_presence: no DB pool")
        return False
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS area_presence(
                    tg_id BIGINT PRIMARY KEY,
                    area_key TEXT NOT NULL,
                    updated_at TIMESTAMP NOT NULL DEFAULT now()
                );
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS area_presence_area_updated_idx
                ON area_presence(area_key, updated_at DESC);
            """)
        _SCHEMA_OK = True
        return True
    except Exception as e:
        logger.warning(f"area_presence.ensure_schema failed: {e}")
        return False

# ────────────────────────────────────────────────────────────────────
# API
# ────────────────────────────────────────────────────────────────────

async def touch(area_key: str, tg_id: int) -> None:
    """
    Позначаємо гравця присутнім у локації area_key.
    Оновлює або створює запис (PRIMARY KEY по tg_id).
    """
    if not get_pool or not await ensure_schema():
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO area_presence(tg_id, area_key, updated_at)
            VALUES ($1, $2, now())
            ON CONFLICT (tg_id) DO UPDATE
              SET area_key = EXCLUDED.area_key,
                  updated_at = now()
            """,
            tg_id, area_key,
        )

async def leave(tg_id: int) -> None:
    """
    Приховуємо гравця (вихід з локації/меню).
    """
    if not get_pool or not await ensure_schema():
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM area_presence WHERE tg_id=$1", tg_id)

async def list_present(area_key: str, limit: int = 10) -> List[int]:
    """
    TG IDs присутніх у локації за останні TTL_SECONDS, новіші вище.
    """
    if not get_pool or not await ensure_schema():
        return []
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT tg_id
            FROM area_presence
            WHERE area_key=$1
              AND updated_at >= now() - make_interval(secs => $2::int)
            ORDER BY updated_at DESC
            LIMIT $3
            """,
            area_key, TTL_SECONDS, limit,
        )
    return [int(r["tg_id"]) for r in rows]

async def names_for_ids(tg_ids: List[int]) -> Dict[int, Tuple[str, int, str, str]]:
    """
    {tg_id: (name, level, race_key, class_key)}. Відсутніх у players ігноруємо.
    """
    out: Dict[int, Tuple[str, int, str, str]] = {}
    if not tg_ids or not get_pool or not await ensure_schema():
        return out
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT tg_id,
                   COALESCE(name,'Герой')  AS name,
                   COALESCE(level,1)        AS level,
                   COALESCE(race_key,'-')   AS race_key,
                   COALESCE(class_key,'-')  AS class_key
            FROM players
            WHERE tg_id = ANY($1::bigint[])
            """,
            tg_ids,
        )
    for r in rows:
        out[int(r["tg_id"])] = (
            r["name"], int(r["level"]), r["race_key"], r["class_key"]
        )
    return out

async def top_present_named(area_key: str, exclude: int, limit: int = 5) -> List[Tuple[int, str, int]]:
    """
    Список (tg_id, name, level) присутніх у локації, без поточного користувача.
    """
    ids = await list_present(area_key, limit=limit + 2)
    ids = [i for i in ids if i != exclude][:limit]
    info = await names_for_ids(ids)
    out: List[Tuple[int, str, int]] = []
    for uid in ids:
        nm, lvl, _, _ = info.get(uid, (f"Гравець {uid}", 1, "-", "-"))
        out.append((uid, nm, lvl))
    return out

__all__ = [
    "TTL_SECONDS",
    "ensure_schema",
    "touch",
    "leave",
    "list_present",
    "names_for_ids",
    "top_present_named",
]