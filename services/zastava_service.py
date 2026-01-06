# services/zastava_service.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, List, Tuple, Dict
from datetime import datetime
from loguru import logger

# База
try:
    from database import get_pool  # type: ignore
except Exception:
    get_pool = None  # без БД нічого робити тут не будемо


# ---------- Константи / Ролі ----------
ROLES = ("hetman", "head", "tysiachnyk", "sotnyk", "desiatnyk", "novachok")
DEFAULT_ROLE = "novachok"
LEAD_ROLES = {"hetman", "head"}  # хто може призначати ролі

# Ліміти для чату
CHAT_MAX_LEN = 600
CHAT_RECENT_LIMIT = 40


# ---------- DTO ----------
@dataclass
class FortInfo:
    id: int
    name: str
    created_by: int
    created_at: datetime
    members_count: int
    treasury: int
    leaders: List[Tuple[int, str]]  # [(tg_id, role), ...]


@dataclass
class FortStats:
    battles: int
    wins: int
    losses: int
    donations: int


@dataclass
class ChatMsg:
    id: int
    tg_id: int
    name: str
    text: str
    created_at: datetime


# ---------- Схема ----------
SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS forts (
        id BIGSERIAL PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        created_by BIGINT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT now()
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS fort_members (
        fort_id BIGINT NOT NULL REFERENCES forts(id) ON DELETE CASCADE,
        tg_id BIGINT NOT NULL,
        role TEXT NOT NULL DEFAULT 'novachok',
        joined_at TIMESTAMP NOT NULL DEFAULT now(),
        PRIMARY KEY (fort_id, tg_id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS fort_treasury (
        fort_id BIGINT PRIMARY KEY REFERENCES forts(id) ON DELETE CASCADE,
        gold BIGINT NOT NULL DEFAULT 0,
        updated_at TIMESTAMP NOT NULL DEFAULT now()
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS fort_chat (
        id BIGSERIAL PRIMARY KEY,
        fort_id BIGINT NOT NULL REFERENCES forts(id) ON DELETE CASCADE,
        tg_id BIGINT NOT NULL,
        name TEXT NOT NULL,
        text TEXT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT now()
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS fort_stats (
        fort_id BIGINT PRIMARY KEY REFERENCES forts(id) ON DELETE CASCADE,
        battles BIGINT NOT NULL DEFAULT 0,
        wins    BIGINT NOT NULL DEFAULT 0,
        losses  BIGINT NOT NULL DEFAULT 0,
        donations BIGINT NOT NULL DEFAULT 0,
        updated_at TIMESTAMP NOT NULL DEFAULT now()
    );
    """,
]

async def ensure_schema() -> bool:
    if not get_pool:
        logger.warning("zastava_service: no DB pool")
        return False
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            for sql in SCHEMA_SQL:
                await conn.execute(sql)
        return True
    except Exception as e:
        logger.error(f"zastava_service.ensure_schema failed: {e}")
        return False


# ---------- CRUD Застав ----------
async def create_fort(tg_id: int, name: str) -> Optional[int]:
    """
    Створити заставу, зробити автора гетьманом, створити казну та статистику.
    """
    if not await ensure_schema():
        return None
    name = (name or "").strip()
    if not name:
        return None
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO forts(name, created_by) VALUES ($1,$2) RETURNING id, created_at",
                name, tg_id
            )
            fort_id = int(row["id"])
            await conn.execute(
                "INSERT INTO fort_members(fort_id, tg_id, role) VALUES ($1,$2,'hetman') ON CONFLICT DO NOTHING",
                fort_id, tg_id
            )
            await conn.execute(
                "INSERT INTO fort_treasury(fort_id, gold) VALUES ($1,0) ON CONFLICT DO NOTHING",
                fort_id
            )
            await conn.execute(
                "INSERT INTO fort_stats(fort_id) VALUES ($1) ON CONFLICT DO NOTHING",
                fort_id
            )
        return fort_id
    except Exception as e:
        logger.warning(f"create_fort failed: {e}")
        return None


async def get_fort_by_name(name: str) -> Optional[int]:
    if not await ensure_schema():
        return None
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT id FROM forts WHERE name=$1", name)
            return int(row["id"]) if row else None
    except Exception:
        return None


async def list_forts(limit: int = 50) -> List[FortInfo]:
    if not await ensure_schema():
        return []
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT f.*, 
                       COALESCE(t.gold,0) AS gold,
                       (SELECT COUNT(*) FROM fort_members m WHERE m.fort_id=f.id) AS mcount
                FROM forts f
                LEFT JOIN fort_treasury t ON t.fort_id=f.id
                ORDER BY f.id DESC
                LIMIT $1
                """, limit
            )
            result: List[FortInfo] = []
            for r in rows:
                leaders_rows = await conn.fetch(
                    "SELECT tg_id, role FROM fort_members WHERE fort_id=$1 AND role IN ('hetman','head')",
                    r["id"]
                )
                leaders = [(int(x["tg_id"]), str(x["role"])) for x in leaders_rows]
                result.append(FortInfo(
                    id=int(r["id"]),
                    name=str(r["name"]),
                    created_by=int(r["created_by"]),
                    created_at=r["created_at"],
                    members_count=int(r["mcount"]),
                    treasury=int(r["gold"]),
                    leaders=leaders
                ))
            return result
    except Exception as e:
        logger.warning(f"list_forts failed: {e}")
        return []


async def get_member_fort(tg_id: int) -> Optional[int]:
    if not await ensure_schema():
        return None
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT fort_id FROM fort_members WHERE tg_id=$1", tg_id)
            return int(row["fort_id"]) if row else None
    except Exception:
        return None


async def join_fort(tg_id: int, fort_id: int, role: str = DEFAULT_ROLE) -> bool:
    if not await ensure_schema():
        return False
    if role not in ROLES:
        role = DEFAULT_ROLE
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO fort_members(fort_id, tg_id, role) VALUES ($1,$2,$3) "
                "ON CONFLICT (fort_id, tg_id) DO UPDATE SET role=EXCLUDED.role",
                fort_id, tg_id, role
            )
        return True
    except Exception as e:
        logger.warning(f"join_fort failed: {e}")
        return False


async def leave_fort(tg_id: int) -> bool:
    if not await ensure_schema():
        return False
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM fort_members WHERE tg_id=$1", tg_id)
        return True
    except Exception as e:
        logger.warning(f"leave_fort failed: {e}")
        return False


async def set_role(operator_tg_id: int, target_tg_id: int, role: str) -> bool:
    """
    Призначити роль у тій самій заставі, де знаходиться оператор.
    Дозволено лише лідерам (hetman/head).
    """
    if not await ensure_schema():
        return False
    if role not in ROLES:
        return False
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            op = await conn.fetchrow(
                "SELECT fort_id, role FROM fort_members WHERE tg_id=$1",
                operator_tg_id
            )
            if not op or op["role"] not in LEAD_ROLES:
                return False
            fort_id = int(op["fort_id"])
            tgt = await conn.fetchrow(
                "SELECT fort_id FROM fort_members WHERE tg_id=$1",
                target_tg_id
            )
            if not tgt or int(tgt["fort_id"]) != fort_id:
                return False
            await conn.execute(
                "UPDATE fort_members SET role=$3 WHERE fort_id=$1 AND tg_id=$2",
                fort_id, target_tg_id, role
            )
        return True
    except Exception as e:
        logger.warning(f"set_role failed: {e}")
        return False


# ---------- Казна ----------
async def get_treasury(fort_id: int) -> int:
    if not await ensure_schema():
        return 0
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT gold FROM fort_treasury WHERE fort_id=$1",
                fort_id
            )
            return int(row["gold"]) if row else 0
    except Exception:
        return 0


async def add_gold(fort_id: int, amount: int) -> int:
    if not await ensure_schema():
        return 0
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO fort_treasury(fort_id, gold) VALUES ($1, GREATEST($2,0))
                ON CONFLICT (fort_id) DO UPDATE SET
                    gold = GREATEST(fort_treasury.gold + EXCLUDED.gold, 0),
                    updated_at = now()
                """,
                fort_id, amount
            )
            row = await conn.fetchrow("SELECT gold FROM fort_treasury WHERE fort_id=$1", fort_id)
            new_gold = int(row["gold"]) if row else 0
            # статистика пожертв
            if amount > 0:
                await conn.execute(
                    "INSERT INTO fort_stats(fort_id, donations) VALUES ($1,$2) "
                    "ON CONFLICT (fort_id) DO UPDATE SET donations = fort_stats.donations + EXCLUDED.donations, updated_at=now()",
                    fort_id, amount
                )
            return new_gold
    except Exception as e:
        logger.warning(f"add_gold failed: {e}")
        return 0


async def spend_gold(fort_id: int, amount: int) -> Optional[int]:
    """
    Списати з казни. Повертає новий баланс або None якщо не вистачає.
    """
    if amount <= 0:
        return await get_treasury(fort_id)
    if not await ensure_schema():
        return None
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT gold FROM fort_treasury WHERE fort_id=$1 FOR UPDATE", fort_id)
            cur = int(row["gold"]) if row else 0
            if cur < amount:
                return None
            new_gold = cur - amount
            await conn.execute(
                "UPDATE fort_treasury SET gold=$2, updated_at=now() WHERE fort_id=$1",
                fort_id, new_gold
            )
            return new_gold
    except Exception as e:
        logger.warning(f"spend_gold failed: {e}")
        return None


# ---------- Статистика ----------
async def bump_battle(fort_id: int, win: bool):
    if not await ensure_schema():
        return
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            if win:
                await conn.execute(
                    "INSERT INTO fort_stats(fort_id, battles, wins) VALUES ($1,1,1) "
                    "ON CONFLICT (fort_id) DO UPDATE SET battles=fort_stats.battles+1, wins=fort_stats.wins+1, updated_at=now()",
                    fort_id
                )
            else:
                await conn.execute(
                    "INSERT INTO fort_stats(fort_id, battles, losses) VALUES ($1,1,1) "
                    "ON CONFLICT (fort_id) DO UPDATE SET battles=fort_stats.battles+1, losses=fort_stats.losses+1, updated_at=now()",
                    fort_id
                )
    except Exception as e:
        logger.warning(f"bump_battle failed: {e}")


async def get_stats(fort_id: int) -> FortStats:
    if not await ensure_schema():
        return FortStats(battles=0, wins=0, losses=0, donations=0)
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM fort_stats WHERE fort_id=$1", fort_id)
            if not row:
                return FortStats(0, 0, 0, 0)
            return FortStats(
                battles=int(row["battles"]),
                wins=int(row["wins"]),
                losses=int(row["losses"]),
                donations=int(row["donations"]),
            )
    except Exception as e:
        logger.warning(f"get_stats failed: {e}")
        return FortStats(0, 0, 0, 0)


# ---------- Чат застави ----------
async def post_message(fort_id: int, tg_id: int, name: str, text: str) -> Optional[int]:
    if not await ensure_schema():
        return None
    clean = (text or "").strip()
    if not clean:
        return None
    if len(clean) > CHAT_MAX_LEN:
        clean = clean[:CHAT_MAX_LEN]
    name = (name or "Герой").strip()
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO fort_chat(fort_id, tg_id, name, text) VALUES ($1,$2,$3,$4) RETURNING id",
                fort_id, tg_id, name, clean
            )
            return int(row["id"]) if row else None
    except Exception as e:
        logger.warning(f"post_message failed: {e}")
        return None


async def list_recent(fort_id: int, limit: int = CHAT_RECENT_LIMIT) -> List[ChatMsg]:
    if not await ensure_schema():
        return []
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, tg_id, name, text, created_at FROM fort_chat WHERE fort_id=$1 "
                "ORDER BY id DESC LIMIT $2",
                fort_id, limit
            )
        msgs = [
            ChatMsg(
                id=int(r["id"]),
                tg_id=int(r["tg_id"]),
                name=str(r["name"]),
                text=str(r["text"]),
                created_at=r["created_at"],
            )
            for r in rows
        ]
        msgs.reverse()  # старі зверху
        return msgs
    except Exception as e:
        logger.warning(f"list_recent failed: {e}")
        return []


# ---------- Зведена інформація ----------
async def get_fort_info(fort_id: int) -> Optional[FortInfo]:
    if not await ensure_schema():
        return None
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            f = await conn.fetchrow("SELECT * FROM forts WHERE id=$1", fort_id)
            if not f:
                return None
            t = await conn.fetchrow("SELECT gold FROM fort_treasury WHERE fort_id=$1", fort_id)
            mcount = await conn.fetchval("SELECT COUNT(*) FROM fort_members WHERE fort_id=$1", fort_id)
            leaders_rows = await conn.fetch(
                "SELECT tg_id, role FROM fort_members WHERE fort_id=$1 AND role IN ('hetman','head')",
                fort_id
            )
            leaders = [(int(x["tg_id"]), str(x["role"])) for x in leaders_rows]
            return FortInfo(
                id=int(f["id"]),
                name=str(f["name"]),
                created_by=int(f["created_by"]),
                created_at=f["created_at"],
                members_count=int(mcount or 0),
                treasury=int(t["gold"]) if t else 0,
                leaders=leaders,
            )
    except Exception as e:
        logger.warning(f"get_fort_info failed: {e}")
        return None