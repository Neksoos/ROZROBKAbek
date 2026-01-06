# routers/tavern.py
from __future__ import annotations

import json
from typing import Optional, List, Any, Dict, Tuple

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from loguru import logger

from db import get_pool
from services.char_stats import get_full_stats_for_player  # type: ignore
from services.daily_login import process_daily_login  # type: ignore

from .inventory import _ensure_items_columns as _ensure_items_base  # type: ignore
from core.tg_auth import get_tg_user  # ✅ initData auth

router = APIRouter(prefix="/api/tavern", tags=["tavern"])


# ─────────────────────────────────────────────
# tg_id dependency (тільки з перевіреного initData)
# ─────────────────────────────────────────────
async def get_tg_id(u: Dict[str, Any] = Depends(get_tg_user)) -> int:
    if not u or u.get("id") is None:
        raise HTTPException(status_code=401, detail="Invalid initData: user.id missing")
    return int(u["id"])


# ─────────────────────────────────────────────
# ENSURE: колонки для корчми + інвентаря
# ─────────────────────────────────────────────
async def _ensure_items_for_tavern() -> None:
    """
    - ensure items.base_value / items.sell_price / items.is_active
    - ensure player_inventory.qty існує + is_equipped
    ВАЖЛИВО: ми НЕ використовуємо legacy amount взагалі.
    """
    await _ensure_items_base()

    pool = await get_pool()
    async with pool.acquire() as conn:
        # items
        await conn.execute("""ALTER TABLE items ADD COLUMN IF NOT EXISTS base_value INT;""")
        await conn.execute("""ALTER TABLE items ADD COLUMN IF NOT EXISTS sell_price INT;""")
        await conn.execute(
            """ALTER TABLE items ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;"""
        )
        await conn.execute("""UPDATE items SET is_active = TRUE WHERE is_active IS NULL;""")

        # player_inventory
        await conn.execute(
            """ALTER TABLE player_inventory ADD COLUMN IF NOT EXISTS qty INT DEFAULT 1;"""
        )
        await conn.execute(
            """ALTER TABLE player_inventory ADD COLUMN IF NOT EXISTS is_equipped BOOLEAN DEFAULT FALSE;"""
        )

        await conn.execute("""UPDATE player_inventory SET qty = 1 WHERE qty IS NULL OR qty <= 0;""")
        await conn.execute(
            """UPDATE player_inventory SET is_equipped = FALSE WHERE is_equipped IS NULL;"""
        )


# ─────────────────────────────────────────────
# ЛОГІКА ЦІНИ ПРОДАЖУ
# ─────────────────────────────────────────────
SELL_COEF = 0.4  # 40% від base_value


def _fallback_price_by_category(category: Optional[str]) -> int:
    if not category:
        return 1
    cat = category.lower()
    if cat in ("trash", "herb", "ore", "mat", "consum", "food"):
        return 1
    if cat in ("weapon", "armor", "shield", "ring", "trinket", "amulet"):
        return 5
    return 1


def _compute_sell_price(
    sell_price_db: Optional[int],
    base_value_db: Optional[int],
    category: Optional[str],
) -> int:
    if sell_price_db is not None and sell_price_db > 0:
        price = int(sell_price_db)
    elif base_value_db is not None and base_value_db > 0:
        price = int(base_value_db * SELL_COEF)
    else:
        price = _fallback_price_by_category(category)

    return max(1, int(price))


# ─────────────────────────────────────────────
# ВІДПОЧИНОК У КОРЧМІ
# ─────────────────────────────────────────────
REST_PRICE = 50


class RestResponse(BaseModel):
    ok: bool
    hp: int
    hp_max: int
    mp: int
    mp_max: int
    chervontsi: int

    daily_applied: bool = Field(False)
    daily_xp: int = Field(0)
    daily_chervontsi: int = Field(0)
    daily_kleynod: bool = Field(False)


@router.post("/rest", response_model=RestResponse)
async def tavern_rest(tg_id: int = Depends(get_tg_id)):
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COALESCE(chervontsi,0) AS chervontsi FROM players WHERE tg_id=$1",
            tg_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail="PLAYER_NOT_FOUND")

        ch = int(row["chervontsi"] or 0)
        if ch < REST_PRICE:
            raise HTTPException(status_code=400, detail="NOT_ENOUGH_CHERVONTSI")

        stats = await get_full_stats_for_player(tg_id)
        hp_max = int(stats.get("hp_max", 1))
        mp_max = int(stats.get("mp_max", 0))

        await conn.execute(
            """
            UPDATE players
            SET hp=$2,
                mp=$3,
                chervontsi=COALESCE(chervontsi,0) - $4
            WHERE tg_id=$1
            """,
            tg_id,
            hp_max,
            mp_max,
            REST_PRICE,
        )

    # daily бонус
    daily_xp = 0
    daily_ch = 0
    daily_k = False
    daily_applied = False

    try:
        xp_gain, coins_gain, got_kleynod = await process_daily_login(tg_id)
        daily_xp = int(xp_gain or 0)
        daily_ch = int(coins_gain or 0)
        daily_k = bool(got_kleynod)
        daily_applied = (daily_xp > 0) or (daily_ch > 0) or daily_k
    except Exception as e:
        logger.exception(f"daily_login failed for rest tg_id={tg_id}: {e}")

    async with pool.acquire() as conn:
        row2 = await conn.fetchrow(
            "SELECT COALESCE(chervontsi,0) AS chervontsi FROM players WHERE tg_id=$1",
            tg_id,
        )
        new_chervontsi = int(row2["chervontsi"]) if row2 else 0

    return RestResponse(
        ok=True,
        hp=hp_max,
        hp_max=hp_max,
        mp=mp_max,
        mp_max=mp_max,
        chervontsi=new_chervontsi,
        daily_applied=daily_applied,
        daily_xp=daily_xp,
        daily_chervontsi=daily_ch,
        daily_kleynod=daily_k,
    )


# ─────────────────────────────────────────────
# SELL: DTO
# ─────────────────────────────────────────────
class SellListItem(BaseModel):
    inv_id: int
    item_id: int
    code: str
    name: str
    emoji: Optional[str]
    rarity: Optional[str]
    amount: int
    base_value: int
    total_value: int


class SellListResponse(BaseModel):
    items: List[SellListItem]


class SellItemRequest(BaseModel):
    amount: Optional[int] = Field(1, ge=1)


class SellItemResponse(BaseModel):
    inv_id: int
    item_id: int
    item_name: str
    amount_sold: int
    chervontsi_gained: int
    chervontsi_total: int
    amount_left: int

    daily_applied: bool = Field(False)
    daily_xp: int = Field(0)
    daily_chervontsi: int = Field(0)
    daily_kleynod: bool = Field(False)


# ─────────────────────────────────────────────
# SELL LIST (ТІЛЬКИ qty)
# ─────────────────────────────────────────────
@router.get("/sell/list", response_model=SellListResponse)
async def list_sellable_items(tg_id: int = Depends(get_tg_id)):
    await _ensure_items_for_tavern()

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                pi.id        AS inv_id,
                pi.item_id   AS item_id,
                COALESCE(pi.qty, 0) AS qty,
                i.code       AS code,
                i.name       AS name,
                i.emoji      AS emoji,
                i.rarity     AS rarity,
                i.category   AS category,
                i.base_value AS base_value,
                i.sell_price AS sell_price
            FROM player_inventory pi
            JOIN items i ON i.id = pi.item_id
            WHERE pi.tg_id = $1
              AND COALESCE(pi.is_equipped, FALSE) = FALSE
              AND COALESCE(pi.qty, 0) > 0
            ORDER BY i.rarity NULLS LAST, i.name
            """,
            tg_id,
        )

    items: List[SellListItem] = []
    for r in rows:
        qty = int(r["qty"] or 0)
        if qty <= 0:
            continue

        price_per_unit = _compute_sell_price(
            sell_price_db=r["sell_price"],
            base_value_db=r["base_value"],
            category=r["category"],
        )
        total_value = price_per_unit * qty
        if total_value <= 0:
            continue

        items.append(
            SellListItem(
                inv_id=int(r["inv_id"]),
                item_id=int(r["item_id"]),
                code=r["code"],
                name=r["name"],
                emoji=r["emoji"],
                rarity=r["rarity"],
                amount=qty,
                base_value=price_per_unit,
                total_value=total_value,
            )
        )

    return SellListResponse(items=items)


# ─────────────────────────────────────────────
# SELL ITEM (ТІЛЬКИ qty)
# ─────────────────────────────────────────────
@router.post("/sell/{inv_id}", response_model=SellItemResponse)
async def sell_item(inv_id: int, req: SellItemRequest, tg_id: int = Depends(get_tg_id)):
    await _ensure_items_for_tavern()

    amount_requested = int(req.amount or 1)
    if amount_requested <= 0:
        raise HTTPException(status_code=400, detail="INVALID_AMOUNT")

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                pi.id          AS inv_id,
                pi.item_id     AS item_id,
                COALESCE(pi.qty, 0) AS qty,
                COALESCE(pi.is_equipped, FALSE) AS is_equipped,
                i.name         AS name,
                i.category     AS category,
                i.base_value   AS base_value,
                i.sell_price   AS sell_price
            FROM player_inventory pi
            JOIN items i ON i.id = pi.item_id
            WHERE pi.id = $1 AND pi.tg_id = $2
            """,
            inv_id,
            tg_id,
        )

        if not row:
            raise HTTPException(status_code=404, detail="ITEM_NOT_FOUND")

        if bool(row["is_equipped"]):
            raise HTTPException(status_code=400, detail="ITEM_EQUIPPED")

        current_qty = int(row["qty"] or 0)
        if current_qty <= 0:
            raise HTTPException(status_code=400, detail="EMPTY_STACK")

        amount_sold = min(amount_requested, current_qty)

        price_per_unit = _compute_sell_price(
            sell_price_db=row["sell_price"],
            base_value_db=row["base_value"],
            category=row["category"],
        )
        gain = int(amount_sold * price_per_unit)

        async with conn.transaction():
            if current_qty > amount_sold:
                await conn.execute(
                    """
                    UPDATE player_inventory
                    SET qty = qty - $2
                    WHERE id = $1
                    """,
                    inv_id,
                    amount_sold,
                )
                amount_left = current_qty - amount_sold
            else:
                await conn.execute("DELETE FROM player_inventory WHERE id = $1", inv_id)
                amount_left = 0

            row_player = await conn.fetchrow(
                """
                UPDATE players
                SET chervontsi = COALESCE(chervontsi,0) + $2
                WHERE tg_id = $1
                RETURNING chervontsi
                """,
                tg_id,
                gain,
            )

        total_chervontsi = int(row_player["chervontsi"]) if row_player else gain

    # daily бонус
    daily_xp = 0
    daily_ch = 0
    daily_k = False
    daily_applied = False

    try:
        xp_gain, coins_gain, got_kleynod = await process_daily_login(tg_id)
        daily_xp = int(xp_gain or 0)
        daily_ch = int(coins_gain or 0)
        daily_k = bool(got_kleynod)
        daily_applied = (daily_xp > 0) or (daily_ch > 0) or daily_k
    except Exception as e:
        logger.exception(f"daily_login failed for sell tg_id={tg_id}: {e}")

    async with pool.acquire() as conn:
        row2 = await conn.fetchrow(
            "SELECT COALESCE(chervontsi,0) AS chervontsi FROM players WHERE tg_id=$1",
            tg_id,
        )
        if row2:
            total_chervontsi = int(row2["chervontsi"])

    return SellItemResponse(
        inv_id=int(inv_id),
        item_id=int(row["item_id"]),
        item_name=row["name"],
        amount_sold=int(amount_sold),
        chervontsi_gained=int(gain),
        chervontsi_total=int(total_chervontsi),
        amount_left=int(amount_left),
        daily_applied=daily_applied,
        daily_xp=daily_xp,
        daily_chervontsi=daily_ch,
        daily_kleynod=daily_k,
    )


# ─────────────────────────────────────────────
# FOOD SHOP: купівля їжі (ТЕПЕР: НЕ ВІДНОВЛЮЄ HP/MP ПРИ ПОКУПЦІ)
# ─────────────────────────────────────────────
FOOD_BUY_MULTIPLIER = 1.0  # можеш зробити 1.2 чи 1.5 якщо треба дорожче


class FoodShopItem(BaseModel):
    item_id: int
    code: str
    name: str
    emoji: Optional[str]
    rarity: Optional[str]

    price: int
    hp_restore: int
    mp_restore: int


class FoodShopListResponse(BaseModel):
    items: List[FoodShopItem]


class FoodBuyRequest(BaseModel):
    qty: int = Field(1, ge=1, le=50)


class FoodBuyResponse(BaseModel):
    ok: bool
    item_id: int
    item_name: str
    qty: int
    price_total: int

    hp: int
    hp_max: int
    mp: int
    mp_max: int
    chervontsi: int

    daily_applied: bool = Field(False)
    daily_xp: int = Field(0)
    daily_chervontsi: int = Field(0)
    daily_kleynod: bool = Field(False)


def _extract_restore_from_stats(stats: Any) -> Tuple[int, int]:
    """
    В items.stats (JSONB або TEXT(JSON)) беремо hp/mp як величини відновлення.
    (Для відображення на фронті + майбутнього "use item")
    """
    if not stats:
        return (0, 0)

    if isinstance(stats, str):
        try:
            stats = json.loads(stats)
        except Exception:
            return (0, 0)

    if not isinstance(stats, dict):
        return (0, 0)

    hp = int(stats.get("hp") or 0)
    mp = int(stats.get("mp") or 0)
    return (max(0, hp), max(0, mp))


def _compute_buy_price(base_value: Optional[int], sell_price: Optional[int]) -> int:
    """
    Для покупки беремо base_value (якщо є), інакше sell_price, інакше 5.
    """
    if base_value is not None and int(base_value) > 0:
        p = int(base_value)
    elif sell_price is not None and int(sell_price) > 0:
        p = int(sell_price)
    else:
        p = 5
    p = int(p * float(FOOD_BUY_MULTIPLIER))
    return max(1, p)


@router.get("/food/list", response_model=FoodShopListResponse)
async def food_list(tg_id: int = Depends(get_tg_id)):
    await _ensure_items_for_tavern()

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
              id AS item_id,
              code,
              name,
              emoji,
              rarity,
              stats,
              base_value,
              sell_price
            FROM items
            WHERE category = 'food'
              AND COALESCE(is_active, TRUE) = TRUE
            ORDER BY
              CASE COALESCE(rarity,'common')
                WHEN 'common' THEN 1
                WHEN 'uncommon' THEN 2
                WHEN 'rare' THEN 3
                WHEN 'epic' THEN 4
                WHEN 'legendary' THEN 5
                WHEN 'mythic' THEN 6
                ELSE 10
              END,
              name ASC
            """
        )

    items: List[FoodShopItem] = []
    for r in rows:
        hp_restore, mp_restore = _extract_restore_from_stats(r["stats"])
        # якщо “їжа” без ефекту — не показуємо
        if hp_restore <= 0 and mp_restore <= 0:
            continue

        price = _compute_buy_price(r["base_value"], r["sell_price"])

        items.append(
            FoodShopItem(
                item_id=int(r["item_id"]),
                code=str(r["code"]),
                name=str(r["name"]),
                emoji=r["emoji"],
                rarity=r["rarity"],
                price=int(price),
                hp_restore=int(hp_restore),
                mp_restore=int(mp_restore),
            )
        )

    return FoodShopListResponse(items=items)


@router.post("/food/buy/{item_id}", response_model=FoodBuyResponse)
async def food_buy(
    item_id: int,
    req: FoodBuyRequest,
    tg_id: int = Depends(get_tg_id),
):
    await _ensure_items_for_tavern()
    qty = int(req.qty or 1)
    if qty <= 0:
        raise HTTPException(status_code=400, detail="INVALID_QTY")

    pool = await get_pool()

    # беремо item
    async with pool.acquire() as conn:
        row_item = await conn.fetchrow(
            """
            SELECT
              id AS item_id,
              name,
              stats,
              base_value,
              sell_price,
              category
            FROM items
            WHERE id=$1 AND category='food' AND COALESCE(is_active, TRUE)=TRUE
            """,
            item_id,
        )
        if not row_item:
            raise HTTPException(status_code=404, detail="FOOD_NOT_FOUND")

    # ефект потрібен (щоб не продавати "порожню" їжу)
    hp_restore, mp_restore = _extract_restore_from_stats(row_item["stats"])
    if hp_restore <= 0 and mp_restore <= 0:
        raise HTTPException(status_code=400, detail="FOOD_HAS_NO_EFFECT")

    price_one = _compute_buy_price(row_item["base_value"], row_item["sell_price"])
    price_total = int(price_one * qty)

    # max stats (для UI)
    stats = await get_full_stats_for_player(tg_id)
    hp_max = int(stats.get("hp_max", 1))
    mp_max = int(stats.get("mp_max", 0))

    # списуємо гроші + додаємо їжу в інвентар (БЕЗ відновлення HP/MP)
    async with pool.acquire() as conn:
        async with conn.transaction():
            # lock гравця
            row_p = await conn.fetchrow(
                """
                SELECT
                  COALESCE(chervontsi,0) AS chervontsi,
                  COALESCE(hp,1) AS hp,
                  COALESCE(mp,0) AS mp
                FROM players
                WHERE tg_id=$1
                FOR UPDATE
                """,
                tg_id,
            )
            if not row_p:
                raise HTTPException(status_code=404, detail="PLAYER_NOT_FOUND")

            ch = int(row_p["chervontsi"] or 0)
            if ch < price_total:
                raise HTTPException(status_code=400, detail="NOT_ENOUGH_CHERVONTSI")

            hp_now = int(row_p["hp"] or 1)
            mp_now = int(row_p["mp"] or 0)
            ch_new = ch - price_total

            # 1) апдейт гравця (лише гроші)
            await conn.execute(
                """
                UPDATE players
                SET chervontsi=$2
                WHERE tg_id=$1
                """,
                tg_id,
                ch_new,
            )

            # 2) додати предмет (їжу) в інвентар як стек
            row_inv = await conn.fetchrow(
                """
                SELECT id, COALESCE(qty,0) AS qty
                FROM player_inventory
                WHERE tg_id=$1 AND item_id=$2 AND COALESCE(is_equipped,FALSE)=FALSE
                ORDER BY id ASC
                LIMIT 1
                FOR UPDATE
                """,
                tg_id,
                item_id,
            )

            if row_inv:
                inv_id = int(row_inv["id"])
                await conn.execute(
                    """
                    UPDATE player_inventory
                    SET qty = COALESCE(qty,0) + $2
                    WHERE id = $1
                    """,
                    inv_id,
                    qty,
                )
            else:
                await conn.execute(
                    """
                    INSERT INTO player_inventory (tg_id, item_id, qty, is_equipped)
                    VALUES ($1, $2, $3, FALSE)
                    """,
                    tg_id,
                    item_id,
                    qty,
                )

    # daily бонус
    daily_xp = 0
    daily_ch = 0
    daily_k = False
    daily_applied = False

    try:
        xp_gain, coins_gain, got_kleynod = await process_daily_login(tg_id)
        daily_xp = int(xp_gain or 0)
        daily_ch = int(coins_gain or 0)
        daily_k = bool(got_kleynod)
        daily_applied = (daily_xp > 0) or (daily_ch > 0) or daily_k
    except Exception as e:
        logger.exception(f"daily_login failed for food_buy tg_id={tg_id}: {e}")

    # фінальні числа
    async with pool.acquire() as conn:
        row2 = await conn.fetchrow(
            """
            SELECT COALESCE(chervontsi,0) AS chervontsi,
                   COALESCE(hp,1) AS hp,
                   COALESCE(mp,0) AS mp
            FROM players
            WHERE tg_id=$1
            """,
            tg_id,
        )
        ch_final = int(row2["chervontsi"] or 0) if row2 else 0
        hp_final = int(row2["hp"] or 1) if row2 else 1
        mp_final = int(row2["mp"] or 0) if row2 else 0

    return FoodBuyResponse(
        ok=True,
        item_id=int(row_item["item_id"]),
        item_name=str(row_item["name"]),
        qty=int(qty),
        price_total=int(price_total),
        # ✅ тепер віддаємо поточні hp/mp без змін
        hp=int(hp_final),
        hp_max=int(hp_max),
        mp=int(mp_final),
        mp_max=int(mp_max),
        chervontsi=int(ch_final),
        daily_applied=daily_applied,
        daily_xp=daily_xp,
        daily_chervontsi=daily_ch,
        daily_kleynod=daily_k,
    )