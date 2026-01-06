# services/gathering_loot.py
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import List, Optional

from loguru import logger

from db import get_pool

# Legacy area keys in DB
LEGACY_AREA_MAP: dict[str, str] = {
    "slums": "netrytsia",
    "suburbs": "peredmistia",
    "peredmistya": "peredmistia",
}


def _normalize_area_key_for_db(area_key: str) -> str:
    if not area_key:
        return area_key
    s = str(area_key).strip()
    return LEGACY_AREA_MAP.get(s, s)


# ─────────────────────────────────────────────
# Risk config
# ─────────────────────────────────────────────

# Max distinct items per run (not qty)
RISK_MAX_DISTINCT: dict[str, int] = {
    "low": 1,
    "medium": 2,
    "high": 3,
    "extreme": 5,  # real range handled in _max_distinct_for_risk
}

# Chance of "complication" that cuts loot
RISK_COMPLICATION_CHANCE: dict[str, int] = {
    "low": 5,       # 5%
    "medium": 12,   # 12%
    "high": 22,     # 22%
    "extreme": 35,  # 35%
}


def _normalize_risk(risk: Optional[str]) -> str:
    if not risk:
        return "medium"
    r = str(risk).strip().lower()
    if r in ("safe", "careful"):
        return "low"
    if r in ("normal", "usual", "standard"):
        return "medium"
    if r in ("risky", "danger"):
        return "high"
    if r not in ("low", "medium", "high", "extreme"):
        return "medium"
    return r


def _max_distinct_for_risk(risk: str) -> int:
    if risk == "extreme":
        return random.randint(4, 5)
    return int(RISK_MAX_DISTINCT.get(risk, 2))


# ─────────────────────────────────────────────
# DTO
# ─────────────────────────────────────────────

@dataclass
class GatherDrop:
    # залишаємо назву material_id для сумісності з існуючим DTO/фронтом,
    # але фактично тут items.id
    material_id: int
    code: str
    name: str
    rarity: str | None
    qty: int

    def as_dict(self) -> dict:
        return {
            "material_id": self.material_id,
            "code": self.code,
            "name": self.name,
            "rarity": self.rarity,
            "qty": self.qty,
        }


async def _get_player_level(tg_id: int) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COALESCE(level, 1) AS level FROM players WHERE tg_id = $1",
            tg_id,
        )
    return int(row["level"]) if row else 1


def _rarity_weight(rarity: Optional[str], risk: str) -> float:
    """
    High/extreme трохи частіше беруть рідкісні, але без “дощу легендарок”.
    """
    r = (rarity or "").strip().lower()

    base = 1.0
    if r in ("звичайний", "common"):
        base = 1.0
    elif r in ("добротний", "uncommon"):
        base = 1.2
    elif r in ("обереговий", "rare"):
        base = 1.35
    elif r in ("рідкісний", "epic"):
        base = 1.5
    elif r in ("вибраний", "legendary"):
        base = 1.65
    elif r in ("божественний", "mythic", "divine"):
        base = 1.8

    if risk == "low":
        return base * 0.95
    if risk == "high":
        return base * 1.08
    if risk == "extreme":
        return base * 1.15
    return base


def _pick_distinct_drops(candidates: List[GatherDrop], risk: str) -> List[GatherDrop]:
    """
    Беремо обмежену кількість РІЗНИХ ресурсів (1/2/3/4-5),
    з вагою по rarity.
    """
    if not candidates:
        return []

    max_n = _max_distinct_for_risk(risk)
    if len(candidates) <= max_n:
        random.shuffle(candidates)
        return candidates

    weights = [_rarity_weight(c.rarity, risk) for c in candidates]

    chosen: List[GatherDrop] = []
    pool = list(candidates)
    pool_w = list(weights)

    # без повторів
    for _ in range(max_n):
        if not pool:
            break
        idx = random.choices(range(len(pool)), weights=pool_w, k=1)[0]
        chosen.append(pool.pop(idx))
        pool_w.pop(idx)

    return chosen


def _apply_complication(risk: str, drops: List[GatherDrop]) -> List[GatherDrop]:
    if not drops:
        return drops

    roll = random.randint(1, 100)
    chance = int(RISK_COMPLICATION_CHANCE.get(risk, 12))
    if roll > chance:
        return drops

    # ✅ complication happened
    if risk in ("low", "medium"):
        if len(drops) >= 2:
            return drops[:-1]
        if len(drops) == 1 and drops[0].qty > 1:
            drops[0].qty = max(1, drops[0].qty - 1)
        return drops

    # high / extreme
    mode = random.choice(["cut_half", "drop_to_one", "reduce_qty"])
    if mode == "cut_half":
        keep = max(1, (len(drops) + 1) // 2)
        return drops[:keep]
    if mode == "drop_to_one":
        return drops[:1]

    # reduce_qty
    for d in drops:
        if d.qty > 1:
            d.qty = max(1, d.qty - random.randint(1, min(2, d.qty - 1)))
    return drops


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

async def roll_gathering_loot(
    tg_id: int,
    area_key: str,
    source_type: str,  # "ore" | "herb" | "stone"
    risk: Optional[str] = None,
) -> List[GatherDrop]:
    """
    ✅ Gathering дропає RAW ресурси з таблиці items.
    Очікування: gathering_loot.material_id -> items.id (RAW предмети: руда/камінь/свіжа трава).

    НЕ використовує craft_materials.
    """
    risk_n = _normalize_risk(risk)
    level = await _get_player_level(tg_id)
    area_key_db = _normalize_area_key_for_db(area_key)

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                i.id              AS item_id,
                i.code            AS code,
                i.name            AS name,
                i.rarity          AS rarity,
                gl.drop_chance,
                gl.min_qty,
                gl.max_qty
            FROM gathering_loot gl
            JOIN items i ON i.id = gl.material_id
            WHERE gl.area_key = $1
              AND gl.source_type = $2
              AND gl.level_min <= $3
            """,
            area_key_db,
            source_type,
            level,
        )

    candidates: List[GatherDrop] = []

    for r in rows:
        chance = int(r["drop_chance"] or 0)
        if chance <= 0:
            continue

        if random.randint(1, 100) > chance:
            continue

        min_qty = int(r["min_qty"] or 1)
        max_qty = int(r["max_qty"] or min_qty)
        if max_qty < min_qty:
            max_qty = min_qty

        candidates.append(
            GatherDrop(
                material_id=int(r["item_id"]),
                code=str(r["code"]),
                name=str(r["name"]),
                rarity=r["rarity"],
                qty=random.randint(min_qty, max_qty),
            )
        )

    drops = _pick_distinct_drops(candidates, risk_n)
    drops = _apply_complication(risk_n, drops)

    logger.info(
        "gather loot tg=%s area=%s(area_db=%s) source=%s risk=%s lvl=%s rows=%s cand=%s drops=%s",
        tg_id,
        area_key,
        area_key_db,
        source_type,
        risk_n,
        level,
        len(rows),
        len(candidates),
        len(drops),
    )
    return drops