routers/ptofile.py

from __future__ import annotations

from typing import Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Header, Query
from pydantic import BaseModel
from loguru import logger

from db import get_pool
from services.progress import xp_required_for, _ensure_player_progress_schema
from services.char_stats import get_full_stats_for_player
from services.energy import get_energy  # 🔥 наснага

router = APIRouter(prefix="/api", tags=["profile"])


# ─────────────────────────────────────────────
# tg_id (через proxy з X-Tg-Id) + fallback tg_id query
# ─────────────────────────────────────────────
async def get_tg_id(
    x_tg_id: Optional[str] = Header(default=None, alias="X-Tg-Id"),
    tg_id_q: Optional[int] = Query(default=None, alias="tg_id"),
) -> int:
    if tg_id_q:
        return int(tg_id_q)

    if not x_tg_id:
        raise HTTPException(status_code=401, detail="Missing X-Tg-Id")

    try:
        v = int(x_tg_id)
        if v <= 0:
            raise ValueError()
        return v
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid X-Tg-Id")


# ─────────────────────────────────────────────
# ENSURE: вага + qty (але БЕЗ падінь якщо таблиць нема)
# ─────────────────────────────────────────────
async def _table_exists(conn, table_name: str) -> bool:
    return bool(
        await conn.fetchval(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = $1
            """,
            table_name,
        )
    )


async def _ensure_inventory_weight_schema() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        items_ok = await _table_exists(conn, "items")
        inv_ok = await _table_exists(conn, "player_inventory")

        # Якщо це "копія" БД або ще не прогнаний seed — просто не валимось
        if not items_ok or not inv_ok:
            logger.warning(
                f"profile: skip inventory schema ensure (items={items_ok}, player_inventory={inv_ok})"
            )
            return

        await conn.execute("""ALTER TABLE items ADD COLUMN IF NOT EXISTS weight INTEGER DEFAULT 0;""")
        await conn.execute("""ALTER TABLE player_inventory ADD COLUMN IF NOT EXISTS qty INTEGER;""")
        await conn.execute("""UPDATE player_inventory SET qty = 1 WHERE qty IS NULL OR qty = 0;""")


# ─────────────────────────────────────────────
# DTO
# ─────────────────────────────────────────────
class ProfileDTOOut(BaseModel):
    tg_id: int
    name: str

    level: int
    xp: int
    xp_needed: int

    race_key: Optional[str] = None
    class_key: Optional[str] = None
    gender: Optional[str] = None

    # поточні значення
    hp: int
    mp: int
    energy: int
    energy_max: int

    # максимальні стати
    hp_max: int
    mp_max: int
    atk: int
    defense: int

    chervontsi: int
    kleynody: int

    # ✅ НОВЕ: ВАГА
    carry_weight: int
    carry_capacity: int


class EntryState(BaseModel):
    """Залишаємо для сумісності, але поки не використовуємо."""
    regen_hp: int
    regen_mp: int
    regen_energy: int


class ProfileResponse(BaseModel):
    ok: bool
    player: ProfileDTOOut
    entry: Optional[EntryState] = None


# ─────────────────────────────────────────────
# API: /api/profile
# ─────────────────────────────────────────────
@router.get("/profile", response_model=ProfileResponse)
async def get_profile(me: int = Depends(get_tg_id)) -> ProfileResponse:
    tg_id = int(me)

    pool = await get_pool()

    # щоб були level/xp у players (якщо схема стара)
    try:
        await _ensure_player_progress_schema()
    except Exception as e:
        logger.warning(f"profile: _ensure_player_progress_schema failed: {e}")

    # ✅ щоб була вага/qty (але не падаємо якщо БД "не та")
    try:
        await _ensure_inventory_weight_schema()
    except Exception as e:
        logger.warning(f"profile: _ensure_inventory_weight_schema failed: {e}")

    # 1️⃣ ЧИТАЄМО ГРАВЦЯ
    async with pool.acquire() as conn:
        players_ok = await _table_exists(conn, "players")
        if not players_ok:
            # якщо навіть players нема — це точно не той seed/БД
            raise HTTPException(status_code=500, detail="DB_SCHEMA_MISSING_PLAYERS")

        row = await conn.fetchrow(
            """
            SELECT
                tg_id,
                name,
                COALESCE(level, 1)      AS level,
                COALESCE(xp, 0)         AS xp,
                COALESCE(chervontsi, 0) AS chervontsi,
                COALESCE(kleynody, 0)   AS kleynody,
                race_key,
                class_key,
                gender,
                hp,
                mp
            FROM players
            WHERE tg_id = $1
            """,
            tg_id,
        )

        if not row:
            raise HTTPException(status_code=403, detail="Player not found")

        # ✅ Вага інвентаря (якщо таблиці є)
        carry_weight = 0
        items_ok = await _table_exists(conn, "items")
        inv_ok = await _table_exists(conn, "player_inventory")
        if items_ok and inv_ok:
            try:
                carry_weight = int(
                    await conn.fetchval(
                        """
                        SELECT COALESCE(SUM(COALESCE(pi.qty,1) * COALESCE(i.weight,0)), 0) AS carry_weight
                        FROM player_inventory pi
                        JOIN items i ON i.id = pi.item_id
                        WHERE pi.tg_id = $1
                        """,
                        tg_id,
                    )
                    or 0
                )
            except Exception as e:
                logger.warning(f"profile: carry_weight calc failed tg_id={tg_id}: {e}")
                carry_weight = 0

    level = int(row["level"])
    xp = int(row["xp"])
    xp_needed = xp_required_for(level)

    # 2️⃣ ПОВНІ СТАТИ (МАКСИ)
    try:
        stats = await get_full_stats_for_player(tg_id)
        hp_max = int(stats.get("hp_max", 1))
        mp_max = int(stats.get("mp_max", 0))
        atk = int(stats.get("atk", 1))
        defense = int(stats.get("def", 0))
    except Exception as e:
        logger.warning(f"profile: get_full_stats_for_player fail tg_id={tg_id}: {e}")
        hp_max = 1
        mp_max = 0
        atk = 1
        defense = 0

    # 3️⃣ ПОТОЧНІ HP/MP (обмежуємо max)
    hp_row = row["hp"]
    mp_row = row["mp"]

    if hp_row is None or int(hp_row) <= 0:
        hp_current = hp_max
    else:
        hp_current = min(int(hp_row), hp_max)

    if mp_row is None:
        mp_current = mp_max
    else:
        mp_current = min(int(mp_row), mp_max)

    # 4️⃣ НАСНАГА
    try:
        energy_current, energy_max = await get_energy(tg_id)
    except Exception as e:
        logger.warning(f"profile: get_energy fail tg_id={tg_id}: {e}")
        energy_current, energy_max = 0, 0

    # ✅ Максимальна вантажопідйомність (формула)
    carry_capacity = 50 + 5 * max(level - 1, 0)

    dto = ProfileDTOOut(
        tg_id=int(row["tg_id"]),
        name=row["name"] or "",
        level=level,
        xp=xp,
        xp_needed=xp_needed,
        race_key=row["race_key"],
        class_key=row["class_key"],
        gender=row["gender"],
        hp=hp_current,
        mp=mp_current,
        energy=energy_current,
        energy_max=energy_max,
        hp_max=hp_max,
        mp_max=mp_max,
        atk=atk,
        defense=defense,
        chervontsi=int(row["chervontsi"]),
        kleynody=int(row["kleynody"]),
        carry_weight=int(carry_weight),
        carry_capacity=int(carry_capacity),
    )

    return ProfileResponse(ok=True, player=dto, entry=None)