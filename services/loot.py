from __future__ import annotations

from typing import Any, Dict, List, Optional
import random
import json

from db import get_pool

RARITIES_FOR_EQUIP = ("Звичайний", "Добротний")

EQUIP_DROP_CHANCE = 0.10
TRASH_DROP_CHANCE = 0.65

# cache structure:
# {
#   "equip": {"__global__": [...], "slums": [...], "suburbs": [...], ...},
#   "trash": [...],
# }
_LOOT_CACHE: Optional[Dict[str, Any]] = None


def _normalize_stats(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (str, bytes)):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    try:
        return dict(raw)  # type: ignore
    except Exception:
        return {}


def _norm_area(a: Optional[str]) -> str:
    return (a or "").strip().lower()


def _extract_drop_areas(stats: Dict[str, Any]) -> List[str]:
    """
    Очікуємо в items.stats:
      drop_areas: ["suburbs","slums"] або "suburbs"
    Якщо drop_areas відсутній -> предмет глобальний.
    """
    v = stats.get("drop_areas")
    if v is None:
        return []
    if isinstance(v, str):
        s = _norm_area(v)
        return [s] if s else []
    if isinstance(v, list):
        out: List[str] = []
        for x in v:
            if isinstance(x, str):
                s = _norm_area(x)
                if s:
                    out.append(s)
        return out
    return []


def _extract_drop_weight(stats: Dict[str, Any]) -> int:
    """
    Вага дропу:
      1) stats.drop_weight (НОВЕ)
      2) stats.loot_weight (СТАРЕ, для сумісності)
    """
    w = stats.get("drop_weight", None)
    if w is None:
        w = stats.get("loot_weight", 1)

    try:
        w = int(w)
    except Exception:
        w = 1

    return max(0, w)


async def _load_loot_items() -> Dict[str, Any]:
    global _LOOT_CACHE
    if _LOOT_CACHE is not None:
        return _LOOT_CACHE

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                id, code, name, description, rarity, category, slot, emoji,
                atk, defense, hp, mp, level_req, sell_price, stats, is_archived
            FROM items
            WHERE COALESCE(is_archived, false) = false
              AND category IN ('equip', 'trash', 'junk')
            """
        )

    equip_by_area: Dict[str, List[Dict[str, Any]]] = {"__global__": []}
    trash: List[Dict[str, Any]] = []

    for r in rows:
        stats = _normalize_stats(r["stats"])
        drop_weight = _extract_drop_weight(stats)
        if drop_weight <= 0:
            continue

        item_dict: Dict[str, Any] = {
            "id": r["id"],
            "code": r["code"],
            "name": r["name"],
            "description": r["description"],
            "rarity": r["rarity"],
            "category": r["category"],
            "slot": r["slot"],
            "emoji": r["emoji"],
            "atk": r["atk"],
            "defense": r["defense"],
            "hp": r["hp"],
            "mp": r["mp"],
            "level_req": r["level_req"],
            "sell_price": r["sell_price"],
            "stats": stats,
            "drop_weight": drop_weight,   # ✅ окреме поле для ролу
        }

        # ── EQUIP
        if (
            r["category"] == "equip"
            and r["slot"] is not None
            and r["rarity"] in RARITIES_FOR_EQUIP
        ):
            areas = _extract_drop_areas(stats)
            if not areas:
                equip_by_area["__global__"].append(item_dict)
            else:
                for a in areas:
                    equip_by_area.setdefault(a, []).append(item_dict)
            continue

        # ── TRASH/JUNK
        if r["category"] in ("trash", "junk"):
            trash.append(item_dict)

    _LOOT_CACHE = {"equip": equip_by_area, "trash": trash}
    return _LOOT_CACHE


def invalidate_loot_cache() -> None:
    global _LOOT_CACHE
    _LOOT_CACHE = None


def _weighted_choice(items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not items:
        return None
    weights = [max(1, int(it.get("drop_weight", 1))) for it in items]
    return random.choices(items, weights=weights, k=1)[0]


async def get_loot_for_mob(mob_code: str, *, area_key: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Повертає 0..1 предмет.
    Якщо area_key передали — спочатку пробує пул цієї локації,
    потім додає глобальні предмети.
    """
    loot_data = await _load_loot_items()

    equip_by_area: Dict[str, List[Dict[str, Any]]] = loot_data.get("equip", {}) or {}
    trash: List[Dict[str, Any]] = loot_data.get("trash", []) or []

    roll = random.random()

    if roll < EQUIP_DROP_CHANCE:
        a = _norm_area(area_key)
        pool: List[Dict[str, Any]] = []
        if a and a in equip_by_area:
            pool.extend(equip_by_area[a])
        pool.extend(equip_by_area.get("__global__", []))

        if pool:
            item = _weighted_choice(pool)
            return [item] if item else []
        return []

    if roll < EQUIP_DROP_CHANCE + TRASH_DROP_CHANCE and trash:
        item = _weighted_choice(trash)
        return [item] if item else []

    return []