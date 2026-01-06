# services/skills.py
from __future__ import annotations
from typing import Dict, Tuple, Optional, List
import random
from loguru import logger

# --- DB ---------------------------------------------------------------
try:
    from database import get_pool  # type: ignore
except Exception:
    get_pool = None  # type: ignore


# ------------------------- Public API ---------------------------------
# Використання з battle/pvp:
#   mods = await get_combat_mods(tg_id)
#   dmg, note1, rng = roll_with_mods(base_dmg, mods, rng)
#   lifesteal_hp, note2 = after_hit_effects(dmg, mods, rng)
#   if first_strike(mods, rng): ...  # почати хід першим

async def get_combat_mods(tg_id: int) -> Dict[str, float]:
    """
    Збирає бойові модифікатори гравця з:
      - пасивок раси (races.passives)
      - пасивок класу (classes.passives)
      - (опц.) таблиці player_skills (якщо існує)
    Повертає словник з безпечними дефолтами.
    Ключі (всі значення у дробах, не у відсотках):
      dmg_pct, def_pct, heal_power_pct,
      crit_chance, crit_mult,
      stun_chance, dodge_chance,
      lifesteal_pct,
      first_strike_chance, low_hp_rage_pct, low_hp_threshold_pct,
    """
    base = _empty_mods()

    # якщо нема БД — просто вертаємо нулі
    if not get_pool:
        return base

    try:
        race_key, class_key = await _load_player_race_class(tg_id)
        # расові пасивки
        _merge_pct(base, await _load_passives("races", race_key))
        # класові пасивки
        _merge_pct(base, await _load_passives("classes", class_key))
        # активні скіли гравця (якщо таблиця є)
        _merge_pct(base, await _load_player_skills(tg_id))
    except Exception as e:
        logger.warning(f"skills: get_combat_mods fallback: {e}")

    # саніти
    base["crit_mult"] = max(1.0, float(base.get("crit_mult", 1.5)))
    base["low_hp_threshold_pct"] = min(max(base.get("low_hp_threshold_pct", 0.35), 0.05), 0.5)
    return base


def roll_with_mods(base_dmg: int, mods: Dict[str, float], rng: Optional[random.Random] = None
                   ) -> Tuple[int, str, random.Random]:
    """
    Застосовує моди до урону атакуючого:
      - dmg_pct (загальний буст)
      - crit_chance/crit_mult
      - low_hp_rage_pct якщо HP < threshold (цю перевірку має робити виклик, передавши флаг через mods['_is_low_hp'])
    Повертає (фінальний_урон, текст-нотатка, rng)
    """
    if rng is None:
        rng = random.Random()

    note_parts: List[str] = []

    dmg = int(round(base_dmg * (1.0 + float(mods.get("dmg_pct", 0.0)))))

    # "режим люті" при низькому HP (перед тим у бою вистави mods['_is_low_hp']=True)
    if mods.get("_is_low_hp", False):
        rage = float(mods.get("low_hp_rage_pct", 0.0))
        if rage > 0:
            dmg = int(round(dmg * (1.0 + rage)))
            note_parts.append("🩸 Лють")

    # крит
    crit = False
    crit_ch = max(0.0, min(1.0, float(mods.get("crit_chance", 0.0))))
    if rng.random() < crit_ch:
        crit = True
        cm = max(1.0, float(mods.get("crit_mult", 1.5)))
        dmg = int(round(dmg * cm))
        note_parts.append("💥 Крит")

    note = (" + ".join(note_parts)) if note_parts else ""
    return max(1, dmg), note, rng


def mitigate_damage(incoming_dmg: int, defender_mods: Dict[str, float],
                    rng: Optional[random.Random] = None) -> Tuple[int, str]:
    """
    Застосовує захисні моди цілі:
      - def_pct (загальне зменшення)
      - dodge_chance (повне уникнення)
      - thorns_pct (опц., шкода у відповідь — бою варто окремо списати)
    Повертає (фінальний_урон, нотатка)
    """
    rng = rng or random.Random()
    # ухилення
    if rng.random() < max(0.0, min(1.0, float(defender_mods.get("dodge_chance", 0.0)))):
        return 0, "🌀 Ухилення"

    dmg = int(round(incoming_dmg * (1.0 - max(0.0, min(0.9, float(defender_mods.get("def_pct", 0.0)))))))
    return max(0, dmg), ""


def after_hit_effects(final_dmg: int, attacker_mods: Dict[str, float],
                      rng: Optional[random.Random] = None) -> Tuple[int, str]:
    """
    Після завдання шкоди:
      - lifesteal_pct -> скільки HP повернути атакеру
    Повертає (heal_amount, note)
    """
    ls = max(0.0, float(attacker_mods.get("lifesteal_pct", 0.0)))
    heal = int(round(final_dmg * ls))
    return heal, ("🧛‍♂️ Вампіризм +" + str(heal)) if heal > 0 else ""


def first_strike(mods: Dict[str, float], rng: Optional[random.Random] = None) -> bool:
    """
    Чи починає бій першим.
    """
    rng = rng or random.Random()
    ch = max(0.0, min(1.0, float(mods.get("first_strike_chance", 0.0))))
    return rng.random() < ch


# ------------------------- Loaders ------------------------------------

async def _load_player_race_class(tg_id: int) -> Tuple[Optional[str], Optional[str]]:
    """
    Лише race_key та class_key — без полів hp/hp_max, щоб не падати
    на схемах, де їх ще немає.
    """
    if not get_pool:
        return (None, None)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT race_key, class_key FROM players WHERE tg_id=$1",
            tg_id,
        )
    if not row:
        return (None, None)
    return (row["race_key"], row["class_key"])


async def _load_passives(table: str, key: Optional[str]) -> Dict[str, float]:
    """
    Зчитує масив passives з таблиці races/classes і агрегує числові поля:
      dmg_pct, def_pct, heal_power_pct, crit_chance, crit_mult,
      stun_chance, dodge_chance, lifesteal_pct, first_strike_chance,
      low_hp_rage_pct, low_hp_threshold_pct
    Нечислові пасивки з key/desc ігноруються без помилок.
    """
    if not get_pool or not key:
        return {}
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(f"SELECT passives FROM {table} WHERE key=$1", key)
    if not row or not row["passives"]:
        return {}

    total = _empty_mods()
    for p in row["passives"]:
        if not isinstance(p, dict):
            continue
        for k in total.keys():
            # тільки числові значення
            v = p.get(k)
            if isinstance(v, (int, float)):
                total[k] += float(v)

        # підтримка альтернативних полів
        if isinstance(p.get("crit"), (int, float)):
            total["crit_chance"] += float(p["crit"])
        if isinstance(p.get("dodge"), (int, float)):
            total["dodge_chance"] += float(p["dodge"])
    return total


async def _load_player_skills(tg_id: int) -> Dict[str, float]:
    """
    Необов'язково. Якщо є таблиця player_skills(passives jsonb), агрегуємо так само як вище.
    Якщо таблиці немає — повертаємо порожні моди.
    """
    if not get_pool:
        return {}
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            # перевіряємо наявність таблиці
            has = await conn.fetchval("""
                SELECT EXISTS (
                  SELECT 1 FROM information_schema.tables
                  WHERE table_name='player_skills'
                )
            """)
            if not has:
                return {}
            rows = await conn.fetch("SELECT passives FROM player_skills WHERE tg_id=$1", tg_id)
        total = _empty_mods()
        for r in rows or []:
            for p in (r["passives"] or []):
                if isinstance(p, dict):
                    for k in total.keys():
                        v = p.get(k)
                        if isinstance(v, (int, float)):
                            total[k] += float(v)
        return total
    except Exception as e:
        logger.info(f"skills: player_skills not used ({e})")
        return {}


# ------------------------- Utils --------------------------------------

def _empty_mods() -> Dict[str, float]:
    return {
        "dmg_pct": 0.0,           # + до завданої шкоди
        "def_pct": 0.0,           # - до вхідної шкоди (0..0.9)
        "heal_power_pct": 0.0,    # + до сили лікування
        "crit_chance": 0.0,       # 0..1
        "crit_mult": 1.5,         # >=1.0
        "stun_chance": 0.0,       # 0..1 (для майбутніх станів)
        "dodge_chance": 0.0,      # 0..1
        "lifesteal_pct": 0.0,     # частка від завданого урону в HP
        "first_strike_chance": 0.0,
        "low_hp_rage_pct": 0.0,   # дод. мультиплікатор dmg при низькому HP
        "low_hp_threshold_pct": 0.35,  # поріг низького HP
    }


def _merge_pct(dst: Dict[str, float], src: Dict[str, float]) -> None:
    for k, v in (src or {}).items():
        try:
            dst[k] = float(dst.get(k, 0.0)) + float(v or 0.0)
        except Exception:
            pass