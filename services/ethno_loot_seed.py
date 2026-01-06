from __future__ import annotations

"""
Авто-сідер етно-луту.

Викликається із services.seed.seed_all_content()
і сам:
  - генерує етно-лут (мусор, інгредієнти, трофеї, консум)
  - upsert-ить усе в таблицю items
"""

from typing import Dict, List

from loguru import logger

try:
    from ..database import get_pool  # type: ignore
except Exception:
    get_pool = None  # type: ignore

from .loot_generator import get_all_ethno_items_for_db

# скільки штук згенерувати по категоріях
TARGET_COUNTS: Dict[str, int] = {
    "trash": 40,
    "herb": 50,
    "ore": 30,
    "gem": 15,
    "mat": 25,
    "trophy": 15,
    "consum": 25,
}


UPSERT_ETHNO_ITEM = """
INSERT INTO items (
    code,
    name,
    category,
    rarity,
    descr,
    stack_max,
    weight,
    tradable,
    bind_on_pickup,
    npc_key,
    is_archived,
    base_value
)
VALUES (
    $1,$2,$3,$4,$5,
    $6,$7,$8,$9,$10,
    $11,$12
)
ON CONFLICT (code) DO UPDATE SET
  name           = EXCLUDED.name,
  category       = EXCLUDED.category,
  rarity         = EXCLUDED.rarity,
  descr          = EXCLUDED.descr,
  stack_max      = EXCLUDED.stack_max,
  weight         = EXCLUDED.weight,
  tradable       = EXCLUDED.tradable,
  bind_on_pickup = EXCLUDED.bind_on_pickup,
  npc_key        = EXCLUDED.npc_key,
  is_archived    = EXCLUDED.is_archived,
  base_value     = EXCLUDED.base_value,
  updated_at     = now();
"""


async def ensure_ethno_loot() -> None:
    """
    Генерує етно-лут і заливає в items.

    Нічого руками запускати не треба – просто викликаємо
    з seed_all_content().
    """
    if not get_pool:
        logger.info("ethno_loot: no pool, skipping.")
        return

    pool = await get_pool()
    async with pool.acquire() as conn:
        # перевіряємо, що таблиця items існує
        exists = await conn.fetchval("SELECT to_regclass('public.items') IS NOT NULL;")
        if not exists:
            logger.warning("⚠️ Таблиці 'items' немає — пропускаю ensure_ethno_loot()")
            return

        # генеруємо всі предмети (статичні + автогенерація)
        items: List[dict] = get_all_ethno_items_for_db(
            TARGET_COUNTS,
            min_tier=1,
            max_tier=5,
            seed=42,          # стабільний результат, можна змінити
            include_static=True,
        )

        logger.info(f"ethno_loot: generated {len(items)} items, upserting into DB...")

        async with conn.transaction():
            for it in items:
                await conn.execute(
                    UPSERT_ETHNO_ITEM,
                    it["code"],
                    it["name"],
                    it["category"],
                    it["rarity"],
                    it["descr"],
                    int(it.get("stack_max", 1)),
                    float(it.get("weight", 0)),
                    bool(it.get("tradable", True)),
                    bool(it.get("bind_on_pickup", False)),
                    it.get("npc_key"),
                    bool(it.get("is_archived", False)),
                    int(it.get("base_value", 1)),
                )

    logger.success("✅ ethno_loot: items table populated/updated.")