# services/battle/repo.py
from __future__ import annotations

import json
from typing import Optional

from fastapi import HTTPException

from db import get_pool
from data.world_data import MOBS

from services.char_stats import get_full_stats_for_player  # type: ignore
from services.energy import get_energy

from services.battle.models import Hero


async def load_hero(tg_id: int) -> Hero:
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT name, hp, mp
        FROM players
        WHERE tg_id = $1
        """,
        tg_id,
    )
    if not row:
        raise HTTPException(404, "HERO_NOT_FOUND")

    stats = await get_full_stats_for_player(tg_id)

    hp_max = int(stats.get("hp_max", 1) or 1)
    mp_max = int(stats.get("mp_max", 0) or 0)

    hero_hp = int(row["hp"]) if row["hp"] and row["hp"] > 0 else hp_max
    hero_mp = int(row["mp"]) if row["mp"] is not None else mp_max

    energy, energy_max = await get_energy(tg_id)

    phys_atk = int(stats.get("phys_attack", stats.get("atk", 1)) or 1)
    mag_atk = int(stats.get("magic_attack", 0) or 0)
    phys_def = int(stats.get("phys_defense", stats.get("def", 0)) or 0)
    mag_def = int(stats.get("magic_defense", 0) or 0)

    legacy_atk = int(stats.get("atk", phys_atk) or phys_atk)
    legacy_def = int(stats.get("def", phys_def) or phys_def)

    return Hero(
        name=row["name"],
        hp=hero_hp,
        hp_max=hp_max,
        mp=hero_mp,
        mp_max=mp_max,
        phys_attack=phys_atk,
        magic_attack=mag_atk,
        phys_defense=phys_def,
        magic_defense=mag_def,
        atk=legacy_atk,
        def_=legacy_def,
        def_legacy=legacy_def,
        energy=energy,
        energy_max=energy_max,
    )


async def save_hero(tg_id: int, hero: Hero) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE players
        SET hp = $2, mp = $3
        WHERE tg_id = $1
        """,
        tg_id,
        hero.hp,
        hero.mp,
    )


async def refresh_hero_energy(hero: Hero, tg_id: int) -> Hero:
    energy, energy_max = await get_energy(tg_id)
    hero.energy = energy
    hero.energy_max = energy_max
    return hero


def _find_area_for_mob(mob_id: int) -> Optional[str]:
    for area_key, mob_list in MOBS:
        for mid, _name, _lvl in mob_list:
            if int(mid) == int(mob_id):
                return area_key
    return None


async def load_mob_from_db(mob_id: int) -> Optional[dict]:
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT
            id,
            name,
            level,
            COALESCE(hp, base_hp, 1) AS hp,
            COALESCE(phys_attack, atk, base_attack, GREATEST(1, level * 3)) AS phys_attack,
            COALESCE(magic_attack, 0) AS magic_attack,
            COALESCE(phys_defense, GREATEST(0, level * 2)) AS phys_defense,
            COALESCE(magic_defense, GREATEST(0, level)) AS magic_defense
        FROM mobs
        WHERE id = $1
        """,
        mob_id,
    )
    if not row:
        return None

    area_key = _find_area_for_mob(mob_id) or "slums"

    return {
        "id": int(row["id"]),
        "code": f"mob_{int(row['id'])}",
        "name": row["name"],
        "level": int(row["level"] or 1),
        "area": area_key,
        "hp": int(row["hp"] or 1),
        "phys_attack": int(row["phys_attack"] or 1),
        "magic_attack": int(row["magic_attack"] or 0),
        "phys_defense": int(row["phys_defense"] or 0),
        "magic_defense": int(row["magic_defense"] or 0),
    }


# ===========================
# HEAL HELPERS (NEW)
# ===========================
def extract_restore_from_item_stats(stats: object) -> tuple[int, int]:
    """
    В items.stats (JSONB або TEXT(JSON)) беремо hp/mp як величини відновлення.
    """
    if not stats:
        return (0, 0)

    if isinstance(stats, str):
        try:
            stats = json.loads(stats)
        except Exception:
            return (0, 0)

    if not isinstance(stats, dict):
        return (0, 0)

    hp = int(stats.get("hp") or 0)
    mp = int(stats.get("mp") or 0)
    return (max(0, hp), max(0, mp))


async def pick_and_consume_heal_item(conn, tg_id: int, hp_missing: int, mp_missing: int):
    """
    Автоматично підбирає найкращу їжу/зілля з інвентаря і списує 1 шт (qty-- або delete).
    Повертає (item_name, hp_restore, mp_restore) або None якщо нема.
    """
    rows = await conn.fetch(
        """
        SELECT
          pi.id AS inv_id,
          pi.item_id,
          COALESCE(pi.qty, 0) AS qty,
          i.name,
          i.category,
          i.stats
        FROM player_inventory pi
        JOIN items i ON i.id = pi.item_id
        WHERE pi.tg_id = $1
          AND COALESCE(pi.is_equipped, FALSE) = FALSE
          AND COALESCE(pi.qty, 0) > 0
          AND COALESCE(i.is_active, TRUE) = TRUE
          AND (i.category = 'food' OR i.category = 'potion')
        ORDER BY pi.id ASC
        """,
        tg_id,
    )

    best = None
    best_score = -1

    for r in rows:
        hp_restore, mp_restore = extract_restore_from_item_stats(r["stats"])
        if hp_restore <= 0 and mp_restore <= 0:
            continue

        score = min(hp_missing, hp_restore) + min(mp_missing, mp_restore)
        if score > best_score:
            best_score = score
            best = (r, hp_restore, mp_restore)

    if not best:
        return None

    r, hp_restore, mp_restore = best
    inv_id = int(r["inv_id"])
    qty = int(r["qty"] or 0)

    if qty > 1:
        await conn.execute(
            "UPDATE player_inventory SET qty = qty - 1 WHERE id = $1",
            inv_id,
        )
    else:
        await conn.execute("DELETE FROM player_inventory WHERE id = $1", inv_id)

    return (str(r["name"]), int(hp_restore), int(mp_restore))