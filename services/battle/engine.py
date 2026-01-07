# services/battle/engine.py
from __future__ import annotations

import random

from services.battle.models import Hero, Mob


def calc_damage(atk: int, defense: int) -> int:
    base = max(1, atk - max(0, defense) // 2)
    spread = max(1, base // 4)
    return random.randint(max(1, base - spread), base + spread)


def mob_choose_attack_type(mob: Mob) -> str:
    if mob.magic_attack <= 0:
        return "phys"
    return "magic" if random.random() < 0.25 else "phys"


def mob_to_dict(m: Mob) -> dict:
    return m.dict(by_alias=True)


def hero_to_dict(h: Hero) -> dict:
    return h.dict(by_alias=True)