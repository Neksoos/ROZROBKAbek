# services/inventory/repo.py
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException

from db import get_pool
from services.inventory.migrations import (
    ensure_items_columns,
    ensure_player_inventory_columns,
    ensure_players_columns,
)
from services.inventory.utils import normalize_slot, stackable


async def give_item_to_player_repo(
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
    slot: Optional[str] = None,
) -> None:
    """
    Правило:
    - Стекові (stackable=True і items.slot IS NULL) -> один рядок, slot=NULL, qty=N
    - Екіп/не-стек -> кожен екземпляр окремим рядком, slot=items.slot (НЕ NULL), qty=1
    """
    final_qty = qty if qty is not None else (amount if amount is not None else 1)
    try:
        final_qty = int(final_qty)
    except Exception:
        final_qty = 1

    if final_qty <= 0:
        return

    await ensure_items_columns()
    await ensure_player_inventory_columns()

    pool = await get_pool()
    stats_json: Dict[str, Any] = stats or {}
    slot_norm = normalize_slot(slot)

    async with pool.acquire() as conn:
        item_row = await conn.fetchrow(
            "SELECT id, stackable, slot, category FROM items WHERE code = $1",
            item_code,
        )

        if not item_row:
            stack_flag = stackable(category)
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
        item_slot = normalize_slot(item_row["slot"])
        stack_flag = bool(item_row["stackable"]) if "stackable" in item_row else stackable(category)

        # ✅ Стек дозволяємо ТІЛЬКИ якщо предмет stackable і В items.slot НЕ заданий (тобто це НЕ екіп)
        if stack_flag and item_slot is None:
            # Тримаємо всі стеки в slot=NULL (не екіп)
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

        # ✅ Не-стек (екіп або інші екземпляри):
        # slot НІКОЛИ не NULL. Беремо items.slot, інакше (fallback) slot_norm.
        final_slot = item_slot or slot_norm
        if not final_slot:
            raise HTTPException(400, "ITEM_HAS_NO_SLOT")

        for _ in range(final_qty):
            await conn.execute(
                """
                INSERT INTO player_inventory(tg_id, item_id, qty, is_equipped, slot, created_at, updated_at)
                VALUES ($1,$2,1,FALSE,$3,NOW(),NOW())
                """,
                tg_id,
                item_id,
                final_slot,
            )


async def equip_repo(inv_id: int, tg_id: int) -> None:
    """
    Важливо:
    - НЕ робимо slot=NULL при unequip (інакше це починає конфліктувати зі стеками/індексами)
    - swap робимо без проміжного стану, який ламає унікальні індекси
    """
    await ensure_items_columns()
    await ensure_player_inventory_columns()

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT
                  pi.id AS inv_id,
                  pi.tg_id,
                  COALESCE(pi.qty,1) AS qty,
                  COALESCE(pi.is_equipped,FALSE) AS is_equipped,
                  pi.slot AS inv_slot,
                  pi.item_id AS item_id,
                  i.slot AS item_slot
                FROM player_inventory pi
                JOIN items i ON i.id = pi.item_id
                WHERE pi.id = $1 AND pi.tg_id = $2
                FOR UPDATE
                """,
                inv_id,
                tg_id,
            )
            if not row:
                raise HTTPException(404, "ITEM_NOT_FOUND")

            # ✅ не даємо екіпати стек
            if int(row["qty"] or 1) != 1:
                raise HTTPException(400, "CANNOT_EQUIP_STACK")

            slot = normalize_slot(row["item_slot"] or row["inv_slot"])
            if not slot:
                raise HTTPException(400, "ITEM_HAS_NO_SLOT")

            # Лочимо поточний екіп у слоті (якщо є)
            cur = await conn.fetchrow(
                """
                SELECT id
                FROM player_inventory
                WHERE tg_id = $1
                  AND slot = $2
                  AND is_equipped = TRUE
                FOR UPDATE
                """,
                tg_id,
                slot,
            )

            # Якщо вже екіпнуто саме цей inv_id — нічого не робимо
            if cur and int(cur["id"]) == int(inv_id):
                return

            # 1) знімаємо поточний екіп (ТОЧКОВО по id)
            if cur:
                await conn.execute(
                    """
                    UPDATE player_inventory
                    SET is_equipped = FALSE,
                        updated_at = NOW()
                    WHERE id = $1 AND tg_id = $2
                    """,
                    int(cur["id"]),
                    tg_id,
                )

            # 2) екіпуємо обраний
            await conn.execute(
                """
                UPDATE player_inventory
                SET is_equipped = TRUE,
                    slot = $3,
                    updated_at = NOW()
                WHERE id = $1 AND tg_id = $2
                """,
                inv_id,
                tg_id,
                slot,
            )


async def unequip_repo(inv_id: int, tg_id: int) -> None:
    await ensure_items_columns()
    await ensure_player_inventory_columns()

    pool = await get_pool()
    async with pool.acquire() as conn:
        # ⚠️ НЕ СТАВИМО slot=NULL
        await conn.execute(
            """
            UPDATE player_inventory
            SET is_equipped = FALSE,
                updated_at = NOW()
            WHERE id = $1 AND tg_id = $2
            """,
            inv_id,
            tg_id,
        )


async def unequip_slot_repo(slot: str, tg_id: int) -> None:
    await ensure_items_columns()
    await ensure_player_inventory_columns()

    slot = normalize_slot(slot)
    if not slot:
        raise HTTPException(400, "INVALID_SLOT")

    pool = await get_pool()
    async with pool.acquire() as conn:
        # ⚠️ НЕ СТАВИМО slot=NULL
        await conn.execute(
            """
            UPDATE player_inventory
            SET is_equipped = FALSE,
                updated_at = NOW()
            WHERE tg_id = $1
              AND slot = $2
              AND is_equipped = TRUE
            """,
            tg_id,
            slot,
        )


async def list_inventory_rows(tg_id: int):
    await ensure_items_columns()
    await ensure_player_inventory_columns()

    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
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


async def get_item_row(inv_id: int, tg_id: int):
    await ensure_items_columns()
    await ensure_player_inventory_columns()

    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(
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


async def consume_repo(inv_id: int, tg_id: int, want_qty: int) -> Dict[str, int]:
    await ensure_items_columns()
    await ensure_player_inventory_columns()
    await ensure_players_columns()

    try:
        want = int(want_qty or 1)
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
            tg_id,
        )
        if not row:
            raise HTTPException(404, "ITEM_NOT_FOUND")

        if bool(row["is_equipped"]):
            raise HTTPException(400, "ITEM_NOT_USABLE")

        # ✅ тільки стеки консумок (slot має бути NULL)
        if row["inv_slot"] is not None:
            raise HTTPException(400, "ITEM_NOT_USABLE")

        have = int(row["qty"] or 0)
        if have <= 0:
            raise HTTPException(400, "NOT_ENOUGH_QTY")

        use_qty = min(have, want)

        category = (row["category"] or "").strip().lower()
        stats = row["stats"]
        base_stats: Dict[str, Any] = dict(stats or {})

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
                tg_id,
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
                tg_id,
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
            tg_id,
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
            tg_id,
            new_hp,
            new_mp,
            new_en,
        )

    return {
        "used_qty": use_qty,
        "remaining_qty": remaining_qty,
        "hp": new_hp,
        "hp_max": hp_max,
        "mp": new_mp,
        "mp_max": mp_max,
        "energy": new_en,
        "energy_max": energy_max,
    }
