# routers/battle.py
from __future__ import annotations

import json
import random
import time
from typing import List, Optional
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field
from loguru import logger

from db import get_pool
from routers.redis_manager import get_redis
from data.world_data import MOBS

from services.char_stats import get_full_stats_for_player  # type: ignore
from services.progress import grant_xp_for_win  # type: ignore
from services.fort_levels import add_fort_xp_for_kill  # type: ignore
from services.rewards import distribute_drops
from services.energy import spend_energy, get_energy
from services.loot import get_loot_for_mob  # type: ignore

try:
    from services.night_watch import roll_medal, report_kill  # type: ignore
except Exception:

    def roll_medal(_lvl: int, _rng=None) -> bool:  # type: ignore
        return False

    async def report_kill(_tg_id: int, _lvl: int, _hp: int, _medal: bool) -> None:  # type: ignore
        return None


router = APIRouter(prefix="/battle", tags=["battle"])


# ─────────────────────────────────────────────
# initData -> tg_id
# ─────────────────────────────────────────────
def _tg_id_from_init_data(x_init_data: str | None) -> int:
    if not x_init_data or not x_init_data.strip():
        raise HTTPException(status_code=401, detail="Missing X-Init-Data")

    try:
        qs = parse_qs(x_init_data, keep_blank_values=True)
        user_raw = (qs.get("user") or [None])[0]
        if not user_raw:
            raise ValueError("user missing")

        user = json.loads(user_raw)
        tg_id = int(user.get("id"))
        return tg_id
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid X-Init-Data")


# ===========================
# MODELS
# ===========================
class Mob(BaseModel):
    code: str
    name: str
    hp: int
    hp_max: int
    level: int

    phys_attack: int
    magic_attack: int
    phys_defense: int
    magic_defense: int

    atk_legacy: Optional[int] = Field(None, alias="atk")

    class Config:
        allow_population_by_field_name = True
        extra = "allow"


class Hero(BaseModel):
    name: str
    hp: int
    hp_max: int
    mp: int
    mp_max: int

    phys_attack: int
    magic_attack: int
    phys_defense: int
    magic_defense: int

    atk: int
    def_: int
    def_legacy: Optional[int] = Field(None, alias="def")

    energy: int
    energy_max: int

    class Config:
        allow_population_by_field_name = True
        extra = "allow"


class BattleDTO(BaseModel):
    id: int
    state: str
    turn: int
    area_key: str
    mob: Mob
    hero: Hero
    note: str
    loot: List[str]


class BattleStartRequest(BaseModel):
    mob_id: int


class BattleActionRequest(BaseModel):
    battle_id: Optional[int] = None
    mode: Optional[str] = None  # ✅ "hp" | "mp" (optional, default = auto)


# ===========================
# UTILS
# ===========================
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


def calc_damage(atk: int, defense: int) -> int:
    base = max(1, atk - max(0, defense) // 2)
    spread = max(1, base // 4)
    return random.randint(max(1, base - spread), base + spread)


async def save_battle(r, tg_id: int, data: dict) -> None:
    await r.set(f"battle:{tg_id}", json.dumps(data), ex=600)


async def load_battle(r, tg_id: int) -> Optional[dict]:
    raw = await r.get(f"battle:{tg_id}")
    return json.loads(raw) if raw else None


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


def _mob_choose_attack_type(mob: Mob) -> str:
    if mob.magic_attack <= 0:
        return "phys"
    return "magic" if random.random() < 0.25 else "phys"


def _mob_to_dict(m: Mob) -> dict:
    return m.dict(by_alias=True)


def _hero_to_dict(h: Hero) -> dict:
    return h.dict(by_alias=True)


# ===========================
# LOOT + REWARD
# ===========================
async def _reward_items_new(tg_id: int, mob: Mob) -> List[str]:
    try:
        loot_items = await get_loot_for_mob(mob.code)
        logger.info("loot: mob={} lvl={} result={}", mob.code, mob.level, loot_items)
    except Exception:
        logger.exception("loot: get_loot_for_mob FAILED tg_id={} mob_code={}", tg_id, mob.code)
        return []

    if not loot_items:
        return []

    try:
        return await distribute_drops(tg_id, loot_items)
    except Exception:
        logger.exception("loot: distribute_drops FAILED tg_id={} loot={}", tg_id, loot_items)
        return []


async def _reward_for_win(tg_id: int, mob: Mob) -> List[str]:
    loot: List[str] = []

    try:
        xp_gain, _, _, _ = await grant_xp_for_win(tg_id, mob.code)
        if xp_gain > 0:
            loot.append(f"XP +{xp_gain}")
    except Exception:
        logger.exception("battle: grant_xp_for_win FAILED tg_id={} mob={}", tg_id, mob)

    try:
        g_gain, level_up, _ = await add_fort_xp_for_kill(tg_id, mob.code)
        if g_gain > 0:
            loot.append(f"Застава XP +{g_gain}")
            if level_up:
                loot.append(f"Застава отримала рівень {level_up}!")
    except Exception:
        logger.exception("battle: add_fort_xp_for_kill FAILED tg_id={} mob={}", tg_id, mob)

    coins = max(1, 3 + mob.level * 2)
    try:
        pool = await get_pool()
        await pool.execute(
            "UPDATE players SET chervontsi = chervontsi + $2 WHERE tg_id = $1",
            tg_id,
            coins,
        )
        loot.append(f"Червонці +{coins}")
    except Exception:
        logger.exception("battle: update chervontsi FAILED tg_id={} coins={}", tg_id, coins)

    try:
        medal = roll_medal(mob.level)
        await report_kill(tg_id, mob.level, mob.hp_max, medal)
        if medal:
            loot.append("🏅 Медаль Сторожа")
    except Exception:
        logger.exception("battle: night_watch report FAILED tg_id={} mob={}", tg_id, mob)

    try:
        loot.extend(await _reward_items_new(tg_id, mob))
    except Exception:
        logger.exception("battle: _reward_items_new FAILED tg_id={} mob={}", tg_id, mob)

    if not loot:
        loot.append("Трофей ×1")

    return loot


# ===========================
# HEAL HELPERS (NEW)
# ===========================
def _extract_restore_from_item_stats(stats: object) -> tuple[int, int]:
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


async def _pick_and_consume_heal_item(conn, tg_id: int, hp_missing: int, mp_missing: int):
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
        hp_restore, mp_restore = _extract_restore_from_item_stats(r["stats"])
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


# ===========================
# BATTLE START
# ===========================
@router.post("/start", response_model=BattleDTO)
async def battle_start(
    payload: BattleStartRequest,
    x_init_data: str | None = Header(default=None, alias="X-Init-Data"),
) -> BattleDTO:
    tg_id = _tg_id_from_init_data(x_init_data)
    mob_id = payload.mob_id

    try:
        await spend_energy(tg_id, 1)
    except ValueError:
        raise HTTPException(400, "NO_ENERGY")

    r = await get_redis()

    mob_data = await load_mob_from_db(mob_id)
    if not mob_data:
        raise HTTPException(400, "MOB_NOT_FOUND")

    hero = await load_hero(tg_id)

    mob = Mob(
        code=mob_data["code"],
        name=mob_data["name"],
        hp=int(mob_data["hp"]),
        hp_max=int(mob_data["hp"]),
        level=int(mob_data["level"]),
        phys_attack=int(mob_data["phys_attack"]),
        magic_attack=int(mob_data["magic_attack"]),
        phys_defense=int(mob_data["phys_defense"]),
        magic_defense=int(mob_data["magic_defense"]),
        atk_legacy=int(mob_data["phys_attack"]),
    )

    battle_id = int(time.time())

    battle = {
        "id": battle_id,
        "state": "active",
        "turn": 1,
        "area_key": mob_data["area"],
        "mob": _mob_to_dict(mob),
        "hero": _hero_to_dict(hero),
        "note": f"Ти зустрів: {mob.name}",
        "loot": [],
    }

    await save_battle(r, tg_id, battle)
    return BattleDTO(**battle)


# ===========================
# ATTACK
# ===========================
@router.post("/attack", response_model=BattleDTO)
async def battle_attack(
    payload: BattleActionRequest,
    x_init_data: str | None = Header(default=None, alias="X-Init-Data"),
) -> BattleDTO:
    tg_id = _tg_id_from_init_data(x_init_data)

    try:
        await spend_energy(tg_id, 1)
    except ValueError:
        raise HTTPException(400, "NO_ENERGY")

    r = await get_redis()
    battle = await load_battle(r, tg_id)
    if not battle:
        raise HTTPException(404, "BATTLE_NOT_FOUND")

    if battle["state"] != "active":
        return BattleDTO(**battle)

    hero = Hero(**battle["hero"])
    hero = await refresh_hero_energy(hero, tg_id)
    mob = Mob(**battle["mob"])

    dmg = calc_damage(hero.phys_attack, mob.phys_defense)
    mob.hp = max(0, mob.hp - dmg)
    note = f"Ти вдарив {mob.name} на {dmg} (фіз.)."

    if mob.hp <= 0:
        battle["state"] = "won"
        battle["loot"] = await _reward_for_win(tg_id, mob)
        battle["note"] = note + f" {mob.name} переможений!"
        battle["mob"] = _mob_to_dict(mob)
        battle["hero"] = _hero_to_dict(hero)
        await save_battle(r, tg_id, battle)
        await save_hero(tg_id, hero)
        return BattleDTO(**battle)

    mob_type = _mob_choose_attack_type(mob)
    if mob_type == "magic":
        mdmg = calc_damage(mob.magic_attack, hero.magic_defense)
        note += f" {mob.name} чаклує і завдає {mdmg} (магія)."
    else:
        mdmg = calc_damage(mob.phys_attack, hero.phys_defense)
        note += f" {mob.name} вдарив у відповідь на {mdmg} (фіз.)."

    hero.hp = max(0, hero.hp - mdmg)

    if hero.hp <= 0:
        hero.hp = 0
        battle["state"] = "lost"
        battle["note"] = note + " Ти впав у бою!"
        battle["mob"] = _mob_to_dict(mob)
        battle["hero"] = _hero_to_dict(hero)
        await save_battle(r, tg_id, battle)
        await save_hero(tg_id, hero)
        return BattleDTO(**battle)

    battle["turn"] += 1
    battle["note"] = note
    battle["mob"] = _mob_to_dict(mob)
    battle["hero"] = _hero_to_dict(hero)

    await save_battle(r, tg_id, battle)
    await save_hero(tg_id, hero)
    return BattleDTO(**battle)


# ===========================
# CAST
# ===========================
@router.post("/cast", response_model=BattleDTO)
async def battle_cast(
    payload: BattleActionRequest,
    x_init_data: str | None = Header(default=None, alias="X-Init-Data"),
) -> BattleDTO:
    tg_id = _tg_id_from_init_data(x_init_data)

    try:
        await spend_energy(tg_id, 1)
    except ValueError:
        raise HTTPException(400, "NO_ENERGY")

    r = await get_redis()
    battle = await load_battle(r, tg_id)
    if not battle:
        raise HTTPException(404, "BATTLE_NOT_FOUND")

    if battle["state"] != "active":
        return BattleDTO(**battle)

    hero = Hero(**battle["hero"])
    hero = await refresh_hero_energy(hero, tg_id)
    mob = Mob(**battle["mob"])

    MP_COST = 6
    if hero.mp < MP_COST:
        battle["note"] = "У тебе недостатньо мани для чарів!"
        battle["hero"] = _hero_to_dict(hero)
        await save_battle(r, tg_id, battle)
        return BattleDTO(**battle)

    hero.mp -= MP_COST

    dmg = calc_damage(max(1, hero.magic_attack), mob.magic_defense)
    mob.hp = max(0, mob.hp - dmg)
    note = f"Ти застосував чари й завдав {mob.name} {dmg} (магія)."

    if mob.hp <= 0:
        battle["state"] = "won"
        battle["loot"] = await _reward_for_win(tg_id, mob)
        battle["note"] = note + f" {mob.name} переможений!"
        battle["mob"] = _mob_to_dict(mob)
        battle["hero"] = _hero_to_dict(hero)
        await save_battle(r, tg_id, battle)
        await save_hero(tg_id, hero)
        return BattleDTO(**battle)

    mob_type = _mob_choose_attack_type(mob)
    if mob_type == "magic":
        mdmg = calc_damage(mob.magic_attack, hero.magic_defense)
        note += f" {mob.name} відповів чарами на {mdmg}."
    else:
        mdmg = calc_damage(mob.phys_attack, hero.phys_defense)
        note += f" {mob.name} відповів ударом на {mdmg}."

    hero.hp = max(0, hero.hp - mdmg)

    battle["turn"] += 1
    battle["note"] = note
    battle["mob"] = _mob_to_dict(mob)
    battle["hero"] = _hero_to_dict(hero)

    if hero.hp <= 0:
        battle["state"] = "lost"
        battle["note"] += " Ти загинув!"
        await save_battle(r, tg_id, battle)
        await save_hero(tg_id, hero)
        return BattleDTO(**battle)

    await save_battle(r, tg_id, battle)
    await save_hero(tg_id, hero)
    return BattleDTO(**battle)


# ===========================
# HEAL (UPDATED: за їжу/зілля з інвентаря, без мани)
# ===========================
@router.post("/heal", response_model=BattleDTO)
async def battle_heal(
    payload: BattleActionRequest,
    x_init_data: str | None = Header(default=None, alias="X-Init-Data"),
) -> BattleDTO:
    tg_id = _tg_id_from_init_data(x_init_data)

    try:
        await spend_energy(tg_id, 1)
    except ValueError:
        raise HTTPException(400, "NO_ENERGY")

    r = await get_redis()
    battle = await load_battle(r, tg_id)
    if not battle:
        raise HTTPException(404, "BATTLE_NOT_FOUND")

    if battle["state"] != "active":
        return BattleDTO(**battle)

    hero = Hero(**battle["hero"])
    hero = await refresh_hero_energy(hero, tg_id)
    mob = Mob(**battle["mob"])

    # ✅ режим: "hp" або "mp" або None (auto)
    mode = (payload.mode or "").strip().lower() or None

    # якщо вже фул — не марнуємо
    if hero.hp >= hero.hp_max and hero.mp >= hero.mp_max:
        battle["note"] = "Ти вже повністю відновлений."
        battle["hero"] = _hero_to_dict(hero)
        await save_battle(r, tg_id, battle)
        return BattleDTO(**battle)

    hp_missing = max(0, hero.hp_max - hero.hp)
    mp_missing = max(0, hero.mp_max - hero.mp)

    # ✅ примусово лікуємо тільки те, що натиснули
    if mode == "hp":
        mp_missing = 0
    elif mode == "mp":
        hp_missing = 0

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            used = await _pick_and_consume_heal_item(conn, tg_id, hp_missing, mp_missing)
            if not used:
                battle["note"] = "У тебе немає їжі або зілля для лікування!"
                battle["hero"] = _hero_to_dict(hero)
                await save_battle(r, tg_id, battle)
                return BattleDTO(**battle)

            item_name, hp_restore, mp_restore = used

            # ✅ якщо натиснули MP — не застосовуємо HP навіть якщо предмет дає обидва
            # ✅ якщо натиснули HP — не застосовуємо MP навіть якщо предмет дає обидва
            if mode == "hp":
                mp_restore = 0
            elif mode == "mp":
                hp_restore = 0

            if hp_restore > 0:
                hero.hp = min(hero.hp_max, hero.hp + hp_restore)
            if mp_restore > 0:
                hero.mp = min(hero.mp_max, hero.mp + mp_restore)

    note = f"Ти використав {item_name}."
    if hp_restore > 0:
        note += f" HP +{hp_restore}."
    if mp_restore > 0:
        note += f" MP +{mp_restore}."

    mob_type = _mob_choose_attack_type(mob)
    if mob_type == "magic":
        mdmg = calc_damage(mob.magic_attack, hero.magic_defense)
        note += f" {mob.name} завдав {mdmg} магією у відповідь."
    else:
        mdmg = calc_damage(mob.phys_attack, hero.phys_defense)
        note += f" {mob.name} завдав {mdmg} фіз. у відповідь."

    hero.hp = max(0, hero.hp - mdmg)

    battle["turn"] += 1
    battle["note"] = note
    battle["hero"] = _hero_to_dict(hero)
    battle["mob"] = _mob_to_dict(mob)

    if hero.hp <= 0:
        battle["state"] = "lost"
        battle["note"] += " Ти загинув!"
        await save_battle(r, tg_id, battle)
        await save_hero(tg_id, hero)
        return BattleDTO(**battle)

    await save_battle(r, tg_id, battle)
    await save_hero(tg_id, hero)
    return BattleDTO(**battle)


# ===========================
# FLEE
# ===========================
@router.post("/flee", response_model=BattleDTO)
async def battle_flee(
    payload: BattleActionRequest,
    x_init_data: str | None = Header(default=None, alias="X-Init-Data"),
) -> BattleDTO:
    tg_id = _tg_id_from_init_data(x_init_data)

    try:
        await spend_energy(tg_id, 1)
    except ValueError:
        raise HTTPException(400, "NO_ENERGY")

    r = await get_redis()
    battle = await load_battle(r, tg_id)
    if not battle:
        raise HTTPException(404, "BATTLE_NOT_FOUND")

    battle["state"] = "fled"
    battle["note"] = "Ти втік з бою."

    hero = Hero(**battle["hero"])
    hero = await refresh_hero_energy(hero, tg_id)
    battle["hero"] = _hero_to_dict(hero)

    await save_battle(r, tg_id, battle)
    return BattleDTO(**battle)