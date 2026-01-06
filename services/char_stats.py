from __future__ import annotations

from typing import Dict, Tuple, Optional, List, Any
from loguru import logger
from contextlib import suppress
import json

# ---- DB --------------------------------------------------------------
try:
    from db import get_pool  # type: ignore
except Exception:
    get_pool = None  # fallback якщо нема конекту

# ---- форти (бонуси застави) ------------------------------------------
try:
    from services.fort_levels import (
        get_fort_level,
        bonuses_for_level,
    )  # type: ignore
except Exception:
    async def get_fort_level(_fid: int) -> Tuple[int, int, int]:  # type: ignore
        return (1, 0, 100)

    def bonuses_for_level(_lvl: int):  # type: ignore
        return {
            "hp_pct": 0.0,
            "mp_pct": 0.0,
            "atk_pct": 0.0,
            "def_pct": 0.0,
            "coin_pct": 0.0,
            "drop_pct": 0.0,
            "phys_attack_pct": 0.0,
            "magic_attack_pct": 0.0,
            "phys_defense_pct": 0.0,
            "magic_defense_pct": 0.0,
        }

# ---------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------

def _as_float(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default

def _as_int(x, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return default

def _merge_pct(dst: Dict[str, float], src: Dict[str, float]) -> None:
    for k, v in (src or {}).items():
        with suppress(Exception):
            dst[k] = float(dst.get(k, 0.0)) + float(v or 0.0)

def _maybe_parse_json(val):
    if isinstance(val, (dict, list)) or val is None:
        return val
    if isinstance(val, str):
        with suppress(Exception):
            return json.loads(val)
    return None

def _normalize_stats(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, (str, bytes)):
        with suppress(Exception):
            return json.loads(raw)
        return {}
    try:
        return dict(raw)  # asyncpg Record / mapping
    except Exception:
        return {}

# ---------------------------------------------------------------------
# БАЗОВІ СТАТИ ВІД ЛЕВЕЛА
# ---------------------------------------------------------------------

def _base_stats_for_level(level: int) -> Dict[str, int]:
    L = max(1, int(level))

    hp_max = 60 + 12 * (L - 1)
    mp_max = 18 + 3 * (L - 1)

    phys_attack = 6 + 2 * (L - 1)
    phys_defense = 4 + 1 * (L - 1)

    magic_attack = max(0, 1 + 1 * (L - 1))
    magic_defense = max(0, 1 + 1 * (L - 1))

    # legacy
    atk = phys_attack
    defense = phys_defense

    return {
        "hp_max": hp_max,
        "mp_max": mp_max,
        "phys_attack": phys_attack,
        "magic_attack": magic_attack,
        "phys_defense": phys_defense,
        "magic_defense": magic_defense,
        "atk": atk,
        "def": defense,
    }

# ---------------------------------------------------------------------
# AUTO-ENSURE схеми для races/classes
# ---------------------------------------------------------------------

async def _ensure_classes_races_columns() -> None:
    if not get_pool:
        return
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("ALTER TABLE classes ADD COLUMN IF NOT EXISTS stat_mult JSONB")
            await conn.execute("ALTER TABLE classes ADD COLUMN IF NOT EXISTS passives JSONB")
            await conn.execute("ALTER TABLE classes ADD COLUMN IF NOT EXISTS tat_mult FLOAT DEFAULT 1.0")

            await conn.execute("ALTER TABLE races ADD COLUMN IF NOT EXISTS stat_mult JSONB")
            await conn.execute("ALTER TABLE races ADD COLUMN IF NOT EXISTS passives JSONB")
            await conn.execute("ALTER TABLE races ADD COLUMN IF NOT EXISTS tat_mult FLOAT DEFAULT 1.0")
    except Exception as e:
        logger.warning(f"char_stats: ensure columns fail: {e}")

# ---------------------------------------------------------------------
# ЗАВАНТАЖЕННЯ РІВНЯ/ЗАСТАВИ/РАСИ/КЛАСУ
# ---------------------------------------------------------------------

async def _load_player_level_fort_race_class(
    tg_id: int,
) -> tuple[int, Optional[int], Optional[str], Optional[str]]:
    if not get_pool:
        return (1, None, None, None)
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row_p = await conn.fetchrow(
                "SELECT COALESCE(level,1) AS level, race_key, class_key FROM players WHERE tg_id=$1",
                tg_id,
            )
            level = int(row_p["level"]) if row_p else 1
            race_key = row_p["race_key"] if row_p else None
            class_key = row_p["class_key"] if row_p else None

            row_f = await conn.fetchrow("SELECT fort_id FROM fort_members WHERE tg_id=$1", tg_id)
            fort_id = int(row_f["fort_id"]) if row_f and row_f["fort_id"] is not None else None

        return (level, fort_id, race_key, class_key)
    except Exception as e:
        logger.warning(f"char_stats: _load_player_level_fort_race_class fail {e}")
        return (1, None, None, None)

async def _load_fort_bonus(fort_id: Optional[int]) -> Dict[str, float]:
    base = {
        "hp_pct": 0.0,
        "mp_pct": 0.0,
        "atk_pct": 0.0,
        "def_pct": 0.0,
        "coin_pct": 0.0,
        "drop_pct": 0.0,
        "phys_attack_pct": 0.0,
        "magic_attack_pct": 0.0,
        "phys_defense_pct": 0.0,
        "magic_defense_pct": 0.0,
    }
    if fort_id is None:
        return base
    try:
        lvl, _xp_in_lvl, _need_next = await get_fort_level(fort_id)
        b = bonuses_for_level(lvl) or {}
        for k in list(base.keys()):
            with suppress(Exception):
                base[k] = float(b.get(k, base[k]) or base[k])
        return base
    except Exception as e:
        logger.warning(f"char_stats: _load_fort_bonus fail {e}")
        return base

# ---------------------------------------------------------------------
# РАСА/КЛАС: МНОЖНИКИ + ПАСИВКИ
# ---------------------------------------------------------------------

async def _load_stat_mult(table: str, key: Optional[str]) -> Dict[str, float]:
    if not get_pool or not key:
        return {}

    await _ensure_classes_races_columns()

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(f"SELECT stat_mult, tat_mult FROM {table} WHERE key=$1", key)
        if not row:
            return {}

        payload = _maybe_parse_json(row["stat_mult"])

        if not payload and row["tat_mult"] is not None:
            t = _as_float(row["tat_mult"], 1.0)
            pct = t - 1.0
            return {
                "hp_pct": pct,
                "mp_pct": 0.0,
                "phys_attack_pct": pct,
                "magic_attack_pct": 0.0,
                "phys_defense_pct": 0.0,
                "magic_defense_pct": 0.0,
            }

        if not isinstance(payload, dict):
            return {}

        hp_m = _as_float(payload.get("hp", 1.0), 1.0)
        mp_m = _as_float(payload.get("mp", 1.0), 1.0)

        phys_atk_m = _as_float(payload.get("phys_attack", payload.get("attack", 1.0)), 1.0)
        mag_atk_m = _as_float(payload.get("magic_attack", 1.0), 1.0)

        phys_def_m = _as_float(payload.get("phys_defense", payload.get("defense", 1.0)), 1.0)
        mag_def_m = _as_float(payload.get("magic_defense", 1.0), 1.0)

        return {
            "hp_pct": hp_m - 1.0,
            "mp_pct": mp_m - 1.0,
            "phys_attack_pct": phys_atk_m - 1.0,
            "magic_attack_pct": mag_atk_m - 1.0,
            "phys_defense_pct": phys_def_m - 1.0,
            "magic_defense_pct": mag_def_m - 1.0,
        }
    except Exception as e:
        logger.warning(f"char_stats: _load_stat_mult({table}) fail {e}")
        return {}

async def _load_passives_pct(table: str, key: Optional[str]) -> Dict[str, float]:
    if not get_pool or not key:
        return {}

    await _ensure_classes_races_columns()

    out = {
        "hp_pct": 0.0,
        "mp_pct": 0.0,
        "atk_pct": 0.0,
        "def_pct": 0.0,
        "phys_attack_pct": 0.0,
        "magic_attack_pct": 0.0,
        "phys_defense_pct": 0.0,
        "magic_defense_pct": 0.0,
    }

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(f"SELECT passives FROM {table} WHERE key=$1", key)
        if not row or row["passives"] is None:
            return out

        arr = _maybe_parse_json(row["passives"]) or []
        if not isinstance(arr, list):
            return out

        for p in arr:
            if not isinstance(p, dict):
                continue
            for k in list(out.keys()):
                with suppress(Exception):
                    out[k] += _as_float(p.get(k, 0.0), 0.0)

        return out
    except Exception as e:
        logger.warning(f"char_stats: _load_passives_pct({table}) fail {e}")
        return out

async def _load_race_class_bonus(race_key: Optional[str], class_key: Optional[str]) -> Dict[str, float]:
    total = {
        "hp_pct": 0.0,
        "mp_pct": 0.0,
        "atk_pct": 0.0,
        "def_pct": 0.0,
        "phys_attack_pct": 0.0,
        "magic_attack_pct": 0.0,
        "phys_defense_pct": 0.0,
        "magic_defense_pct": 0.0,
    }
    _merge_pct(total, await _load_stat_mult("races", race_key))
    _merge_pct(total, await _load_stat_mult("classes", class_key))
    _merge_pct(total, await _load_passives_pct("races", race_key))
    _merge_pct(total, await _load_passives_pct("classes", class_key))
    return total

# ---------------------------------------------------------------------
# ✅ ЕКІП: беремо з player_inventory JOIN items
# ---------------------------------------------------------------------

async def _get_equipped_from_inventory(tg_id: int) -> List[Dict[str, Any]]:
    """
    Повертає список екіпнутих речей.
    Не фільтруємо по category, щоб не ламатись якщо seed не поставив category='equip'.
    """
    if not get_pool:
        return []

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
              i.slot,
              i.atk,
              i.defense,
              i.hp,
              i.mp,
              i.stats
            FROM player_inventory pi
            JOIN items i ON i.id = pi.item_id
            WHERE pi.tg_id = $1
              AND pi.is_equipped = TRUE
              AND i.slot IS NOT NULL
            """,
            tg_id,
        )

    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append({
            "slot": r["slot"],
            "atk": _as_int(r["atk"], 0),
            "defense": _as_int(r["defense"], 0),
            "hp": _as_int(r["hp"], 0),
            "mp": _as_int(r["mp"], 0),
            "stats": _normalize_stats(r["stats"]),
        })
    return out

# ---------------------------------------------------------------------
# ФІНАЛЬНА ЗБІРКА СТАТІВ
# ---------------------------------------------------------------------

async def calc_final_stats(
    tg_id: int,
    level: int,
    fort_bonus: Dict[str, float],
    race_class_bonus: Optional[Dict[str, float]] = None,
) -> Dict[str, int]:
    base = _base_stats_for_level(level)

    try:
        equipped_items = await _get_equipped_from_inventory(tg_id)
    except Exception as e:
        logger.warning(f"char_stats: _get_equipped_from_inventory fail {e}")
        equipped_items = []

    bonus = {
        "hp": 0,
        "mp": 0,
        "atk": 0,
        "def": 0,
        "phys_attack": 0,
        "magic_attack": 0,
        "phys_defense": 0,
        "magic_defense": 0,
    }

    for it in equipped_items:
        bonus["hp"] += _as_int(it.get("hp", 0), 0)
        bonus["mp"] += _as_int(it.get("mp", 0), 0)

        # legacy (колонки items)
        bonus["atk"] += _as_int(it.get("atk", 0), 0)
        bonus["def"] += _as_int(it.get("defense", 0), 0)

        # нові (якщо колись будуть у stats JSON)
        st = it.get("stats", {}) or {}
        bonus["phys_attack"] += _as_int(st.get("phys_attack", st.get("phys_atk", 0)), 0)
        bonus["magic_attack"] += _as_int(st.get("magic_attack", st.get("mag_atk", 0)), 0)
        bonus["phys_defense"] += _as_int(st.get("phys_defense", st.get("phys_def", 0)), 0)
        bonus["magic_defense"] += _as_int(st.get("magic_defense", st.get("mag_def", 0)), 0)

    # flat
    total_hp = base["hp_max"] + bonus["hp"]
    total_mp = base["mp_max"] + bonus["mp"]

    # старий atk/def додаємо у фіз, щоб старий екіп працював
    total_phys_atk = base["phys_attack"] + bonus["phys_attack"] + bonus["atk"]
    total_mag_atk = base["magic_attack"] + bonus["magic_attack"]

    total_phys_def = base["phys_defense"] + bonus["phys_defense"] + bonus["def"]
    total_mag_def = base["magic_defense"] + bonus["magic_defense"]

    # множники
    rc = race_class_bonus or {}

    hp_pct = float(rc.get("hp_pct", 0.0)) + float(fort_bonus.get("hp_pct", 0.0) or 0.0)
    mp_pct = float(rc.get("mp_pct", 0.0)) + float(fort_bonus.get("mp_pct", 0.0) or 0.0)

    atk_pct_old = float(rc.get("atk_pct", 0.0)) + float(fort_bonus.get("atk_pct", 0.0) or 0.0)
    def_pct_old = float(rc.get("def_pct", 0.0)) + float(fort_bonus.get("def_pct", 0.0) or 0.0)

    phys_atk_pct = float(rc.get("phys_attack_pct", 0.0)) + float(fort_bonus.get("phys_attack_pct", 0.0) or 0.0)
    mag_atk_pct = float(rc.get("magic_attack_pct", 0.0)) + float(fort_bonus.get("magic_attack_pct", 0.0) or 0.0)
    phys_def_pct = float(rc.get("phys_defense_pct", 0.0)) + float(fort_bonus.get("phys_defense_pct", 0.0) or 0.0)
    mag_def_pct = float(rc.get("magic_defense_pct", 0.0)) + float(fort_bonus.get("magic_defense_pct", 0.0) or 0.0)

    total_hp = int(round(total_hp * (1.0 + hp_pct)))
    total_mp = int(round(total_mp * (1.0 + mp_pct)))

    # старі atk/def множники підсилюють фіз
    total_phys_atk = int(round(total_phys_atk * (1.0 + atk_pct_old + phys_atk_pct)))
    total_phys_def = int(round(total_phys_def * (1.0 + def_pct_old + phys_def_pct)))

    total_mag_atk = int(round(total_mag_atk * (1.0 + mag_atk_pct)))
    total_mag_def = int(round(total_mag_def * (1.0 + mag_def_pct)))

    # clamp
    total_hp = max(1, total_hp)
    total_mp = max(0, total_mp)

    total_phys_atk = max(1, total_phys_atk)
    total_phys_def = max(0, total_phys_def)

    total_mag_atk = max(0, total_mag_atk)
    total_mag_def = max(0, total_mag_def)

    # legacy
    atk = total_phys_atk
    defense = total_phys_def

    return {
        "hp_max": total_hp,
        "mp_max": total_mp,
        "phys_attack": total_phys_atk,
        "magic_attack": total_mag_atk,
        "phys_defense": total_phys_def,
        "magic_defense": total_mag_def,
        "atk": atk,
        "def": defense,
    }

# ---------------------------------------------------------------------
# ✅ ПУБЛІЧНИЙ ХЕЛПЕР (який імпортує battle.py)
# ---------------------------------------------------------------------

async def get_full_stats_for_player(tg_id: int) -> Dict[str, int]:
    """
    Враховує:
      - level
      - race/class (races/classes: stat_mult/tat_mult + passives)
      - bonuses_for_level(fort)
      - екіп (player_inventory JOIN items)
    Повертає: hp/mp + phys/magic atk/def (+ legacy atk/def)
    """
    try:
        await _ensure_classes_races_columns()
        level, fort_id, race_key, class_key = await _load_player_level_fort_race_class(tg_id)
        fort_bonus = await _load_fort_bonus(fort_id)
        rc_bonus = await _load_race_class_bonus(race_key, class_key)
        return await calc_final_stats(tg_id, level, fort_bonus, rc_bonus)
    except Exception as e:
        logger.warning(f"char_stats: get_full_stats_for_player fallback {e}")
        fb = _base_stats_for_level(1)
        return {
            "hp_max": fb["hp_max"],
            "mp_max": fb["mp_max"],
            "phys_attack": fb["phys_attack"],
            "magic_attack": fb["magic_attack"],
            "phys_defense": fb["phys_defense"],
            "magic_defense": fb["magic_defense"],
            "atk": fb["atk"],
            "def": fb["def"],
        }