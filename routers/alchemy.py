from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from db import get_pool
from services.inventory.service import give_item_to_player  # ✅ FIX: правильний імпорт

router = APIRouter(prefix="/api/alchemy", tags=["alchemy"])

DRYING_SECONDS = 30 * 60
DRYING_SLOTS = 5


# ─────────────────────────────────────────────
# DTO
# ─────────────────────────────────────────────

class IngredientDTO(BaseModel):
    material_code: str
    qty: int
    role: str


class RecipeDTO(BaseModel):
    code: str
    name: str
    prof_key: str
    level_req: int
    brew_time_sec: int
    output_item_code: str
    output_amount: int
    ingredients: List[IngredientDTO]


class BrewRequest(BaseModel):
    tg_id: int
    recipe_code: str


class BrewResponse(BaseModel):
    queue_id: int
    recipe_code: str
    status: str
    started_at: datetime
    finish_at: datetime
    seconds_left: int
    output_item_code: str
    output_amount: int


class QueueDTO(BaseModel):
    id: int
    tg_id: int
    recipe_code: str
    status: str
    started_at: datetime
    finish_at: datetime
    seconds_left: int
    output_item_code: Optional[str] = None
    output_amount: int = 1


class MissingDTO(BaseModel):
    material_code: str
    need: int
    have: int
    missing: int
    role: str


class RecipeStatusDTO(BaseModel):
    recipe: RecipeDTO
    can_brew: bool
    missing: List[MissingDTO]


class HerbInvDTO(BaseModel):
    item_code: str
    name: str
    emoji: Optional[str] = None
    category: str
    amount: int


class DryingSlotDTO(BaseModel):
    slot_index: int
    tg_id: int
    input_item_code: Optional[str] = None
    input_amount: int = 0
    output_material_code: Optional[str] = None
    output_amount: int = 0
    started_at: Optional[datetime] = None
    finish_at: Optional[datetime] = None
    seconds_left: int = 0
    status: str = "empty"  # "empty" | "drying" | "done"


class DryingStartRequest(BaseModel):
    tg_id: int = Field(..., gt=0)
    slot_index: int = Field(..., ge=0, lt=DRYING_SLOTS)
    item_code: str
    amount: int = Field(1, gt=0)


# ─────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────

def _seconds_left(finish_at: datetime) -> int:
    now = datetime.now(timezone.utc)
    if finish_at.tzinfo is None:
        finish_at = finish_at.replace(tzinfo=timezone.utc)
    return max(0, int((finish_at - now).total_seconds()))


async def _ensure_queue_columns():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE player_alchemy_queue ADD COLUMN IF NOT EXISTS output_item_code text;"
        )
        await conn.execute(
            "ALTER TABLE player_alchemy_queue ADD COLUMN IF NOT EXISTS output_amount int NOT NULL DEFAULT 1;"
        )


async def _ensure_drying_table():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS player_alchemy_drying (
                tg_id                bigint NOT NULL,
                slot_index           int    NOT NULL,
                input_item_code      text   NOT NULL,
                input_amount         int    NOT NULL DEFAULT 1,
                output_material_code text   NOT NULL,
                output_amount        int    NOT NULL DEFAULT 1,
                status               text   NOT NULL DEFAULT 'drying',
                started_at           timestamptz NOT NULL,
                finish_at            timestamptz NOT NULL,
                created_at           timestamptz NOT NULL DEFAULT now(),
                updated_at           timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (tg_id, slot_index)
            );
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_player_alchemy_drying_tg ON player_alchemy_drying(tg_id);"
        )


def _map_herb_item_to_dried_material(item_code: str) -> str:
    if not item_code.startswith("herb_"):
        raise HTTPException(400, "ONLY_HERB_ITEMS_ALLOWED")
    return "alch_dried_" + item_code[len("herb_"):]


async def _item_code_to_id(conn, item_code: str) -> Optional[int]:
    row = await conn.fetchrow("SELECT id FROM items WHERE code=$1", item_code)
    return int(row["id"]) if row else None


async def _material_code_to_id(conn, material_code: str) -> Optional[int]:
    row = await conn.fetchrow("SELECT id FROM craft_materials WHERE code=$1", material_code)
    return int(row["id"]) if row else None


async def _material_code_to_id_map(conn) -> Dict[str, int]:
    rows = await conn.fetch("SELECT id, code FROM craft_materials")
    return {r["code"]: int(r["id"]) for r in rows}


async def _player_materials_map(conn, tg_id: int) -> Dict[int, int]:
    rows = await conn.fetch(
        "SELECT material_id, qty FROM player_materials WHERE tg_id=$1",
        tg_id,
    )
    return {int(r["material_id"]): int(r["qty"]) for r in rows}


def _calc_missing_for_recipe(
    recipe: RecipeDTO,
    code_to_id: Dict[str, int],
    have_by_id: Dict[int, int],
) -> Tuple[bool, List[MissingDTO]]:
    missing: List[MissingDTO] = []
    for ing in recipe.ingredients:
        mid = code_to_id.get(ing.material_code)
        if not mid:
            missing.append(
                MissingDTO(
                    material_code=ing.material_code,
                    need=int(ing.qty),
                    have=0,
                    missing=int(ing.qty),
                    role=ing.role,
                )
            )
            continue

        have = int(have_by_id.get(mid, 0))
        need = int(ing.qty)
        miss = max(0, need - have)
        if miss > 0:
            missing.append(
                MissingDTO(
                    material_code=ing.material_code,
                    need=need,
                    have=have,
                    missing=miss,
                    role=ing.role,
                )
            )
    return (len(missing) == 0), missing


async def _load_all_recipes_with_ingredients(conn) -> List[RecipeDTO]:
    rows = await conn.fetch(
        """
        SELECT code, name, prof_key, level_req, brew_time_sec, output_item_code, output_amount
        FROM alchemy_recipes
        ORDER BY prof_key, level_req, name
        """
    )

    ing = await conn.fetch(
        """
        SELECT recipe_code, material_code, qty, role
        FROM alchemy_recipe_ingredients
        ORDER BY recipe_code, role, material_code
        """
    )

    by_recipe: Dict[str, List[IngredientDTO]] = {}
    for x in ing:
        by_recipe.setdefault(x["recipe_code"], []).append(
            IngredientDTO(
                material_code=x["material_code"],
                qty=int(x["qty"]),
                role=x["role"],
            )
        )

    return [
        RecipeDTO(
            code=r["code"],
            name=r["name"],
            prof_key=r["prof_key"],
            level_req=int(r["level_req"]),
            brew_time_sec=int(r["brew_time_sec"]),
            output_item_code=r["output_item_code"],
            output_amount=int(r["output_amount"]),
            ingredients=by_recipe.get(r["code"], []),
        )
        for r in rows
    ]


async def _load_recipe_row(conn, recipe_code: str) -> dict:
    r = await conn.fetchrow(
        """
        SELECT code, name, prof_key, level_req, brew_time_sec, output_item_code, output_amount
        FROM alchemy_recipes
        WHERE code = $1
        """,
        recipe_code,
    )
    if not r:
        raise HTTPException(404, "RECIPE_NOT_FOUND")

    ing = await conn.fetch(
        """
        SELECT material_code, qty, role
        FROM alchemy_recipe_ingredients
        WHERE recipe_code = $1
        ORDER BY role, material_code
        """,
        recipe_code,
    )

    return {"recipe": dict(r), "ingredients": [dict(x) for x in ing]}


async def _check_and_consume_materials(conn, tg_id: int, ingredients: List[dict]):
    resolved = []
    for it in ingredients:
        mid = await _material_code_to_id(conn, it["material_code"])
        if not mid:
            raise HTTPException(400, f"MATERIAL_NOT_FOUND:{it['material_code']}")
        resolved.append((mid, it["material_code"], int(it["qty"]), it["role"]))

    for mid, mcode, need_qty, _role in resolved:
        row = await conn.fetchrow(
            "SELECT qty FROM player_materials WHERE tg_id=$1 AND material_id=$2",
            tg_id,
            mid,
        )
        have = int(row["qty"]) if row else 0
        if have < need_qty:
            raise HTTPException(400, f"NOT_ENOUGH_MATERIAL:{mcode}:{have}/{need_qty}")

    for mid, _mcode, need_qty, _role in resolved:
        await conn.execute(
            """
            UPDATE player_materials
            SET qty = qty - $3, updated_at = now()
            WHERE tg_id=$1 AND material_id=$2
            """,
            tg_id,
            mid,
            need_qty,
        )
        await conn.execute(
            "DELETE FROM player_materials WHERE tg_id=$1 AND material_id=$2 AND qty <= 0",
            tg_id,
            mid,
        )


# ✅ інвентар тут працює через player_inventory.qty (а не amount)
async def _consume_inventory_item(conn, tg_id: int, item_code: str, amount: int):
    if amount <= 0:
        return

    item_id = await _item_code_to_id(conn, item_code)
    if not item_id:
        raise HTTPException(404, "ITEM_NOT_FOUND")

    rows = await conn.fetch(
        """
        SELECT id, qty
        FROM player_inventory
        WHERE tg_id=$1 AND item_id=$2 AND is_equipped=FALSE
        ORDER BY created_at ASC, id ASC
        FOR UPDATE
        """,
        tg_id,
        int(item_id),
    )

    have_total = sum(int(r["qty"] or 0) for r in rows)
    if have_total < amount:
        raise HTTPException(400, f"NOT_ENOUGH_ITEMS:{item_code}:{have_total}/{amount}")

    remaining = amount
    for r in rows:
        if remaining <= 0:
            break
        inv_id = int(r["id"])
        q = int(r["qty"] or 0)
        if q <= 0:
            continue

        take = min(q, remaining)
        new_q = q - take

        if new_q <= 0:
            await conn.execute("DELETE FROM player_inventory WHERE id=$1", inv_id)
        else:
            await conn.execute(
                "UPDATE player_inventory SET qty=$2, updated_at=NOW() WHERE id=$1",
                inv_id,
                new_q,
            )

        remaining -= take

    if remaining > 0:
        raise HTTPException(500, "INVENTORY_DEDUCT_FAILED")


async def _add_inventory_item(conn, tg_id: int, item_code: str, amount: int):
    if amount <= 0:
        return

    item_id = await _item_code_to_id(conn, item_code)
    if not item_id:
        raise HTTPException(404, "ITEM_NOT_FOUND")

    # надійно: просто додаємо рядок (не залежить від partial unique index)
    await conn.execute(
        """
        INSERT INTO player_inventory(tg_id, item_id, qty, is_equipped, slot, created_at, updated_at)
        VALUES ($1,$2,$3,FALSE,NULL,NOW(),NOW())
        """,
        tg_id,
        int(item_id),
        int(amount),
    )


# ─────────────────────────────────────────────
# recipes / queue / brew / claim
# ─────────────────────────────────────────────

@router.get("/recipes", response_model=List[RecipeDTO])
async def list_recipes():
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await _load_all_recipes_with_ingredients(conn)


@router.get("/recipes/status", response_model=List[RecipeStatusDTO])
async def recipes_status(tg_id: int = Query(..., gt=0)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        recipes = await _load_all_recipes_with_ingredients(conn)
        code_to_id = await _material_code_to_id_map(conn)
        have_by_id = await _player_materials_map(conn, tg_id)

    out: List[RecipeStatusDTO] = []
    for r in recipes:
        can, miss = _calc_missing_for_recipe(r, code_to_id, have_by_id)
        out.append(RecipeStatusDTO(recipe=r, can_brew=can, missing=miss))
    return out


@router.get("/queue", response_model=List[QueueDTO])
async def get_queue(tg_id: int = Query(..., gt=0)):
    await _ensure_queue_columns()
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, tg_id, recipe_code, status, started_at, finish_at, output_item_code, output_amount
            FROM player_alchemy_queue
            WHERE tg_id = $1
            ORDER BY id DESC
            """,
            tg_id,
        )

    out: List[QueueDTO] = []
    for r in rows:
        sec = _seconds_left(r["finish_at"])
        status = r["status"]
        if sec <= 0 and status == "brewing":
            status = "done"

        out.append(
            QueueDTO(
                id=int(r["id"]),
                tg_id=int(r["tg_id"]),
                recipe_code=r["recipe_code"],
                status=status,
                started_at=r["started_at"],
                finish_at=r["finish_at"],
                seconds_left=sec,
                output_item_code=r["output_item_code"],
                output_amount=int(r["output_amount"] or 1),
            )
        )
    return out


@router.post("/brew", response_model=BrewResponse)
async def brew(req: BrewRequest):
    await _ensure_queue_columns()

    pool = await get_pool()
    async with pool.acquire() as conn:
        data = await _load_recipe_row(conn, req.recipe_code)
        recipe = data["recipe"]
        ingredients = data["ingredients"]

        now = datetime.now(timezone.utc)
        finish = now + timedelta(seconds=int(recipe["brew_time_sec"]))

        async with conn.transaction():
            await _check_and_consume_materials(conn, req.tg_id, ingredients)

            row = await conn.fetchrow(
                """
                INSERT INTO player_alchemy_queue(
                    tg_id, recipe_code, status, started_at, finish_at, output_item_code, output_amount, meta
                )
                VALUES ($1,$2,'brewing',$3,$4,$5,$6,'{}'::jsonb)
                RETURNING id, tg_id, recipe_code, status, started_at, finish_at, output_item_code, output_amount
                """,
                req.tg_id,
                req.recipe_code,
                now,
                finish,
                recipe["output_item_code"],
                int(recipe["output_amount"]),
            )

    sec = _seconds_left(row["finish_at"])
    return BrewResponse(
        queue_id=int(row["id"]),
        recipe_code=row["recipe_code"],
        status=row["status"],
        started_at=row["started_at"],
        finish_at=row["finish_at"],
        seconds_left=sec,
        output_item_code=row["output_item_code"],
        output_amount=int(row["output_amount"]),
    )


@router.post("/claim/{queue_id}")
async def claim(queue_id: int, tg_id: int = Query(..., gt=0)):
    await _ensure_queue_columns()

    pool = await get_pool()
    async with pool.acquire() as conn:
        q = await conn.fetchrow(
            """
            SELECT id, tg_id, recipe_code, status, started_at, finish_at, output_item_code, output_amount
            FROM player_alchemy_queue
            WHERE id=$1 AND tg_id=$2
            """,
            queue_id,
            tg_id,
        )
        if not q:
            raise HTTPException(404, "QUEUE_NOT_FOUND")

        if _seconds_left(q["finish_at"]) > 0:
            raise HTTPException(400, "BREW_NOT_FINISHED")

        item = await conn.fetchrow(
            "SELECT code, name, category, emoji, rarity, description, stats, slot FROM items WHERE code=$1",
            q["output_item_code"],
        )
        if not item:
            raise HTTPException(400, f"OUTPUT_ITEM_NOT_FOUND:{q['output_item_code']}")

        async with conn.transaction():
            # ✅ FIX: give_item_to_player приймає тільки kwargs (tg_id має бути keyword)
            # ✅ FIX: використовуємо qty (не amount), але response лишаємо як було
            await give_item_to_player(
                tg_id=tg_id,
                item_code=item["code"],
                name=item["name"],
                category=item["category"],
                emoji=item["emoji"],
                rarity=item["rarity"],
                description=item["description"],
                stats=item["stats"] if isinstance(item["stats"], dict) else None,
                qty=int(q["output_amount"] or 1),
                slot=item["slot"],
            )

            await conn.execute(
                "DELETE FROM player_alchemy_queue WHERE id=$1 AND tg_id=$2",
                queue_id,
                tg_id,
            )

    return {"ok": True, "item_code": item["code"], "amount": int(q["output_amount"] or 1)}


# ─────────────────────────────────────────────
# herbs + drying
# ─────────────────────────────────────────────

@router.get("/herbs", response_model=List[HerbInvDTO])
async def list_player_herbs(tg_id: int = Query(..., gt=0)):
    """
    ✅ Беремо трави за category з таблиці items.
    ✅ Інвентар: player_inventory має item_id і qty.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
              i.code     AS item_code,
              i.name     AS name,
              i.emoji    AS emoji,
              i.category AS category,
              pi.qty     AS amount
            FROM player_inventory pi
            JOIN items i ON i.id = pi.item_id
            WHERE pi.tg_id = $1
              AND i.category LIKE 'herb_%'
              AND pi.qty > 0
              AND pi.is_equipped = FALSE
            ORDER BY i.category, i.name
            """,
            tg_id,
        )

    return [
        HerbInvDTO(
            item_code=r["item_code"],
            name=r["name"],
            emoji=r["emoji"],
            category=r["category"],
            amount=int(r["amount"]),
        )
        for r in rows
    ]


@router.get("/drying", response_model=List[DryingSlotDTO])
async def get_drying(tg_id: int = Query(..., gt=0)):
    await _ensure_drying_table()

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT tg_id, slot_index, input_item_code, input_amount, output_material_code, output_amount,
                   status, started_at, finish_at
            FROM player_alchemy_drying
            WHERE tg_id=$1
            ORDER BY slot_index ASC
            """,
            tg_id,
        )

    by_slot: Dict[int, dict] = {int(r["slot_index"]): dict(r) for r in rows}
    out: List[DryingSlotDTO] = []

    for slot in range(DRYING_SLOTS):
        r = by_slot.get(slot)
        if not r:
            out.append(DryingSlotDTO(slot_index=slot, tg_id=tg_id, status="empty"))
            continue

        sec = _seconds_left(r["finish_at"])
        status = r["status"]
        if sec <= 0 and status == "drying":
            status = "done"

        out.append(
            DryingSlotDTO(
                slot_index=slot,
                tg_id=int(r["tg_id"]),
                input_item_code=r["input_item_code"],
                input_amount=int(r["input_amount"]),
                output_material_code=r["output_material_code"],
                output_amount=int(r["output_amount"]),
                started_at=r["started_at"],
                finish_at=r["finish_at"],
                seconds_left=sec,
                status=status,
            )
        )

    return out


@router.post("/drying/start", response_model=DryingSlotDTO)
async def start_drying(req: DryingStartRequest):
    await _ensure_drying_table()

    pool = await get_pool()
    async with pool.acquire() as conn:
        item = await conn.fetchrow(
            "SELECT code, category FROM items WHERE code=$1",
            req.item_code,
        )
        if not item:
            raise HTTPException(404, "ITEM_NOT_FOUND")

        # ✅ “трави” визначаємо по category
        if not str(item["category"]).startswith("herb_"):
            raise HTTPException(400, "ONLY_HERBS_ALLOWED")

        out_code = _map_herb_item_to_dried_material(req.item_code)

        # craft material має існувати
        mid = await _material_code_to_id(conn, out_code)
        if not mid:
            raise HTTPException(400, f"DRIED_MATERIAL_NOT_FOUND:{out_code}")

        now = datetime.now(timezone.utc)
        finish = now + timedelta(seconds=DRYING_SECONDS)

        async with conn.transaction():
            existing = await conn.fetchrow(
                "SELECT status FROM player_alchemy_drying WHERE tg_id=$1 AND slot_index=$2",
                req.tg_id,
                req.slot_index,
            )
            if existing and str(existing["status"]) in ("drying", "done"):
                raise HTTPException(400, "SLOT_ALREADY_USED")

            # ✅ списання з інвентарю (qty)
            await _consume_inventory_item(conn, req.tg_id, req.item_code, int(req.amount))

            row = await conn.fetchrow(
                """
                INSERT INTO player_alchemy_drying(
                    tg_id, slot_index, input_item_code, input_amount,
                    output_material_code, output_amount,
                    status, started_at, finish_at, updated_at
                )
                VALUES ($1,$2,$3,$4,$5,$6,'drying',$7,$8, now())
                ON CONFLICT (tg_id, slot_index)
                DO UPDATE SET
                    input_item_code=EXCLUDED.input_item_code,
                    input_amount=EXCLUDED.input_amount,
                    output_material_code=EXCLUDED.output_material_code,
                    output_amount=EXCLUDED.output_amount,
                    status='drying',
                    started_at=EXCLUDED.started_at,
                    finish_at=EXCLUDED.finish_at,
                    updated_at=now()
                RETURNING tg_id, slot_index, input_item_code, input_amount, output_material_code, output_amount,
                          status, started_at, finish_at
                """,
                req.tg_id,
                req.slot_index,
                req.item_code,
                int(req.amount),
                out_code,
                int(req.amount),
                now,
                finish,
            )

    sec = _seconds_left(row["finish_at"])
    return DryingSlotDTO(
        slot_index=int(row["slot_index"]),
        tg_id=int(row["tg_id"]),
        input_item_code=row["input_item_code"],
        input_amount=int(row["input_amount"]),
        output_material_code=row["output_material_code"],
        output_amount=int(row["output_amount"]),
        started_at=row["started_at"],
        finish_at=row["finish_at"],
        seconds_left=sec,
        status="drying" if sec > 0 else "done",
    )


@router.post("/drying/claim/{slot_index}")
async def claim_drying(slot_index: int, tg_id: int = Query(..., gt=0)):
    await _ensure_drying_table()

    if slot_index < 0 or slot_index >= DRYING_SLOTS:
        raise HTTPException(400, "BAD_SLOT_INDEX")

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT tg_id, slot_index, output_material_code, output_amount, finish_at
            FROM player_alchemy_drying
            WHERE tg_id=$1 AND slot_index=$2
            """,
            tg_id,
            slot_index,
        )
        if not row:
            raise HTTPException(404, "SLOT_EMPTY")

        if _seconds_left(row["finish_at"]) > 0:
            raise HTTPException(400, "DRYING_NOT_FINISHED")

        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO player_materials(tg_id, material_id, qty, created_at, updated_at)
                VALUES ($1,$2,$3, now(), now())
                ON CONFLICT (tg_id, material_id)
                DO UPDATE SET qty = player_materials.qty + EXCLUDED.qty, updated_at = now()
                """,
                int(row["tg_id"]),
                int(await _material_code_to_id(conn, row["output_material_code"])),
                int(row["output_amount"]),
            )

            await conn.execute(
                "DELETE FROM player_alchemy_drying WHERE tg_id=$1 AND slot_index=$2",
                tg_id,
                slot_index,
            )

    return {"ok": True, "material_code": row["output_material_code"], "qty": int(row["output_amount"])}


@router.post("/drying/cancel/{slot_index}")
async def cancel_drying(slot_index: int, tg_id: int = Query(..., gt=0)):
    await _ensure_drying_table()

    if slot_index < 0 or slot_index >= DRYING_SLOTS:
        raise HTTPException(400, "BAD_SLOT_INDEX")

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT tg_id, slot_index, input_item_code, input_amount
            FROM player_alchemy_drying
            WHERE tg_id=$1 AND slot_index=$2
            """,
            tg_id,
            slot_index,
        )
        if not row:
            raise HTTPException(404, "SLOT_EMPTY")

        async with conn.transaction():
            # ✅ повернення в інвентар (qty)
            await _add_inventory_item(
                conn,
                tg_id=int(row["tg_id"]),
                item_code=row["input_item_code"],
                amount=int(row["input_amount"]),
            )

            await conn.execute(
                "DELETE FROM player_alchemy_drying WHERE tg_id=$1 AND slot_index=$2",
                tg_id,
                slot_index,
            )

    return {"ok": True}