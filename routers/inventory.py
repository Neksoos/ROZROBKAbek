# routers/inventory.py
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from db import get_pool

router = APIRouter(prefix="/api/inventory", tags=["inventory"])

# ─────────────────────────────
# ENSURE columns / indexes
# ─────────────────────────────

async def _ensure_items_columns() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""ALTER TABLE items ADD COLUMN IF NOT EXISTS emoji TEXT;""")
        await conn.execute("""ALTER TABLE items ADD COLUMN IF NOT EXISTS slot TEXT;""")
        await conn.execute("""ALTER TABLE items ADD COLUMN IF NOT EXISTS stats JSONB DEFAULT '{}'::jsonb;""")
        await conn.execute("""ALTER TABLE items ADD COLUMN IF NOT EXISTS description TEXT;""")
        await conn.execute("""ALTER TABLE items ADD COLUMN IF NOT EXISTS sell_price INTEGER;""")
        await conn.execute("""ALTER TABLE items ADD COLUMN IF NOT EXISTS stackable BOOLEAN DEFAULT FALSE;""")
        await conn.execute("""ALTER TABLE items ADD COLUMN IF NOT EXISTS category TEXT DEFAULT 'trash';""")

        # бойові колонки
        await conn.execute("""ALTER TABLE items ADD COLUMN IF NOT EXISTS atk INTEGER DEFAULT 0;""")
        await conn.execute("""ALTER TABLE items ADD COLUMN IF NOT EXISTS defense INTEGER DEFAULT 0;""")
        await conn.execute("""ALTER TABLE items ADD COLUMN IF NOT EXISTS hp INTEGER DEFAULT 0;""")
        await conn.execute("""ALTER TABLE items ADD COLUMN IF NOT EXISTS mp INTEGER DEFAULT 0;""")
        await conn.execute("""ALTER TABLE items ADD COLUMN IF NOT EXISTS weight INTEGER DEFAULT 0;""")

        # best-effort міграції/чистка
        await conn.execute("""UPDATE items SET sell_price = 1 WHERE sell_price IS NULL;""")
        await conn.execute(
            """
            UPDATE items
            SET description = descr
            WHERE (description IS NULL OR description = '')
              AND descr IS NOT NULL;
            """
        )
        await conn.execute("""UPDATE items SET stackable = FALSE WHERE stackable IS NULL;""")


async def _ensure_player_inventory_columns() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""ALTER TABLE player_inventory ADD COLUMN IF NOT EXISTS qty INTEGER;""")
        await conn.execute("""ALTER TABLE player_inventory ADD COLUMN IF NOT EXISTS is_equipped BOOLEAN DEFAULT FALSE;""")
        await conn.execute("""ALTER TABLE player_inventory ADD COLUMN IF NOT EXISTS slot TEXT;""")
        await conn.execute("""ALTER TABLE player_inventory ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW();""")
        await conn.execute("""ALTER TABLE player_inventory ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW();""")

        # якщо колись була amount — мігруємо в qty
        await conn.execute(
            """
            DO $$
            BEGIN
              IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name='player_inventory' AND column_name='amount'
              ) THEN
                EXECUTE '
                  UPDATE player_inventory
                  SET qty = amount
                  WHERE amount IS NOT NULL
                    AND (qty IS NULL OR qty=0 OR qty=1)
                    AND (qty IS NULL OR qty <> amount)
                ';
              END IF;
            END $$;
            """
        )
        await conn.execute("""UPDATE player_inventory SET qty = 1 WHERE qty IS NULL OR qty = 0;""")

        # partial unique index для стекабельних (tg_id,item_id) коли slot NULL і не екіп
        await conn.execute(
            """
            DO $$
            BEGIN
              IF NOT EXISTS (
                SELECT 1
                FROM pg_indexes
                WHERE indexname = 'uq_player_inventory_stack'
              ) THEN
                EXECUTE '
                  CREATE UNIQUE INDEX uq_player_inventory_stack
                  ON player_inventory (tg_id, item_id)
                  WHERE slot IS NULL AND is_equipped = FALSE
                ';
              END IF;
            END $$;
            """
        )


# ✅ для "вжити" — щоб hp/mp/energy точно були
async def _ensure_players_columns() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        # поточні значення
        await conn.execute("""ALTER TABLE players ADD COLUMN IF NOT EXISTS hp INTEGER;""")
        await conn.execute("""ALTER TABLE players ADD COLUMN IF NOT EXISTS mp INTEGER;""")
        await conn.execute("""ALTER TABLE players ADD COLUMN IF NOT EXISTS energy INTEGER;""")

        # максимуми (якщо їх нема — додаємо, щоб було куди капати)
        await conn.execute("""ALTER TABLE players ADD COLUMN IF NOT EXISTS hp_max INTEGER DEFAULT 100;""")
        await conn.execute("""ALTER TABLE players ADD COLUMN IF NOT EXISTS mp_max INTEGER DEFAULT 50;""")
        await conn.execute("""ALTER TABLE players ADD COLUMN IF NOT EXISTS energy_max INTEGER DEFAULT 240;""")

        # якщо energy NULL — підтягуємо до енергія_макс, щоб не було дивини
        await conn.execute(
            """
            UPDATE players
            SET energy = COALESCE(energy, energy_max)
            WHERE energy IS NULL;
            """
        )

# ─────────────────────────────
# helpers
# ─────────────────────────────

def _normalize_stats(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, (str, bytes)):
        try:
            return _normalize_stats(json.loads(raw))
        except Exception:
            return {}
    if isinstance(raw, list):
        out: Dict[str, Any] = {}
        for el in raw:
            if isinstance(el, dict):
                out.update(el)
        return out
    return {}


# ✅ Нормалізація слотів (бо в БД/даних могли зʼявитись "Чоботи", "Перстень", "Амулет" і т.д.)
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

def _normalize_slot(slot: Optional[str]) -> Optional[str]:
    if slot is None:
        return None
    s = (slot or "").strip().lower()
    if not s:
        return None
    return _SLOT_ALIASES.get(s, s)


_EMOJI_MAP = {
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


def _pick_emoji(category: Optional[str], fallback: Optional[str], slot: Optional[str]) -> str:
    if fallback:
        return fallback
    slot_n = _normalize_slot(slot)
    if slot_n and slot_n in _EMOJI_MAP:
        return _EMOJI_MAP[slot_n]
    c = (category or "").strip().lower()
    return _EMOJI_MAP.get(c, "🎒")


def _stackable(category: Optional[str]) -> bool:
    c = (category or "").strip().lower()
    return c.startswith(("trash", "herb", "ore", "stone", "mat", "food", "potion", "consum"))


def _merge_display_stats(
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


_ALLOWED_SLOTS = {"weapon", "armor", "helmet", "boots", "shield", "ring", "amulet", "trinket"}

# ─────────────────────────────
# DTO
# ─────────────────────────────

class InventoryItem(BaseModel):
    id: int
    item_id: int
    item_code: str
    emoji: Optional[str]
    name: str
    description: Optional[str] = None
    rarity: Optional[str] = None
    slot: Optional[str] = None
    stats: Dict[str, Any]
    qty: int
    is_equipped: bool


class InventoryListResponse(BaseModel):
    items: List[InventoryItem]


class EquipRequest(BaseModel):
    tg_id: int

# ─────────────────────────────
# Public: give_item_to_player
# ─────────────────────────────

async def give_item_to_player(
    tg_id: int,
    *,
    item_code: str,
    name: str,
    category: Optional[str] = None,
    emoji: Optional[str] = None,
    rarity: Optional[str] = None,
    description: Optional[str] = None,
    stats: Optional[Dict[str, Any]] = None,
    qty: Optional[int] = None,
    amount: Optional[int] = None,
    slot: Optional[str] = None,  # якщо передали slot -> це "екземпляр" (не стек)
) -> None:
    final_qty = qty if qty is not None else (amount if amount is not None else 1)
    try:
        final_qty = int(final_qty)
    except Exception:
        final_qty = 1

    if final_qty <= 0:
        return

    await _ensure_items_columns()
    await _ensure_player_inventory_columns()

    pool = await get_pool()
    stats_json: Dict[str, Any] = stats or {}

    # ✅ нормалізуємо slot перед записом у items/player_inventory
    slot_norm = _normalize_slot(slot)

    async with pool.acquire() as conn:
        item_row = await conn.fetchrow(
            "SELECT id, stackable, slot, category FROM items WHERE code = $1",
            item_code,
        )

        if not item_row:
            stack_flag = _stackable(category)
            await conn.execute(
                """
                INSERT INTO items(
                    code, name, category, emoji, rarity,
                    slot, stats, stackable, description
                )
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                """,
                item_code,
                name,
                category,
                emoji,
                rarity,
                slot_norm,
                stats_json,
                stack_flag,
                description,
            )
            item_row = await conn.fetchrow(
                "SELECT id, stackable, slot, category FROM items WHERE code = $1",
                item_code,
            )

        if not item_row:
            raise HTTPException(500, "ITEM_CREATE_FAILED")

        item_id = int(item_row["id"])
        stack = bool(item_row["stackable"]) if "stackable" in item_row else _stackable(category)

        # stack тільки для НЕ-екіпу (player_inventory.slot NULL)
        if stack and slot_norm is None:
            try:
                await conn.execute(
                    """
                    INSERT INTO player_inventory (tg_id, item_id, qty, is_equipped, slot, created_at, updated_at)
                    VALUES ($1, $2, $3, FALSE, NULL, NOW(), NOW())
                    ON CONFLICT (tg_id, item_id)
                    WHERE slot IS NULL AND is_equipped = FALSE
                    DO UPDATE
                    SET qty = player_inventory.qty + EXCLUDED.qty,
                        updated_at = NOW()
                    """,
                    tg_id,
                    item_id,
                    final_qty,
                )
                return
            except Exception:
                updated = await conn.execute(
                    """
                    UPDATE player_inventory
                    SET qty = qty + $3,
                        updated_at = NOW()
                    WHERE tg_id = $1
                      AND item_id = $2
                      AND slot IS NULL
                      AND is_equipped = FALSE
                    """,
                    tg_id,
                    item_id,
                    final_qty,
                )
                if isinstance(updated, str) and updated.endswith(" 0"):
                    await conn.execute(
                        """
                        INSERT INTO player_inventory(tg_id, item_id, qty, is_equipped, slot, created_at, updated_at)
                        VALUES ($1, $2, $3, FALSE, NULL, NOW(), NOW())
                        """,
                        tg_id,
                        item_id,
                        final_qty,
                    )
                return

        # не стек / або екземпляри екіпа -> окремими рядками
        for _ in range(final_qty):
            await conn.execute(
                """
                INSERT INTO player_inventory(tg_id, item_id, qty, is_equipped, slot, created_at, updated_at)
                VALUES ($1,$2,1,FALSE,$3,NOW(),NOW())
                """,
                tg_id,
                item_id,
                slot_norm,
            )

# ─────────────────────────────
# ✅ API: equip / unequip
# ─────────────────────────────

@router.post("/equip/{inv_id}")
async def equip(inv_id: int, req: EquipRequest):
    await _ensure_items_columns()
    await _ensure_player_inventory_columns()

    pool = await get_pool()
    async with pool.acquire() as conn:
        # ✅ мінімальний анти-рейс, щоб два кліки не давали 500
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT
                  pi.id AS inv_id,
                  pi.tg_id,
                  pi.is_equipped,
                  i.slot AS item_slot
                FROM player_inventory pi
                JOIN items i ON i.id = pi.item_id
                WHERE pi.id = $1 AND pi.tg_id = $2
                FOR UPDATE
                """,
                inv_id,
                req.tg_id,
            )
            if not row:
                raise HTTPException(404, "ITEM_NOT_FOUND")

            slot = _normalize_slot(row["item_slot"])
            if not slot:
                raise HTTPException(400, "ITEM_HAS_NO_SLOT")

            if slot not in _ALLOWED_SLOTS:
                raise HTTPException(400, "INVALID_SLOT")

            # Лочимо поточний екіп цього слоту, якщо є
            await conn.execute(
                """
                SELECT 1
                FROM player_inventory pi
                WHERE pi.tg_id = $1
                  AND pi.slot = $2
                  AND pi.is_equipped = TRUE
                FOR UPDATE
                """,
                req.tg_id,
                slot,
            )

            # 1) знімаємо все з цього слоту (важливо: slot -> NULL, щоб не ламати стек/консумки/логіку)
            await conn.execute(
                """
                UPDATE player_inventory
                SET is_equipped = FALSE,
                    slot = NULL,
                    updated_at = NOW()
                WHERE tg_id = $1
                  AND slot = $2
                  AND is_equipped = TRUE
                """,
                req.tg_id,
                slot,
            )

            # 2) екіпуємо цей предмет
            await conn.execute(
                """
                UPDATE player_inventory
                SET is_equipped = TRUE,
                    slot = $3,
                    updated_at = NOW()
                WHERE id = $1 AND tg_id = $2
                """,
                inv_id,
                req.tg_id,
                slot,
            )

    return {"ok": True}


@router.post("/unequip/{inv_id}")
async def unequip(inv_id: int, req: EquipRequest):
    await _ensure_items_columns()
    await _ensure_player_inventory_columns()

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE player_inventory
            SET is_equipped = FALSE,
                slot = NULL,
                updated_at = NOW()
            WHERE id = $1 AND tg_id = $2
            """,
            inv_id,
            req.tg_id,
        )
    return {"ok": True}


class UnequipSlotRequest(BaseModel):
    tg_id: int


@router.post("/unequip-slot/{slot}")
async def unequip_slot(slot: str, req: UnequipSlotRequest):
    await _ensure_items_columns()
    await _ensure_player_inventory_columns()

    slot = _normalize_slot(slot)
    if not slot or slot not in _ALLOWED_SLOTS:
        raise HTTPException(400, "INVALID_SLOT")

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE player_inventory
            SET is_equipped = FALSE,
                slot = NULL,
                updated_at = NOW()
            WHERE tg_id = $1
              AND slot = $2
              AND is_equipped = TRUE
            """,
            req.tg_id,
            slot,
        )

    return {"ok": True}

# ─────────────────────────────
# API: list inventory
# ─────────────────────────────

@router.get("", response_model=InventoryListResponse)
async def list_inventory(tg_id: int = Query(...)):
    await _ensure_items_columns()
    await _ensure_player_inventory_columns()

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                pi.id           AS inv_id,
                pi.item_id      AS item_id,
                pi.qty          AS qty,
                pi.is_equipped  AS is_equipped,
                pi.slot         AS inv_slot,
                i.code          AS item_code,
                i.emoji         AS emoji,
                i.name          AS name,
                i.description   AS description,
                i.rarity        AS rarity,
                i.slot          AS item_slot,
                i.category      AS category,
                i.stats         AS stats,
                i.atk           AS atk,
                i.defense       AS defense,
                i.hp            AS hp,
                i.mp            AS mp,
                i.weight        AS weight
            FROM player_inventory pi
            JOIN items i ON i.id = pi.item_id
            WHERE pi.tg_id = $1
            ORDER BY pi.is_equipped DESC, i.rarity NULLS LAST, i.name
            """,
            tg_id,
        )

    items: List[InventoryItem] = []
    for r in rows:
        merged_stats = _merge_display_stats(
            base_stats=_normalize_stats(r["stats"]),
            atk=int(r["atk"] or 0),
            defense=int(r["defense"] or 0),
            hp=int(r["hp"] or 0),
            mp=int(r["mp"] or 0),
            weight=int(r["weight"] or 0),
        )

        # slot показуємо як item_slot (що “може”) або inv_slot (що “вдягнуто”)
        slot_val = _normalize_slot(r["item_slot"] or r["inv_slot"])

        items.append(
            InventoryItem(
                id=r["inv_id"],
                item_id=r["item_id"],
                item_code=r["item_code"],
                emoji=_pick_emoji(r["category"], r["emoji"], slot_val),
                name=r["name"],
                description=r["description"],
                rarity=r["rarity"],
                slot=slot_val,
                stats=merged_stats,
                qty=int(r["qty"] or 1),
                is_equipped=bool(r["is_equipped"]),
            )
        )

    return InventoryListResponse(items=items)


@router.get("/{inv_id}", response_model=InventoryItem)
async def get_item(inv_id: int, tg_id: int = Query(...)):
    await _ensure_items_columns()
    await _ensure_player_inventory_columns()

    pool = await get_pool()
    async with pool.acquire() as conn:
        r = await conn.fetchrow(
            """
            SELECT
                pi.id           AS inv_id,
                pi.item_id      AS item_id,
                pi.qty          AS qty,
                pi.is_equipped  AS is_equipped,
                pi.slot         AS inv_slot,
                i.code          AS item_code,
                i.emoji         AS emoji,
                i.name          AS name,
                i.description   AS description,
                i.rarity        AS rarity,
                i.slot          AS item_slot,
                i.category      AS category,
                i.stats         AS stats,
                i.atk           AS atk,
                i.defense       AS defense,
                i.hp            AS hp,
                i.mp            AS mp,
                i.weight        AS weight
            FROM player_inventory pi
            JOIN items i ON i.id = pi.item_id
            WHERE pi.id = $1 AND pi.tg_id = $2
            """,
            inv_id,
            tg_id,
        )
    if not r:
        raise HTTPException(404, "ITEM_NOT_FOUND")

    merged_stats = _merge_display_stats(
        base_stats=_normalize_stats(r["stats"]),
        atk=int(r["atk"] or 0),
        defense=int(r["defense"] or 0),
        hp=int(r["hp"] or 0),
        mp=int(r["mp"] or 0),
        weight=int(r["weight"] or 0),
    )

    slot_val = _normalize_slot(r["item_slot"] or r["inv_slot"])

    return InventoryItem(
        id=r["inv_id"],
        item_id=r["item_id"],
        item_code=r["item_code"],
        emoji=_pick_emoji(r["category"], r["emoji"], slot_val),
        name=r["name"],
        description=r["description"],
        rarity=r["rarity"],
        slot=slot_val,
        stats=merged_stats,
        qty=int(r["qty"] or 1),
        is_equipped=bool(r["is_equipped"]),
    )

# ─────────────────────────────
# ✅ API: consume (eat / drink) прямо з інвентаря
# ─────────────────────────────

class ConsumeRequest(BaseModel):
    tg_id: int
    qty: int = 1  # за раз (дефолт 1)


@router.post("/consume/{inv_id}")
async def consume(inv_id: int, req: ConsumeRequest):
    await _ensure_items_columns()
    await _ensure_player_inventory_columns()
    await _ensure_players_columns()

    try:
        want = int(req.qty or 1)
    except Exception:
        want = 1
    if want <= 0:
        want = 1
    if want > 50:
        want = 50

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
              pi.id AS inv_id,
              pi.tg_id,
              pi.qty,
              pi.is_equipped,
              pi.slot AS inv_slot,
              i.category,
              i.stats,
              i.hp AS item_hp,
              i.mp AS item_mp
            FROM player_inventory pi
            JOIN items i ON i.id = pi.item_id
            WHERE pi.id = $1 AND pi.tg_id = $2
            """,
            inv_id,
            req.tg_id,
        )
        if not row:
            raise HTTPException(404, "ITEM_NOT_FOUND")

        if bool(row["is_equipped"]):
            raise HTTPException(400, "ITEM_NOT_USABLE")

        # тільки “стекові” консумки (slot має бути NULL)
        if row["inv_slot"] is not None:
            raise HTTPException(400, "ITEM_NOT_USABLE")

        have = int(row["qty"] or 0)
        if have <= 0:
            raise HTTPException(400, "NOT_ENOUGH_QTY")

        use_qty = min(have, want)

        category = (row["category"] or "").strip().lower()
        base_stats = _normalize_stats(row["stats"])

        hp_restore = int(base_stats.get("hp", 0) or 0) + int(row["item_hp"] or 0)
        mp_restore = int(base_stats.get("mp", 0) or 0) + int(row["item_mp"] or 0)
        energy_restore = int(base_stats.get("energy", 0) or 0)

        allowed_cat = category.startswith(("food", "potion", "consum"))
        if not allowed_cat and (hp_restore <= 0 and mp_restore <= 0 and energy_restore <= 0):
            raise HTTPException(400, "ITEM_NOT_USABLE")

        hp_restore *= use_qty
        mp_restore *= use_qty
        energy_restore *= use_qty

        # 1) списуємо предмет
        if have - use_qty <= 0:
            await conn.execute(
                "DELETE FROM player_inventory WHERE id=$1 AND tg_id=$2",
                inv_id,
                req.tg_id,
            )
            remaining_qty = 0
        else:
            await conn.execute(
                """
                UPDATE player_inventory
                SET qty = qty - $3,
                    updated_at = NOW()
                WHERE id=$1 AND tg_id=$2
                """,
                inv_id,
                req.tg_id,
                use_qty,
            )
            remaining_qty = have - use_qty

        # 2) застосовуємо ефекти до гравця з капами
        p = await conn.fetchrow(
            """
            SELECT hp, mp, energy, hp_max, mp_max, energy_max
            FROM players
            WHERE tg_id=$1
            """,
            req.tg_id,
        )
        if not p:
            raise HTTPException(404, "PLAYER_NOT_FOUND")

        hp_max = int(p["hp_max"] or 100)
        mp_max = int(p["mp_max"] or 50)
        energy_max = int(p["energy_max"] or 240)

        cur_hp = int(p["hp"]) if p["hp"] is not None else hp_max
        cur_mp = int(p["mp"]) if p["mp"] is not None else mp_max
        cur_en = int(p["energy"]) if p["energy"] is not None else energy_max

        new_hp = min(hp_max, cur_hp + hp_restore) if hp_restore > 0 else cur_hp
        new_mp = min(mp_max, cur_mp + mp_restore) if mp_restore > 0 else cur_mp
        new_en = min(energy_max, cur_en + energy_restore) if energy_restore > 0 else cur_en

        await conn.execute(
            """
            UPDATE players
            SET hp=$2, mp=$3, energy=$4
            WHERE tg_id=$1
            """,
            req.tg_id,
            new_hp,
            new_mp,
            new_en,
        )

    return {
        "ok": True,
        "used_qty": use_qty,
        "remaining_qty": remaining_qty,
        "hp": new_hp,
        "hp_max": hp_max,
        "mp": new_mp,
        "mp_max": mp_max,
        "energy": new_en,
        "energy_max": energy_max,
    }