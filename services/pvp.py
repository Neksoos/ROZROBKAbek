# services/pvp.py
from __future__ import annotations

import random
from typing import Dict, Optional, Tuple, Any

from loguru import logger

from . import pvp_rt

# ✅ DB for marking duel finished
try:
    from db import get_pool  # type: ignore
except Exception:
    get_pool = None  # type: ignore


# ---- Комбат-стати гравця (hp/mp/atk/def) -----------------------------
try:
    from .char_stats import get_full_stats_for_player  # type: ignore
except Exception:
    async def get_full_stats_for_player(_tg_id: int) -> Dict[str, int]:
        return {"hp_max": 71, "mp_max": 20, "atk": 12, "def": 6}


# ---- Бойові модифікатори/логіка ----
try:
    from .skills import (
        get_combat_mods,
        roll_with_mods,
        mitigate_damage,
        after_hit_effects,
        first_strike,
    )  # type: ignore
except Exception:
    async def get_combat_mods(_tg_id: int) -> Dict[str, float]:
        return {
            "dmg_pct": 0.0, "def_pct": 0.0, "heal_power_pct": 0.0,
            "crit_chance": 0.0, "crit_mult": 1.5,
            "stun_chance": 0.0, "dodge_chance": 0.0,
            "lifesteal_pct": 0.0, "first_strike_chance": 0.0,
            "low_hp_rage_pct": 0.0, "low_hp_threshold_pct": 0.35,
        }

    def roll_with_mods(base_dmg: int, mods: Dict[str, float], rng: Optional[random.Random] = None
                       ) -> Tuple[int, str, random.Random]:
        rng = rng or random.Random()
        return max(1, base_dmg), "", rng

    def mitigate_damage(incoming_dmg: int, defender_mods: Dict[str, float],
                        rng: Optional[random.Random] = None) -> Tuple[int, str]:
        return max(0, int(incoming_dmg)), ""

    def after_hit_effects(final_dmg: int, attacker_mods: Dict[str, float],
                          rng: Optional[random.Random] = None) -> Tuple[int, str]:
        return 0, ""

    def first_strike(mods: Dict[str, float], rng: Optional[random.Random] = None) -> bool:
        return False


MIN_HEAL_BASE = 10
HEAL_PCT = 0.30


def _is_participant(st: Dict[str, Any], uid: int) -> bool:
    return uid in (st.get("p1"), st.get("p2"))


async def _get_stats_and_mods(tg_id: int) -> Tuple[Dict[str, int], Dict[str, float]]:
    stats = await get_full_stats_for_player(tg_id)
    mods = await get_combat_mods(tg_id)
    stats = {
        "hp_max": int(stats.get("hp_max", 71)),
        "mp_max": int(stats.get("mp_max", 0)),
        "atk":    int(stats.get("atk", 12)),
        "def":    int(stats.get("def", 6)),
    }
    return stats, mods


def _base_roll_from_atk(atk: int) -> int:
    lo = max(1, atk - 2)
    hi = max(lo, atk + 3)
    return random.randint(lo, hi)


async def _mark_duel_finished_in_db(duel_id: int) -> None:
    if not get_pool:
        return
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE perun_duels SET status='finished' WHERE id=$1",
                int(duel_id),
            )
    except Exception as e:
        logger.warning(f"pvp: failed to mark duel finished in DB: {e}")


async def _record_elo_if_finished(st: Dict[str, Any]) -> None:
    """
    ✅ Запис ELO після завершення дуелі.
    """
    try:
        w = int(st.get("winner") or 0)
        l = int(st.get("loser") or 0)
        if w > 0 and l > 0 and w != l:
            from .perun_elo import record_duel_result as _rec  # type: ignore
            await _rec(w, l)
    except Exception as e:
        logger.warning(f"pvp: record elo failed: {e}")


# ============================================================
# PUBLIC API (NO TELEGRAM)
# ============================================================

async def init_duel_state(duel_id: int, p1: int, p2: int) -> Dict[str, Any]:
    """
    Ініціалізує state в Redis. Нічого не надсилає.
    """
    s1 = await get_full_stats_for_player(p1)
    s2 = await get_full_stats_for_player(p2)

    hp1 = int(s1.get("hp_max", 71))
    hp2 = int(s2.get("hp_max", 71))
    common_max = max(hp1, hp2)

    await pvp_rt.init_state(duel_id, p1, p2, common_max, common_max)

    st: Dict[str, Any] = await pvp_rt.load_state(duel_id) or {}
    st["p1"] = int(p1)
    st["p2"] = int(p2)
    st["hp1"] = hp1
    st["hp2"] = hp2
    st["max_hp"] = common_max
    st["max_hp1"] = hp1
    st["max_hp2"] = hp2
    st["state"] = "active"
    st["last"] = None
    st["winner"] = None
    st["loser"] = None

    # turn: first_strike
    try:
        m1 = await get_combat_mods(p1)
        m2 = await get_combat_mods(p2)
        rng = random.Random()
        fs1 = first_strike(m1, rng)
        fs2 = first_strike(m2, rng)
        if fs1 and not fs2:
            st["turn"] = int(p1)
        elif fs2 and not fs1:
            st["turn"] = int(p2)
        else:
            st["turn"] = int(p1)
    except Exception:
        st["turn"] = int(p1)

    await pvp_rt.save_state(duel_id, st)
    return st


async def get_state(duel_id: int) -> Optional[Dict[str, Any]]:
    return await pvp_rt.load_state(duel_id)


async def attack(actor_id: int, duel_id: int) -> Dict[str, Any]:
    if not await pvp_rt.acquire_turn_lock(duel_id):
        return {"ok": False, "error": "busy"}

    try:
        st = await pvp_rt.load_state(duel_id)
        if not st:
            return {"ok": False, "error": "state_missing"}
        if st.get("state") == "finished":
            return {"ok": False, "error": "finished"}
        if not _is_participant(st, int(actor_id)):
            return {"ok": False, "error": "not_participant"}
        if int(st.get("turn")) != int(actor_id):
            return {"ok": False, "error": "not_your_turn"}

        # who attacks / defends
        if int(actor_id) == int(st["p1"]):
            defender = int(st["p2"])
            def_hp_key = "hp2"
            att_hp = int(st["hp1"])
            att_max = int(st.get("max_hp1", st.get("max_hp", 30)))
            next_turn = int(st["p2"])
        else:
            defender = int(st["p1"])
            def_hp_key = "hp1"
            att_hp = int(st["hp2"])
            att_max = int(st.get("max_hp2", st.get("max_hp", 30)))
            next_turn = int(st["p1"])

        att_stats, att_mods = await _get_stats_and_mods(int(actor_id))
        def_stats, def_mods = await _get_stats_and_mods(int(defender))

        # low hp rage flag
        try:
            threshold = float(att_mods.get("low_hp_threshold_pct", 0.35))
            att_mods["_is_low_hp"] = (att_max > 0) and (att_hp / att_max) <= threshold
        except Exception:
            att_mods["_is_low_hp"] = False

        raw = _base_roll_from_atk(int(att_stats["atk"]))
        dmg1, note1, rng = roll_with_mods(raw, att_mods, None)
        dmg2, note2 = mitigate_damage(dmg1, def_mods, rng)

        # flat def stat
        if dmg2 > 0:
            dmg2 = max(0, int(dmg2) - max(0, int(def_stats["def"]) // 3))

        heal_ls, note3 = after_hit_effects(dmg2, att_mods, rng)

        st[def_hp_key] = max(0, int(st[def_hp_key]) - int(dmg2))
        if int(actor_id) == int(st["p1"]):
            st["hp1"] = min(att_max, int(st["hp1"]) + int(heal_ls))
        else:
            st["hp2"] = min(att_max, int(st["hp2"]) + int(heal_ls))

        notes = [x for x in (note1, note2, note3) if x]
        msg = f"hit:{dmg2}" + ((" | " + " + ".join(notes)) if notes else "")
        st["last"] = msg

        # finish?
        if int(st["hp1"]) <= 0 or int(st["hp2"]) <= 0:
            st["state"] = "finished"
            st["winner"] = int(st["p1"]) if int(st["hp2"]) <= 0 else int(st["p2"])
            st["loser"] = int(st["p2"]) if st["winner"] == int(st["p1"]) else int(st["p1"])
            await pvp_rt.save_state(duel_id, st)
            await _mark_duel_finished_in_db(duel_id)
            await _record_elo_if_finished(st)
            return {"ok": True, "event": "finished", "state": st}

        st["turn"] = next_turn
        await pvp_rt.save_state(duel_id, st)
        return {"ok": True, "event": "attack", "state": st}

    finally:
        await pvp_rt.release_turn_lock(duel_id)


async def heal(actor_id: int, duel_id: int) -> Dict[str, Any]:
    if not await pvp_rt.acquire_turn_lock(duel_id):
        return {"ok": False, "error": "busy"}

    try:
        st = await pvp_rt.load_state(duel_id)
        if not st:
            return {"ok": False, "error": "state_missing"}
        if st.get("state") == "finished":
            return {"ok": False, "error": "finished"}
        if not _is_participant(st, int(actor_id)):
            return {"ok": False, "error": "not_participant"}
        if int(st.get("turn")) != int(actor_id):
            return {"ok": False, "error": "not_your_turn"}

        if int(actor_id) == int(st["p1"]):
            max_hp = int(st.get("max_hp1", st.get("max_hp", 30)))
            heal_key = "hp1"
            next_turn = int(st["p2"])
        else:
            max_hp = int(st.get("max_hp2", st.get("max_hp", 30)))
            heal_key = "hp2"
            next_turn = int(st["p1"])

        _stats, mods = await _get_stats_and_mods(int(actor_id))
        heal_power = float(mods.get("heal_power_pct", 0.0))

        base_heal = max(MIN_HEAL_BASE, int(max_hp * HEAL_PCT))
        base_heal = int(round(base_heal * (1.0 + max(0.0, heal_power))))
        val = random.randint(max(4, base_heal - 3), base_heal + 3)

        st[heal_key] = min(max_hp, int(st[heal_key]) + val)
        st["turn"] = next_turn
        st["last"] = f"heal:{val}"

        await pvp_rt.save_state(duel_id, st)
        return {"ok": True, "event": "heal", "state": st}
    finally:
        await pvp_rt.release_turn_lock(duel_id)


async def surrender(actor_id: int, duel_id: int) -> Dict[str, Any]:
    st = await pvp_rt.load_state(duel_id)
    if not st:
        return {"ok": False, "error": "state_missing"}
    if st.get("state") == "finished":
        return {"ok": False, "error": "finished"}
    if not _is_participant(st, int(actor_id)):
        return {"ok": False, "error": "not_participant"}

    winner = int(st["p2"]) if int(actor_id) == int(st["p1"]) else int(st["p1"])
    st["state"] = "finished"
    st["winner"] = winner
    st["loser"] = int(actor_id)
    st["last"] = "surrender"

    await pvp_rt.save_state(duel_id, st)
    await _mark_duel_finished_in_db(duel_id)
    await _record_elo_if_finished(st)
    return {"ok": True, "event": "finished", "state": st}


__all__ = ["init_duel_state", "get_state", "attack", "heal", "surrender"]