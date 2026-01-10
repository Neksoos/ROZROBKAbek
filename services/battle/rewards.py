# services/battle/rewards.py
from __future__ import annotations

from typing import List, Optional

from loguru import logger

from db import get_pool

from services.progress import grant_xp_for_win  # type: ignore
from services.fort_levels import add_fort_xp_for_kill  # type: ignore
from services.rewards import distribute_drops
from services.loot import get_loot_for_mob  # type: ignore

from services.battle.models import Mob

from services.achievements.metrics import inc_metric, try_mark_event_once  # ✅ metrics + idempotency

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


def _normalize_area_for_metric(area: Optional[str]) -> str:
    a = (area or "unknown").strip().lower()
    if not a:
        a = "unknown"
    return a


async def _apply_win_metrics(tg_id: int, mob: Mob) -> None:
    """
    Лічильники під ачівки. Тут тільки "метрики", без нагород.
    """
    # загальні
    await inc_metric(tg_id, "battles_total", 1)
    await inc_metric(tg_id, "battles_won", 1)
    await inc_metric(tg_id, "kills_total", 1)

    # по мобу
    await inc_metric(tg_id, f"kills_{mob.code}", 1)

    # по рівню моба
    await inc_metric(tg_id, "kills_lvl_sum", int(mob.level or 1))
    await inc_metric(tg_id, f"kills_lvl_{int(mob.level or 1):02d}", 1)

    # по зоні (якщо mob має поле area)
    area = _normalize_area_for_metric(getattr(mob, "area", None))
    await inc_metric(tg_id, f"wins_area_{area}", 1)


async def reward_for_win(tg_id: int, mob: Mob, battle_id: Optional[int] = None) -> List[str]:
    """
    Видає нагороди за перемогу.
    ✅ Метрики для ачівок.
    ✅ Ідемпотентність якщо передано battle_id (РЕКОМЕНДОВАНО).
    """
    loot: List[str] = []

    # ----------------------------
    # ✅ ІДЕМПОТЕНТНІСТЬ
    # ----------------------------
    if battle_id is not None:
        event_key = f"battle_win_reward:{int(battle_id)}"
        first = await try_mark_event_once(tg_id, event_key)
        if not first:
            return ["Нагорода вже видана."]

    # ----------------------------
    # ✅ МЕТРИКИ (тільки інкременти)
    # ----------------------------
    try:
        await _apply_win_metrics(tg_id, mob)
    except Exception:
        logger.exception("battle: metrics apply FAILED tg_id={} mob={}", tg_id, mob)

    # ----------------------------
    # XP
    # ----------------------------
    try:
        xp_gain, _, _, _ = await grant_xp_for_win(tg_id, mob.code)
        if xp_gain > 0:
            loot.append(f"XP +{xp_gain}")
            try:
                await inc_metric(tg_id, "xp_from_battles", int(xp_gain))
            except Exception:
                logger.exception("battle: metric xp_from_battles FAILED tg_id={} xp={}", tg_id, xp_gain)
    except Exception:
        logger.exception("battle: grant_xp_for_win FAILED tg_id={} mob={}", tg_id, mob)

    # ----------------------------
    # Fort XP
    # ----------------------------
    try:
        g_gain, level_up, _ = await add_fort_xp_for_kill(tg_id, mob.code)
        if g_gain > 0:
            loot.append(f"Застава XP +{g_gain}")
            if level_up:
                loot.append(f"Застава отримала рівень {level_up}!")
            try:
                await inc_metric(tg_id, "fort_xp_from_kills", int(g_gain))
            except Exception:
                logger.exception("battle: metric fort_xp_from_kills FAILED tg_id={} gain={}", tg_id, g_gain)
    except Exception:
        logger.exception("battle: add_fort_xp_for_kill FAILED tg_id={} mob={}", tg_id, mob)

    # ----------------------------
    # Coins
    # ----------------------------
    coins = max(1, 3 + int(mob.level or 1) * 2)
    try:
        pool = await get_pool()
        await pool.execute(
            "UPDATE players SET chervontsi = chervontsi + $2 WHERE tg_id = $1",
            tg_id,
            coins,
        )
        loot.append(f"Червонці +{coins}")
        try:
            await inc_metric(tg_id, "coins_from_battles", int(coins))
        except Exception:
            logger.exception("battle: metric coins_from_battles FAILED tg_id={} coins={}", tg_id, coins)
    except Exception:
        logger.exception("battle: update chervontsi FAILED tg_id={} coins={}", tg_id, coins)

    # ----------------------------
    # Night watch medal
    # ----------------------------
    try:
        medal = roll_medal(int(mob.level or 1))
        await report_kill(tg_id, int(mob.level or 1), int(mob.hp_max or mob.hp or 1), medal)
        if medal:
            loot.append("🏅 Медаль Сторожа")
            try:
                await inc_metric(tg_id, "nightwatch_medals", 1)
            except Exception:
                logger.exception("battle: metric nightwatch_medals FAILED tg_id={}", tg_id)
    except Exception:
        logger.exception("battle: night_watch report FAILED tg_id={} mob={}", tg_id, mob)

    # ----------------------------
    # Items loot
    # ----------------------------
    try:
        drop_names = await reward_items_new(tg_id, mob)
        if drop_names:
            loot.extend(drop_names)
            try:
                await inc_metric(tg_id, "loot_drops_total", int(len(drop_names)))
            except Exception:
                logger.exception("battle: metric loot_drops_total FAILED tg_id={} n={}", tg_id, len(drop_names))
    except Exception:
        logger.exception("battle: reward_items_new FAILED tg_id={} mob={}", tg_id, mob)

    if not loot:
        loot.append("Трофей ×1")

    return loot