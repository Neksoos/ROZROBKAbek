# routers/battle.py
from __future__ import annotations

import time
from typing import List, Optional

from fastapi import APIRouter, Header, HTTPException
from loguru import logger

from db import get_pool
from routers.redis_manager import get_redis

from services.energy import spend_energy
from services.battle.deps import tg_id_from_init_data
from services.battle.models import (
    BattleActionRequest,
    BattleDTO,
    BattleStartRequest,
    Hero,
    Mob,
)
from services.battle.state import load_battle, save_battle
from services.battle.engine import (
    calc_damage,
    hero_to_dict,
    mob_choose_attack_type,
    mob_to_dict,
)
from services.battle.repo import (
    load_hero,
    load_mob_from_db,
    pick_and_consume_heal_item,
    refresh_hero_energy,
    save_hero,
)
from services.battle.rewards import reward_for_win

router = APIRouter(prefix="/battle", tags=["battle"])


# ===========================
# BATTLE START
# ===========================
@router.post("/start", response_model=BattleDTO)
async def battle_start(
    payload: BattleStartRequest,
    x_init_data: str | None = Header(default=None, alias="X-Init-Data"),
) -> BattleDTO:
    tg_id = tg_id_from_init_data(x_init_data)
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
        "mob": mob_to_dict(mob),
        "hero": hero_to_dict(hero),
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
    tg_id = tg_id_from_init_data(x_init_data)

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
        battle["loot"] = await reward_for_win(tg_id, mob)
        battle["note"] = note + f" {mob.name} переможений!"
        battle["mob"] = mob_to_dict(mob)
        battle["hero"] = hero_to_dict(hero)
        await save_battle(r, tg_id, battle)
        await save_hero(tg_id, hero)
        return BattleDTO(**battle)

    mob_type = mob_choose_attack_type(mob)
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
        battle["mob"] = mob_to_dict(mob)
        battle["hero"] = hero_to_dict(hero)
        await save_battle(r, tg_id, battle)
        await save_hero(tg_id, hero)
        return BattleDTO(**battle)

    battle["turn"] += 1
    battle["note"] = note
    battle["mob"] = mob_to_dict(mob)
    battle["hero"] = hero_to_dict(hero)

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
    tg_id = tg_id_from_init_data(x_init_data)

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
        battle["hero"] = hero_to_dict(hero)
        await save_battle(r, tg_id, battle)
        return BattleDTO(**battle)

    hero.mp -= MP_COST

    dmg = calc_damage(max(1, hero.magic_attack), mob.magic_defense)
    mob.hp = max(0, mob.hp - dmg)
    note = f"Ти застосував чари й завдав {mob.name} {dmg} (магія)."

    if mob.hp <= 0:
        battle["state"] = "won"
        battle["loot"] = await reward_for_win(tg_id, mob)
        battle["note"] = note + f" {mob.name} переможений!"
        battle["mob"] = mob_to_dict(mob)
        battle["hero"] = hero_to_dict(hero)
        await save_battle(r, tg_id, battle)
        await save_hero(tg_id, hero)
        return BattleDTO(**battle)

    mob_type = mob_choose_attack_type(mob)
    if mob_type == "magic":
        mdmg = calc_damage(mob.magic_attack, hero.magic_defense)
        note += f" {mob.name} відповів чарами на {mdmg}."
    else:
        mdmg = calc_damage(mob.phys_attack, hero.phys_defense)
        note += f" {mob.name} відповів ударом на {mdmg}."

    hero.hp = max(0, hero.hp - mdmg)

    battle["turn"] += 1
    battle["note"] = note
    battle["mob"] = mob_to_dict(mob)
    battle["hero"] = hero_to_dict(hero)

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
    tg_id = tg_id_from_init_data(x_init_data)

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

    mode = (payload.mode or "").strip().lower() or None

    if hero.hp >= hero.hp_max and hero.mp >= hero.mp_max:
        battle["note"] = "Ти вже повністю відновлений."
        battle["hero"] = hero_to_dict(hero)
        await save_battle(r, tg_id, battle)
        return BattleDTO(**battle)

    hp_missing = max(0, hero.hp_max - hero.hp)
    mp_missing = max(0, hero.mp_max - hero.mp)

    if mode == "hp":
        mp_missing = 0
    elif mode == "mp":
        hp_missing = 0

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            used = await pick_and_consume_heal_item(conn, tg_id, hp_missing, mp_missing)
            if not used:
                battle["note"] = "У тебе немає їжі або зілля для лікування!"
                battle["hero"] = hero_to_dict(hero)
                await save_battle(r, tg_id, battle)
                return BattleDTO(**battle)

            item_name, hp_restore, mp_restore = used

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

    mob_type = mob_choose_attack_type(mob)
    if mob_type == "magic":
        mdmg = calc_damage(mob.magic_attack, hero.magic_defense)
        note += f" {mob.name} завдав {mdmg} магією у відповідь."
    else:
        mdmg = calc_damage(mob.phys_attack, hero.phys_defense)
        note += f" {mob.name} завдав {mdmg} фіз. у відповідь."

    hero.hp = max(0, hero.hp - mdmg)

    battle["turn"] += 1
    battle["note"] = note
    battle["hero"] = hero_to_dict(hero)
    battle["mob"] = mob_to_dict(mob)

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
    tg_id = tg_id_from_init_data(x_init_data)

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
    battle["hero"] = hero_to_dict(hero)

    await save_battle(r, tg_id, battle)
    return BattleDTO(**battle)