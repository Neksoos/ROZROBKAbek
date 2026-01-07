# services/inventory/utils.py
from __future__ import annotations

import json
from typing import Any, Dict, Optional


def normalize_stats(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, (str, bytes)):
        try:
            return normalize_stats(json.loads(raw))
        except Exception:
            return {}
    if isinstance(raw, list):
        out: Dict[str, Any] = {}
        for el in raw:
            if isinstance(el, dict):
                out.update(el)
        return out
    return {}


_SLOT_ALIASES: Dict[str, str] = {
    # canonical
    "weapon": "weapon",
    "armor": "armor",
    "helmet": "helmet",
    "boots": "boots",
    "shield": "shield",
    "ring": "ring",
    "amulet": "amulet",
    "trinket": "trinket",
    # ua/ru/common
    "зброя": "weapon",
    "меч": "weapon",
    "сокира": "weapon",
    "булава": "weapon",
    "броня": "armor",
    "обладунок": "armor",
    "панцир": "armor",
    "шолом": "helmet",
    "каптур": "helmet",
    "голова": "helmet",
    "чоботи": "boots",
    "черевики": "boots",
    "сапоги": "boots",
    "щит": "shield",
    "перстень": "ring",
    "кільце": "ring",
    "кольцо": "ring",
    "амулет": "amulet",
    "хрестик": "amulet",
    "оберіг": "amulet",
    "талісман": "trinket",
    "дрібничка": "trinket",
    "брелок": "trinket",
}


def normalize_slot(slot: Optional[str]) -> Optional[str]:
    if slot is None:
        return None
    s = (slot or "").strip().lower()
    if not s:
        return None
    return _SLOT_ALIASES.get(s, s)


EMOJI_MAP = {
    "weapon": "⚔️",
    "armor": "🛡️",
    "shield": "🛡️",
    "helmet": "🪖",
    "boots": "🥾",
    "ring": "💍",
    "amulet": "🧿",
    "trinket": "🔮",
    "food": "🍗",
    "consum": "🍗",
    "potion": "🧪",
    "herb": "🌿",
    "ore": "⛏️",
    "stone": "⛏️",
    "mat": "🧱",
    "trash": "🗑️",
    "equip": "🧰",
}


def pick_emoji(category: Optional[str], fallback: Optional[str], slot: Optional[str]) -> str:
    if fallback:
        return fallback
    slot_n = normalize_slot(slot)
    if slot_n and slot_n in EMOJI_MAP:
        return EMOJI_MAP[slot_n]
    c = (category or "").strip().lower()
    return EMOJI_MAP.get(c, "🎒")


def stackable(category: Optional[str]) -> bool:
    c = (category or "").strip().lower()
    return c.startswith(("trash", "herb", "ore", "stone", "mat", "food", "potion", "consum"))


def merge_display_stats(
    *,
    base_stats: Dict[str, Any],
    atk: int,
    defense: int,
    hp: int,
    mp: int,
    weight: int,
) -> Dict[str, Any]:
    s = dict(base_stats or {})
    s.pop("source", None)
    if atk:
        s["atk"] = int(atk)
    if defense:
        s["def"] = int(defense)
    if hp:
        s["hp"] = int(hp)
    if mp:
        s["mp"] = int(mp)
    if weight:
        s["weight"] = int(weight)
    return s


ALLOWED_SLOTS = {"weapon", "armor", "helmet", "boots", "shield", "ring", "amulet", "trinket"}