# services/battle/rewards.py
from __future__ import annotations

from typing import List

from loguru import logger

from db import get_pool

from services.progress import grant_xp_for_win  # type: ignore
from services.fort_levels import add_fort_xp_for_kill  # type: ignore
from services.rewards import distribute_drops
from services.loot import get_loot_for_mob  # type: ignore

from services.battle.models import Mob

try:
    from services.night_watch import roll_medal, report_kill  # type: ignore
except Exception:

    def roll_medal(_lvl: int, _rng=None) -> bool:  # type: ignore
        return False

    async def report_kill(_tg_id: int, _lvl: int, _hp: int, _medal: bool) -> None:  # type: ignore
        return None


async def reward_items_new(tg_id: int, mob: Mob) -> List[str]:
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


async def reward_for_win(tg_id: int, mob: Mob) -> List[str]:
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
        loot.extend(await reward_items_new(tg_id, mob))
    except Exception:
        logger.exception("battle: reward_items_new FAILED tg_id={} mob={}", tg_id, mob)

    if not loot:
        loot.append("Трофей ×1")

    return loot