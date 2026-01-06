from __future__ import annotations

import json
from typing import List, Dict, Any

from db import get_pool

UPSERT_EQUIP_SQL = """
INSERT INTO items (
    code,
    name,
    descr,
    rarity,
    stack_max,
    weight,
    tradable,
    bind_on_pickup,
    npc_key,
    is_archived,
    base_value,
    description,
    category,
    slot,
    emoji,
    stats,
    is_global,
    stackable,
    sell_price,
    atk,
    defense,
    hp,
    mp,
    level_req,
    class_req
)
VALUES (
    $1,$2,$3,$4,
    $5,$6,$7,$8,
    $9,$10,
    $11,$12,$13,$14,
    $15,$16,$17,$18,
    $19,$20,$21,$22,$23,$24,$25
)
ON CONFLICT (code) DO UPDATE SET
  name         = EXCLUDED.name,
  descr        = EXCLUDED.descr,
  rarity       = EXCLUDED.rarity,
  stack_max    = EXCLUDED.stack_max,
  weight       = EXCLUDED.weight,
  tradable     = EXCLUDED.tradable,
  bind_on_pickup = EXCLUDED.bind_on_pickup,
  npc_key      = EXCLUDED.npc_key,
  is_archived  = EXCLUDED.is_archived,
  base_value   = EXCLUDED.base_value,
  description  = EXCLUDED.description,
  category     = EXCLUDED.category,
  slot         = EXCLUDED.slot,
  emoji        = EXCLUDED.emoji,
  stats        = EXCLUDED.stats,
  is_global    = EXCLUDED.is_global,
  stackable    = EXCLUDED.stackable,
  sell_price   = EXCLUDED.sell_price,
  atk          = EXCLUDED.atk,
  defense      = EXCLUDED.defense,
  hp           = EXCLUDED.hp,
  mp           = EXCLUDED.mp,
  level_req    = EXCLUDED.level_req,
  class_req    = EXCLUDED.class_req,
  updated_at   = now();
"""

EQUIPMENT_ITEMS: List[Dict[str, Any]] = [
    # ... ТВОЇ ITEMS БЕЗ ЗМІН ...
]

async def seed_equipment_items() -> None:
    """
    Засіває стартовий лут-екіп у таблицю items.

    FIX:
    - category = 'equip' (щоб loot.py не сприймав як trash)
    - stackable = FALSE (екіп не стекається)
    - loot_weight лишається в stats JSON
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval("SELECT to_regclass('public.items') IS NOT NULL;")
        if not exists:
            print("[seed_equipment_items] items table not found, skipping.")
            return

        for it in EQUIPMENT_ITEMS:
            stats = {
                "loot_weight": it["loot_weight"],
                "source": "seed_equipment",
            }

            await conn.execute(
                UPSERT_EQUIP_SQL,
                it["code"],
                it["name"],
                it["descr"],
                it["rarity"],
                1,                              # stack_max
                int(it.get("weight", 1)),
                True,                           # tradable
                False,                          # bind_on_pickup
                "",                             # npc_key
                False,                          # is_archived
                int(it.get("base_value", 0)),
                it["descr"],                    # description
                "equip",                        # ✅ category FIX
                it["slot"],
                None,                           # emoji
                json.dumps(stats, ensure_ascii=False),
                False,                          # is_global
                False,                          # ✅ stackable
                int(it.get("base_value", 0)),   # sell_price
                it["atk"],
                it["defense"],
                it["hp"],
                it["mp"],
                it["level_req"],
                None,                           # class_req
            )

        print(f"[seed_equipment_items] seeded/updated {len(EQUIPMENT_ITEMS)} items.")