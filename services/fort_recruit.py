# services/fort_recruit.py
from __future__ import annotations

from typing import List, Tuple, Optional
from loguru import logger

# ───────────────────────── DB (мініап) ─────────────────────────
try:
    from db import get_pool  # type: ignore
except Exception:
    get_pool = None  # type: ignore


# ────────── СХЕМА ДЛЯ РЕКРУТИНГУ (форти / учасники / заявки) ──────────
SCHEMA_SQL_RECRUIT = [
    # таблиця застав
    """
    CREATE TABLE IF NOT EXISTS forts (
        id BIGSERIAL PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        created_by BIGINT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT now()
    );
    """,
    # учасники застав
    """
    CREATE TABLE IF NOT EXISTS fort_members (
        fort_id BIGINT NOT NULL REFERENCES forts(id) ON DELETE CASCADE,
        tg_id BIGINT NOT NULL,
        role TEXT NOT NULL DEFAULT 'novachok',
        joined_at TIMESTAMP NOT NULL DEFAULT now(),
        PRIMARY KEY (fort_id, tg_id)
    );
    """,
    # заявки на вступ
    """
    CREATE TABLE IF NOT EXISTS fort_join_requests (
        id BIGSERIAL PRIMARY KEY,
        fort_id BIGINT NOT NULL REFERENCES forts(id) ON DELETE CASCADE,
        tg_id BIGINT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT now(),
        UNIQUE (fort_id, tg_id)
    );
    """,
]


async def ensure_recruit_schema() -> bool:
    """
    Гарантуємо наявність таблиць:
      - forts
      - fort_members
      - fort_join_requests

    Якщо БД недоступна — False.
    """
    if not get_pool:
        logger.warning("fort_recruit: no DB pool")
        return False

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            for sql in SCHEMA_SQL_RECRUIT:
                await conn.execute(sql)
        return True
    except Exception as e:
        logger.warning(f"ensure_recruit_schema failed: {e}")
        return False


# ========== базові утиліти: членство/роль/назви ==========

async def get_member_fort(tg_id: int) -> Optional[int]:
    """В якій заставі зараз гравець, або None."""
    if not await ensure_recruit_schema():
        return None

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT fort_id FROM fort_members WHERE tg_id=$1",
                tg_id,
            )
            return int(row["fort_id"]) if row and row["fort_id"] is not None else None
    except Exception as e:
        logger.warning(f"get_member_fort failed: {e}")
        return None


async def get_fort_name(fort_id: int) -> str:
    """Назва застави або fallback 'Застава #id'."""
    if not await ensure_recruit_schema():
        return f"Застава #{fort_id}"

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT name FROM forts WHERE id=$1",
                fort_id,
            )
            if row and row["name"]:
                return str(row["name"])
            return f"Застава #{fort_id}"
    except Exception:
        return f"Застава #{fort_id}"


async def is_leader(tg_id: int, fort_id: int) -> bool:
    """Чи гравець має керівну роль у заставі (hetman/head)."""
    if not await ensure_recruit_schema():
        return False

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT role FROM fort_members WHERE fort_id=$1 AND tg_id=$2",
                fort_id,
                tg_id,
            )
            if not row:
                return False
            return str(row["role"]) in ("hetman", "head")
    except Exception:
        return False


# ========== публічний список застав (для тих, хто без застави) ==========

async def list_forts_public(limit: int = 30) -> List[Tuple[int, str, int]]:
    """
    Список застав із кількістю учасників.
    Повертає [(fort_id, name, members_count)].
    """
    if not await ensure_recruit_schema():
        return []

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT f.id,
                       f.name,
                       COUNT(m.tg_id) AS members_count
                FROM forts f
                LEFT JOIN fort_members m ON m.fort_id = f.id
                GROUP BY f.id, f.name
                ORDER BY members_count DESC, f.id ASC
                LIMIT $1
                """,
                limit,
            )

        out: List[Tuple[int, str, int]] = []
        for r in rows:
            out.append((int(r["id"]), str(r["name"]), int(r["members_count"])))
        return out

    except Exception as e:
        logger.warning(f"list_forts_public failed: {e}")
        return []


# ========== заявки на вступ ==========

async def has_active_request(tg_id: int) -> Optional[int]:
    """
    Чи юзер вже подав заявку.
    Якщо так — повертає fort_id, інакше None.
    """
    if not await ensure_recruit_schema():
        return None

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT fort_id FROM fort_join_requests WHERE tg_id=$1",
                tg_id,
            )
            return int(row["fort_id"]) if row and row["fort_id"] is not None else None
    except Exception as e:
        logger.warning(f"has_active_request failed: {e}")
        return None


async def create_join_request(tg_id: int, fort_id: int) -> bool:
    """
    Створити заявку на вступ у fort_id.
    Відмовляємо якщо:
      - юзер уже у якійсь заставі,
      - або в нього вже є активна заявка.
    """
    if not await ensure_recruit_schema():
        return False

    # 1) вже у заставі?
    fid_now = await get_member_fort(tg_id)
    if fid_now:
        return False

    # 2) вже є заявка?
    active = await has_active_request(tg_id)
    if active is not None:
        return False

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO fort_join_requests(fort_id, tg_id)
                VALUES ($1,$2)
                ON CONFLICT DO NOTHING
                """,
                fort_id,
                tg_id,
            )
        return True
    except Exception as e:
        logger.warning(f"create_join_request failed: {e}")
        return False


async def list_join_requests_for_fort(fort_id: int) -> List[int]:
    """
    Кандидати у конкретну заставу.
    Повертає список tg_id, які подали заявку.
    """
    if not await ensure_recruit_schema():
        return []

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT tg_id
                FROM fort_join_requests
                WHERE fort_id=$1
                ORDER BY created_at ASC
                LIMIT 50
                """,
                fort_id,
            )
            return [int(r["tg_id"]) for r in rows]
    except Exception as e:
        logger.warning(f"list_join_requests_for_fort failed: {e}")
        return []


async def approve_request(fort_id: int, target_tg: int, approver_tg: int) -> str:
    """
    Лідер (hetman/head) приймає кандидата.
    Кроки:
      - перевірити права approver_tg
      - перевірити, що заявка існує
      - додати target_tg у fort_members з роллю 'novachok'
      - видалити заявку
    """
    if not await ensure_recruit_schema():
        return "❌ Схема не готова."

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            # чи approver у фортеці, і чи він керівник?
            row_role = await conn.fetchrow(
                "SELECT role FROM fort_members WHERE fort_id=$1 AND tg_id=$2",
                fort_id,
                approver_tg,
            )
            if not row_role:
                return "❌ Ти не в цій заставі."
            role_txt = str(row_role["role"])
            if role_txt not in ("hetman", "head"):
                return "❌ В тебе нема прав приймати людей."

            # чи є така заявка?
            req_row = await conn.fetchrow(
                "SELECT id FROM fort_join_requests WHERE fort_id=$1 AND tg_id=$2",
                fort_id,
                target_tg,
            )
            if not req_row:
                return "❌ Немає такої заявки."

            # кандидат міг уже десь вступити?
            row_already = await conn.fetchrow(
                "SELECT fort_id FROM fort_members WHERE tg_id=$1",
                target_tg,
            )
            if row_already:
                # вже у якійсь — прибираємо заявку
                await conn.execute(
                    "DELETE FROM fort_join_requests WHERE fort_id=$1 AND tg_id=$2",
                    fort_id,
                    target_tg,
                )
                return "ℹ️ Він уже в іншій заставі."

            # додаємо в члени
            await conn.execute(
                """
                INSERT INTO fort_members(fort_id, tg_id, role)
                VALUES ($1,$2,'novachok')
                ON CONFLICT DO NOTHING
                """,
                fort_id,
                target_tg,
            )

            # прибираємо заявку
            await conn.execute(
                "DELETE FROM fort_join_requests WHERE fort_id=$1 AND tg_id=$2",
                fort_id,
                target_tg,
            )

        return "✅ Прийнято. Гравця додано."
    except Exception as e:
        logger.warning(f"approve_request failed: {e}")
        return "❌ Сталася помилка."


async def reject_request(fort_id: int, target_tg: int, approver_tg: int) -> str:
    """
    Лідер (hetman/head) відхиляє заявку.
    Просто видаляємо запис з fort_join_requests.
    """
    if not await ensure_recruit_schema():
        return "❌ Схема не готова."

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row_role = await conn.fetchrow(
                "SELECT role FROM fort_members WHERE fort_id=$1 AND tg_id=$2",
                fort_id,
                approver_tg,
            )
            if not row_role:
                return "❌ Ти не в цій заставі."
            role_txt = str(row_role["role"])
            if role_txt not in ("hetman", "head"):
                return "❌ В тебе нема прав відхиляти."

            await conn.execute(
                "DELETE FROM fort_join_requests WHERE fort_id=$1 AND tg_id=$2",
                fort_id,
                target_tg,
            )

        return "🚫 Відхилено."
    except Exception as e:
        logger.warning(f"reject_request failed: {e}")
        return "❌ Сталася помилка."