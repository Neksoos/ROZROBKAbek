# services/gathering_loot.py
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import asyncpg

from db import get_pool


# ──────────────────────────────────────────────
# DTO
# ──────────────────────────────────────────────

@dataclass
class ItemDrop:
    code: str
    name: str
    qty: int = 1
    rarity: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "qty": self.qty,
            "rarity": self.rarity,
        }


# ──────────────────────────────────────────────
# profession.code -> source bucket
# ──────────────────────────────────────────────

PROFESSION_TO_SOURCE: Dict[str, str] = {
    "herbalist": "herb",
    "miner": "ore",
    "stonemason": "ks",
    # aliases
    "stone": "ks",
    "ks": "ks",
    "herb": "herb",
    "ore": "ore",
}

# risk -> tier weights
RISK_WEIGHTS = {
    "low": {"common": 80, "uncommon": 18, "rare": 2, "epic": 0},
    "medium": {"common": 70, "uncommon": 23, "rare": 6, "epic": 1},
    "high": {"common": 55, "uncommon": 28, "rare": 14, "epic": 3},
}


def _pick_tier(risk: str) -> str:
    w = RISK_WEIGHTS.get(risk, RISK_WEIGHTS["medium"])
    roll = random.randint(1, sum(w.values()))
    cur = 0
    for k, v in w.items():
        cur += v
        if roll <= cur:
            return k
    return "common"


def _normalize_source(v: Optional[str]) -> Optional[str]:
    if not v:
        return None
    s = v.strip().lower()
    return PROFESSION_TO_SOURCE.get(s, s)


def _categories_for_source(source: str, tier: Optional[str]) -> List[str]:
    """
    ПІД ТВОЮ БД:
    - для каменя ВСЕ в items.category = "ks" (БЕЗ ks_common/ks_rare)
    - для herb/ore допускаємо base або base_tier
    """
    st = (source or "").strip().lower()

    if st == "ks":
        return ["ks"]

    if not tier:
        return [st]

    t = tier.strip().lower()
    return [st, f"{st}_{t}"]


async def _fetch_items(categories: List[str], limit: int = 300) -> List[Dict[str, Any]]:
    if not categories:
        return []

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT code, name, rarity, category
            FROM items
            WHERE category = ANY($1::text[])
            LIMIT $2
            """,
            categories,
            limit,
        )
        return [dict(r) for r in rows]


async def _get_player_profession_key(tg_id: int) -> Optional[str]:
    """
    Витягує активну gathering-профу: herbalist/miner/stonemason.
    Сумісно з різними схемами players (з id або без).
    """
    if tg_id <= 0:
        return None

    pool = await get_pool()
    async with pool.acquire() as conn:
        # 1) players(id, tg_id, ...)
        try:
            row = await conn.fetchrow(
                """
                SELECT pr.code
                FROM players pl
                JOIN player_professions pp ON pp.player_id = pl.id
                JOIN professions pr ON pr.id = pp.profession_id
                WHERE pl.tg_id = $1
                  AND pr.kind = 'gathering'
                ORDER BY pp.updated_at DESC NULLS LAST, pp.created_at DESC NULLS LAST
                LIMIT 1
                """,
                tg_id,
            )
            if row:
                return str(row["code"])
        except asyncpg.UndefinedColumnError:
            pass

        # 2) fallback: players(tg_id PK), pp.player_id == tg_id
        try:
            row = await conn.fetchrow(
                """
                SELECT pr.code
                FROM players pl
                JOIN player_professions pp ON pp.player_id = pl.tg_id
                JOIN professions pr ON pr.id = pp.profession_id
                WHERE pl.tg_id = $1
                  AND pr.kind = 'gathering'
                ORDER BY pp.updated_at DESC NULLS LAST, pp.created_at DESC NULLS LAST
                LIMIT 1
                """,
                tg_id,
            )
            if row:
                return str(row["code"])
        except Exception:
            return None

    return None


def _resolve_source_type(explicit: Optional[str], profession_key: Optional[str]) -> str:
    """
    ✅ ГОЛОВНЕ ВИПРАВЛЕННЯ:
    Якщо source_type прийшов з кнопки (explicit) — використовуємо ТІЛЬКИ його.
    Інакше беремо з активної професії.
    """
    exp = _normalize_source(explicit)
    if exp:
        return exp

    prof = _normalize_source(profession_key)
    if prof:
        return prof

    # fallback
    return "herb"


# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────

async def roll_gathering_loot(
    tg_id: int,
    area_key: str,
    source_type: Optional[str],
    risk: str = "medium",
) -> List[ItemDrop]:
    """
    Повертає лут як items з таблиці items.
    ✅ Гарантовано не міксує професії:
    - якщо натиснув "stone" -> шукає тільки category='ks'
    - якщо натиснув "herb" -> шукає тільки herb/herb_tier
    """
    profession_key = await _get_player_profession_key(tg_id)

    chosen_source = _resolve_source_type(source_type, profession_key)

    tier = _pick_tier(risk)

    categories = _categories_for_source(chosen_source, tier=tier)

    items = await _fetch_items(categories, limit=300)
    if not items:
        return []

    n = random.randint(1, 3)
    picks = random.sample(items, k=min(n, len(items)))

    out: List[ItemDrop] = []
    for it in picks:
        out.append(
            ItemDrop(
                code=str(it["code"]),
                name=str(it.get("name") or it["code"]),
                qty=random.randint(1, 2),
                rarity=str(it.get("rarity") or tier),
            )
        )
    return out