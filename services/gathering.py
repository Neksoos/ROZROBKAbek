from __future__ import annotations

import random
from dataclasses import dataclass
from typing import List, Dict, Any

from loguru import logger

from db import get_pool


@dataclass
class GatherDrop:
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
    """
    Мінімальна утилітка: дістати рівень героя.
    Якщо немає — вважаємо рівень 1.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COALESCE(level, 1) AS level FROM players WHERE tg_id = $1",
            tg_id,
        )
    if not row:
        return 1
    return int(row["level"])


async def roll_gathering_loot(
    tg_id: int,
    area_key: str,
    source_type: str,
) -> List[GatherDrop]:
    """
    Основна функція: кинути дроп із gathering_loot для конкретного гравця.

    - tg_id          → беремо рівень гравця (щоб фільтрувати по level_min)
    - area_key       → 'netrytsia', 'peredmistia', ...
    - source_type    → 'herb' / 'ore' / 'stone' (як у craft_materials.source_type)

    Повертає список випавших матеріалів із кількістю.
    """
    level = await _get_player_level(tg_id)

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                gl.material_id,
                gl.drop_chance,
                gl.min_qty,
                gl.max_qty,
                cm.code,
                cm.name,
                cm.rarity
            FROM gathering_loot gl
            JOIN craft_materials cm
              ON cm.id = gl.material_id
            WHERE gl.area_key   = $1
              AND gl.source_type = $2
              AND gl.level_min <= $3
            """,
            area_key,
            source_type,
            level,
        )

    drops: List[GatherDrop] = []

    for row in rows:
        chance = int(row["drop_chance"] or 0)
        if chance <= 0:
            continue

        roll = random.randint(1, 100)
        if roll > chance:
            # не пощастило
            continue

        min_qty = int(row["min_qty"] or 1)
        max_qty = int(row["max_qty"] or min_qty)

        if max_qty < min_qty:
            max_qty = min_qty

        qty = random.randint(min_qty, max_qty)

        drops.append(
            GatherDrop(
                material_id=int(row["material_id"]),
                code=row["code"],
                name=row["name"],
                rarity=row["rarity"],
                qty=qty,
            )
        )

    logger.debug(
        f"gathering: tg_id={tg_id} area={area_key} source={source_type} → {len(drops)} drops"
    )

    return drops


async def roll_gathering_loot_as_dicts(
    tg_id: int,
    area_key: str,
    source_type: str,
) -> List[Dict[str, Any]]:
    """
    Обгортка, щоб фронту / іншим сервісам було зручно — повертає list[dict].
    """
    drops = await roll_gathering_loot(tg_id, area_key, source_type)
    return [d.as_dict() for d in drops]