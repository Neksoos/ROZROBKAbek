# routers/city_entry.py
from __future__ import annotations

from typing import Optional
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from loguru import logger

from db import get_pool
from services.regeneration import apply_full_regen
from services.progress import xp_required_for, _ensure_player_progress_schema
from services.char_stats import get_full_stats_for_player
from services.energy import BASE_ENERGY_MAX

# ✅ achievements metrics
from services.achievements.metrics import inc_metric, try_mark_event_once

from models.player import PlayerDTO
from routers.auth import get_player  # ✅ initData -> PlayerDTO

router = APIRouter(prefix="/api", tags=["city"])


class EntryDTO(BaseModel):
    regen_hp: int
    regen_mp: int
    regen_energy: int


class DailyLoginDTO(BaseModel):
    xp_gain: int
    coins_gain: int
    got_kleynod: bool


class PlayerOutDTO(BaseModel):
    tg_id: int
    name: str

    level: int
    xp: int
    xp_needed: int

    race_key: Optional[str] = None
    class_key: Optional[str] = None
    gender: Optional[str] = None

    hp_max: int
    mp_max: int
    atk: int
    defense: int

    chervontsi: int
    kleynody: int

    hp: int
    mp: int
    energy: int
    energy_max: int


class CityEntryResponse(BaseModel):
    ok: bool
    player: PlayerOutDTO
    entry: Optional[EntryDTO] = None
    daily_login: Optional[DailyLoginDTO] = None


def _today_utc_key() -> str:
    return datetime.now(timezone.utc).date().isoformat()  # YYYY-MM-DD


@router.get("/city-entry", response_model=CityEntryResponse)
async def city_entry(player: PlayerDTO = Depends(get_player)) -> CityEntryResponse:
    tg_id = int(player.tg_id)

    pool = await get_pool()
    await _ensure_player_progress_schema()

    # ✅ гравець має існувати (інакше registration/auth ще не створили рядок)
    async with pool.acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM players WHERE tg_id=$1", tg_id)
    if not exists:
        raise HTTPException(403, "PLAYER_NOT_REGISTERED")

    # 1) реген
    regen = None
    try:
        regen = await apply_full_regen(tg_id)
    except Exception as e:
        logger.warning(f"city_entry: apply_full_regen fail tg_id={tg_id}: {e}")
        regen = None

    # ✅ achievements: 1 раз на добу
    try:
        day_key = _today_utc_key()
        event_key = f"login_day:{day_key}"
        first_today = await try_mark_event_once(tg_id, event_key)
        if first_today:
            await inc_metric(tg_id, "login_days_total", 1)
    except Exception as e:
        logger.warning(f"city_entry: achievements login_days_total fail tg_id={tg_id}: {e}")

    # ✅ 2) daily login
    daily_login: Optional[DailyLoginDTO] = None
    try:
        from services.daily_login import process_daily_login  # type: ignore

        xp_gain, coins_gain, got_kleynod = await process_daily_login(tg_id)
        if xp_gain > 0 or coins_gain > 0 or got_kleynod:
            daily_login = DailyLoginDTO(
                xp_gain=int(xp_gain),
                coins_gain=int(coins_gain),
                got_kleynod=bool(got_kleynod),
            )
    except Exception as e:
        logger.warning(f"city_entry: process_daily_login fail tg_id={tg_id}: {e}")
        daily_login = None

    # 3) читаємо гравця
    async with pool.acquire() as conn:
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
                mp,
                energy,
                energy_max
            FROM players
            WHERE tg_id = $1
            """,
            tg_id,
        )

    if not row:
        raise HTTPException(403, "PLAYER_NOT_REGISTERED")

    level = int(row["level"])
    xp = int(row["xp"])
    xp_needed = xp_required_for(level)

    # 4) повні стати
    try:
        stats = await get_full_stats_for_player(tg_id)
        hp_max = int(stats.get("hp_max", 1))
        mp_max = int(stats.get("mp_max", 0))
        atk = int(stats.get("atk", 1))
        defense = int(stats.get("def", 0))
    except Exception as e:
        logger.warning(f"city_entry: get_full_stats_for_player fail tg_id={tg_id}: {e}")
        hp_max = 1
        mp_max = 0
        atk = 1
        defense = 0

    # 5) поточні значення після регену
    if regen is not None:
        cur_hp = int(regen.hp_after)
        cur_mp = int(regen.mp_after)
        cur_energy = int(regen.energy_cur)
        cur_energy_max = int(regen.energy_max)
    else:
        cur_hp = int(row["hp"]) if row["hp"] is not None else hp_max
        cur_mp = int(row["mp"]) if row["mp"] is not None else mp_max
        cur_energy = int(row["energy"]) if row["energy"] is not None else BASE_ENERGY_MAX
        cur_energy_max = int(row["energy_max"]) if row["energy_max"] is not None else BASE_ENERGY_MAX

    # 6) попап регену
    entry: Optional[EntryDTO] = None
    if regen is not None:
        regen_hp = max(0, int(regen.hp_delta))
        regen_mp = max(0, int(regen.mp_delta))

        energy_delta = getattr(regen, "energy_delta", 0)
        try:
            regen_energy = max(0, int(energy_delta))
        except Exception:
            regen_energy = 0

        if regen_hp > 0 or regen_mp > 0 or regen_energy > 0:
            entry = EntryDTO(
                regen_hp=regen_hp,
                regen_mp=regen_mp,
                regen_energy=regen_energy,
            )

    player_dto = PlayerOutDTO(
        tg_id=int(row["tg_id"]),
        name=row["name"] or "",
        level=level,
        xp=xp,
        xp_needed=xp_needed,
        race_key=row["race_key"],
        class_key=row["class_key"],
        gender=row["gender"],
        hp_max=hp_max,
        mp_max=mp_max,
        atk=atk,
        defense=defense,
        chervontsi=int(row["chervontsi"]),
        kleynody=int(row["kleynody"]),
        hp=cur_hp,
        mp=cur_mp,
        energy=cur_energy,
        energy_max=cur_energy_max,
    )

    return CityEntryResponse(ok=True, player=player_dto, entry=entry, daily_login=daily_login)