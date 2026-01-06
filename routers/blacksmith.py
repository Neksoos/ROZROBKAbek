from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from db import get_pool
from routers.inventory import give_item_to_player

router = APIRouter(prefix="/api/blacksmith", tags=["blacksmith"])


# ─────────────────────────────────────────────
# DTO
# ─────────────────────────────────────────────

class IngredientDTO(BaseModel):
    # ⚠️ лишаю назву поля як було (material_code),
    # але тепер це item_code з таблиці items
    material_code: str
    qty: int
    role: str = "metal"


class RecipeDTO(BaseModel):
    code: str
    name: str
    slot: str
    level_req: int

    forge_hits: int = 60
    base_progress_per_hit: float = 0.0166667
    heat_sensitivity: float = 0.65
    rhythm_window_ms: Tuple[int, int] = (120, 220)

    output_item_code: str
    output_amount: int = 1
    ingredients: List[IngredientDTO]


class MissingDTO(BaseModel):
    material_code: str
    need: int
    have: int
    missing: int
    role: str


class RecipeStatusDTO(BaseModel):
    recipe: RecipeDTO
    can_forge: bool
    missing: List[MissingDTO]


class ForgeStartBody(BaseModel):
    recipe_code: str = Field(..., min_length=1)


class ForgeStartResponse(BaseModel):
    forge_id: int
    recipe_code: str

    required_hits: int
    base_progress_per_hit: float
    heat_sensitivity: float
    rhythm_window_ms: Tuple[int, int]


class ForgeClaimBody(BaseModel):
    forge_id: int
    recipe_code: str
    client_report: Optional[dict] = None


class ForgeClaimResponse(BaseModel):
    ok: bool = True
    item_code: str
    amount: int


# ✅ Smelting DTOs (руда → злитки)
class SmeltRecipeDTO(BaseModel):
    code: str
    name: str
    output_item_code: str
    output_amount: int = 1
    ingredients: List[IngredientDTO]


class SmeltStatusDTO(BaseModel):
    recipe: SmeltRecipeDTO
    can_smelt: bool
    missing: List[MissingDTO]


class SmeltStartBody(BaseModel):
    recipe_code: str = Field(..., min_length=1)


class SmeltStartResponse(BaseModel):
    ok: bool = True
    recipe_code: str
    item_code: str
    amount: int


# ─────────────────────────────────────────────
# DB ensure
# ─────────────────────────────────────────────

async def _ensure_blacksmith_tables() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS blacksmith_recipes (
                code                  text PRIMARY KEY,
                name                  text NOT NULL,
                slot                  text NOT NULL,
                level_req             int  NOT NULL DEFAULT 1,

                forge_hits            int  NOT NULL DEFAULT 60,
                base_progress_per_hit double precision NOT NULL DEFAULT 0.0166667,
                heat_sensitivity      double precision NOT NULL DEFAULT 0.65,
                rhythm_min_ms         int  NOT NULL DEFAULT 120,
                rhythm_max_ms         int  NOT NULL DEFAULT 220,

                output_item_code      text NOT NULL,
                output_amount         int  NOT NULL DEFAULT 1,

                created_at            timestamptz NOT NULL DEFAULT now(),
                updated_at            timestamptz NOT NULL DEFAULT now()
            );
            """
        )

        # ⚠️ лишаємо назву колонки material_code, але тепер це item_code
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS blacksmith_recipe_ingredients (
                recipe_code   text NOT NULL REFERENCES blacksmith_recipes(code) ON DELETE CASCADE,
                material_code text NOT NULL,
                qty           int  NOT NULL DEFAULT 1,
                role          text NOT NULL DEFAULT 'metal',
                PRIMARY KEY (recipe_code, material_code, role)
            );
            """
        )

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS player_blacksmith_forge (
                id                    bigserial PRIMARY KEY,
                tg_id                 bigint NOT NULL,
                recipe_code           text NOT NULL REFERENCES blacksmith_recipes(code),
                status                text NOT NULL DEFAULT 'started', -- started|claimed|cancelled
                started_at            timestamptz NOT NULL DEFAULT now(),
                claimed_at            timestamptz NULL,

                required_hits         int NOT NULL,
                base_progress_per_hit double precision NOT NULL,
                heat_sensitivity      double precision NOT NULL,
                rhythm_min_ms         int NOT NULL,
                rhythm_max_ms         int NOT NULL
            );
            """
        )

        # ✅ smelting recipes (в цьому ж роутері)
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS blacksmith_smelt_recipes (
                code             text PRIMARY KEY,
                name             text NOT NULL,
                output_item_code text NOT NULL,
                output_amount    int  NOT NULL DEFAULT 1,
                created_at       timestamptz NOT NULL DEFAULT now(),
                updated_at       timestamptz NOT NULL DEFAULT now()
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS blacksmith_smelt_ingredients (
                recipe_code   text NOT NULL REFERENCES blacksmith_smelt_recipes(code) ON DELETE CASCADE,
                material_code text NOT NULL, -- item_code
                qty           int  NOT NULL DEFAULT 1,
                role          text NOT NULL DEFAULT 'ore',
                PRIMARY KEY (recipe_code, material_code, role)
            );
            """
        )

        await conn.execute("CREATE INDEX IF NOT EXISTS idx_bsmith_recipes_slot ON blacksmith_recipes(slot);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_bsmith_forge_tg ON player_blacksmith_forge(tg_id, status);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_bsmith_smelt ON blacksmith_smelt_recipes(code);")


async def _seed_blacksmith_demo_if_empty() -> None:
    """Демо-рецепти кування + плавки, щоб сторінка вже працювала. Потім можеш прибрати."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # forge recipes
        row = await conn.fetchrow("SELECT count(*) AS c FROM blacksmith_recipes")
        if not row or int(row["c"] or 0) == 0:
            await conn.execute(
                """
                INSERT INTO blacksmith_recipes
                  (code, name, slot, level_req, forge_hits, base_progress_per_hit,
                   heat_sensitivity, rhythm_min_ms, rhythm_max_ms, output_item_code, output_amount)
                VALUES
                  ('smith_knife_iron_1', 'Залізний ніж ремісника', 'weapon', 1, 45, 1.0/45.0, 0.65, 120, 220, 'knife_iron_01', 1),
                  ('smith_helm_iron_2',  'Клепаний шолом',         'helmet', 2, 80, 1.0/80.0, 0.68, 120, 220, 'helm_iron_01',  1),
                  ('smith_chest_iron_3', 'Нагрудник із лускою',    'armor',  3, 140, 1.0/140.0,0.72, 120, 220, 'chest_iron_01', 1)
                ;
                """
            )
            await conn.execute(
                """
                INSERT INTO blacksmith_recipe_ingredients (recipe_code, material_code, qty, role)
                VALUES
                  ('smith_knife_iron_1', 'smith_ingot_zalizna', 2, 'metal'),
                  ('smith_knife_iron_1', 'leather_strip', 1, 'binding'),

                  ('smith_helm_iron_2', 'smith_ingot_zalizna', 4, 'metal'),
                  ('smith_helm_iron_2', 'rivet_set', 1, 'fasteners'),

                  ('smith_chest_iron_3', 'smith_ingot_zalizna', 8, 'metal'),
                  ('smith_chest_iron_3', 'leather_strip', 2, 'binding')
                ;
                """
            )

        # smelt recipes
        srow = await conn.fetchrow("SELECT count(*) AS c FROM blacksmith_smelt_recipes")
        if not srow or int(srow["c"] or 0) == 0:
            await conn.execute(
                """
                INSERT INTO blacksmith_smelt_recipes (code, name, output_item_code, output_amount)
                VALUES
                  ('smelt_fuel_1', 'Паливо: ковалівське вугілля', 'smith_fuel_vuhilna_zhyla', 1),
                  ('smelt_iron_1', 'Плавка: залізний чушок', 'smith_ingot_zalizna', 1),
                  ('smelt_copper_1', 'Плавка: мідний злиток', 'smith_ingot_midna', 1),
                  ('smelt_kryt_1', 'Плавка: крицевий злиток', 'smith_ingot_krytcovyi', 1),
                  ('smelt_silverstone_1', 'Плавка: срібний злиток', 'smith_ingot_sriblokamin', 1)
                ;
                """
            )
            await conn.execute(
                """
                INSERT INTO blacksmith_smelt_ingredients (recipe_code, material_code, qty, role)
                VALUES
                  ('smelt_fuel_1', 'ore_vuhilna_zhyla', 2, 'ore'),

                  ('smelt_iron_1', 'ore_ruda_zalizna', 3, 'ore'),
                  ('smelt_iron_1', 'smith_fuel_vuhilna_zhyla', 1, 'fuel'),

                  ('smelt_copper_1', 'ore_midna_zhyla', 3, 'ore'),
                  ('smelt_copper_1', 'smith_fuel_vuhilna_zhyla', 1, 'fuel'),

                  ('smelt_kryt_1', 'ore_krytcovyi_kamin', 4, 'ore'),
                  ('smelt_kryt_1', 'smith_fuel_vuhilna_zhyla', 1, 'fuel'),

                  ('smelt_silverstone_1', 'ore_sriblokamin', 4, 'ore'),
                  ('smelt_silverstone_1', 'smith_fuel_vuhilna_zhyla', 1, 'fuel')
                ;
                """
            )


# ─────────────────────────────────────────────
# helpers (INVENTORY)
# ─────────────────────────────────────────────

async def _player_inventory_qty_by_code(conn, tg_id: int) -> Dict[str, int]:
    rows = await conn.fetch(
        """
        SELECT i.code AS code, COALESCE(SUM(pi.qty), 0)::int AS qty
        FROM player_inventory pi
        JOIN items i ON i.id = pi.item_id
        WHERE pi.tg_id = $1
          AND pi.is_equipped = FALSE
        GROUP BY i.code
        """,
        tg_id,
    )
    return {str(r["code"]): int(r["qty"] or 0) for r in (rows or [])}


def _calc_missing_inventory(
    ingredients: List[IngredientDTO],
    have_by_code: Dict[str, int],
) -> Tuple[bool, List[MissingDTO]]:
    missing: List[MissingDTO] = []
    for ing in ingredients:
        code = str(ing.material_code)
        need = int(ing.qty)
        have = int(have_by_code.get(code, 0))
        miss = max(0, need - have)
        if miss > 0:
            missing.append(
                MissingDTO(
                    material_code=code,
                    need=need,
                    have=have,
                    missing=miss,
                    role=str(ing.role),
                )
            )
    return (len(missing) == 0), missing


async def _deduct_inventory_items(conn, tg_id: int, ingredients: List[IngredientDTO]) -> None:
    have_by_code = await _player_inventory_qty_by_code(conn, tg_id)
    ok, miss = _calc_missing_inventory(ingredients, have_by_code)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail={"code": "NOT_ENOUGH_ITEMS", "missing": [m.dict() for m in miss]},
        )

    # Списання по кожному item_code: спочатку зі стеків, потім з одиночних
    for ing in ingredients:
        code = str(ing.material_code)
        need = int(ing.qty)
        if need <= 0:
            continue

        # знайти item_id
        item_id = await conn.fetchval("SELECT id FROM items WHERE code=$1", code)
        if not item_id:
            raise HTTPException(400, "ITEM_CODE_NOT_FOUND")

        # беремо всі рядки інвентаря з цим item_id (не екіп), від старих до нових
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

        remaining = need
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
            # shouldn't happen because we prechecked
            raise HTTPException(500, "INVENTORY_DEDUCT_FAILED")


async def _load_forge_recipes(conn) -> List[RecipeDTO]:
    rrows = await conn.fetch(
        """
        SELECT
          code, name, slot, level_req,
          forge_hits, base_progress_per_hit, heat_sensitivity,
          rhythm_min_ms, rhythm_max_ms,
          output_item_code, output_amount
        FROM blacksmith_recipes
        ORDER BY slot, level_req, name
        """
    )
    irows = await conn.fetch(
        """
        SELECT recipe_code, material_code, qty, role
        FROM blacksmith_recipe_ingredients
        ORDER BY recipe_code, role, material_code
        """
    )

    ings_by: Dict[str, List[IngredientDTO]] = {}
    for r in irows:
        code = str(r["recipe_code"])
        ings_by.setdefault(code, []).append(
            IngredientDTO(
                material_code=str(r["material_code"]),
                qty=int(r["qty"]),
                role=str(r["role"]),
            )
        )

    out: List[RecipeDTO] = []
    for r in rrows:
        code = str(r["code"])
        hits = int(r["forge_hits"] or 60)
        base = float(r["base_progress_per_hit"] or (1.0 / max(1, hits)))
        out.append(
            RecipeDTO(
                code=code,
                name=str(r["name"]),
                slot=str(r["slot"]),
                level_req=int(r["level_req"] or 1),
                forge_hits=hits,
                base_progress_per_hit=base,
                heat_sensitivity=float(r["heat_sensitivity"] or 0.65),
                rhythm_window_ms=(int(r["rhythm_min_ms"] or 120), int(r["rhythm_max_ms"] or 220)),
                output_item_code=str(r["output_item_code"]),
                output_amount=int(r["output_amount"] or 1),
                ingredients=ings_by.get(code, []),
            )
        )
    return out


async def _load_smelt_recipes(conn) -> List[SmeltRecipeDTO]:
    rrows = await conn.fetch(
        """
        SELECT code, name, output_item_code, output_amount
        FROM blacksmith_smelt_recipes
        ORDER BY name
        """
    )
    irows = await conn.fetch(
        """
        SELECT recipe_code, material_code, qty, role
        FROM blacksmith_smelt_ingredients
        ORDER BY recipe_code, role, material_code
        """
    )
    ings_by: Dict[str, List[IngredientDTO]] = {}
    for r in irows:
        code = str(r["recipe_code"])
        ings_by.setdefault(code, []).append(
            IngredientDTO(
                material_code=str(r["material_code"]),
                qty=int(r["qty"]),
                role=str(r["role"]),
            )
        )

    out: List[SmeltRecipeDTO] = []
    for r in rrows:
        code = str(r["code"])
        out.append(
            SmeltRecipeDTO(
                code=code,
                name=str(r["name"]),
                output_item_code=str(r["output_item_code"]),
                output_amount=int(r["output_amount"] or 1),
                ingredients=ings_by.get(code, []),
            )
        )
    return out


async def _get_item_meta(conn, item_code: str) -> Dict[str, Optional[str]]:
    row = await conn.fetchrow(
        "SELECT code, name, category, emoji, rarity, description, slot FROM items WHERE code=$1",
        item_code,
    )
    if not row:
        # fallback minimal
        return {
            "name": item_code,
            "category": "mat",
            "emoji": None,
            "rarity": None,
            "description": None,
            "slot": None,
        }
    return {
        "name": str(row["name"]),
        "category": row["category"],
        "emoji": row["emoji"],
        "rarity": row["rarity"],
        "description": row["description"],
        "slot": row["slot"],
    }


# ─────────────────────────────────────────────
# endpoints: SMELT
# ─────────────────────────────────────────────

@router.get("/smelt/recipes/status", response_model=List[SmeltStatusDTO])
async def smelt_recipes_status(tg_id: int) -> List[SmeltStatusDTO]:
    if tg_id <= 0:
        raise HTTPException(400, "INVALID_TG_ID")

    await _ensure_blacksmith_tables()
    await _seed_blacksmith_demo_if_empty()

    pool = await get_pool()
    async with pool.acquire() as conn:
        recipes = await _load_smelt_recipes(conn)
        have = await _player_inventory_qty_by_code(conn, tg_id)

    out: List[SmeltStatusDTO] = []
    for r in recipes:
        ok, miss = _calc_missing_inventory(r.ingredients, have)
        out.append(SmeltStatusDTO(recipe=r, can_smelt=ok, missing=miss))
    return out


@router.post("/smelt/start", response_model=SmeltStartResponse)
async def smelt_start(tg_id: int, body: SmeltStartBody) -> SmeltStartResponse:
    if tg_id <= 0:
        raise HTTPException(400, "INVALID_TG_ID")

    await _ensure_blacksmith_tables()
    await _seed_blacksmith_demo_if_empty()

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            rr = await conn.fetchrow(
                """
                SELECT output_item_code, output_amount
                FROM blacksmith_smelt_recipes
                WHERE code=$1
                """,
                body.recipe_code,
            )
            if not rr:
                raise HTTPException(404, "SMELT_RECIPE_NOT_FOUND")

            irows = await conn.fetch(
                """
                SELECT material_code, qty, role
                FROM blacksmith_smelt_ingredients
                WHERE recipe_code=$1
                """,
                body.recipe_code,
            )
            ingredients = [
                IngredientDTO(
                    material_code=str(x["material_code"]),
                    qty=int(x["qty"]),
                    role=str(x["role"]),
                )
                for x in irows
            ]

            # deduct upfront (проти дюпу)
            await _deduct_inventory_items(conn, tg_id, ingredients)

            item_code = str(rr["output_item_code"])
            amount = int(rr["output_amount"] or 1)
            meta = await _get_item_meta(conn, item_code)

        # ⚠️ give_item_to_player бере свій коннект, тому викликаємо ПІСЛЯ транзакції
        await give_item_to_player(
            tg_id,
            item_code=item_code,
            name=meta["name"] or item_code,
            category=meta["category"],
            emoji=meta["emoji"],
            rarity=meta["rarity"],
            description=meta["description"],
            qty=amount,
            slot=meta["slot"],
        )

    return SmeltStartResponse(ok=True, recipe_code=body.recipe_code, item_code=item_code, amount=amount)


# ─────────────────────────────────────────────
# endpoints: FORGE
# ─────────────────────────────────────────────

@router.get("/recipes/status", response_model=List[RecipeStatusDTO])
async def recipes_status(tg_id: int) -> List[RecipeStatusDTO]:
    if tg_id <= 0:
        raise HTTPException(400, "INVALID_TG_ID")

    await _ensure_blacksmith_tables()
    await _seed_blacksmith_demo_if_empty()

    pool = await get_pool()
    async with pool.acquire() as conn:
        recipes = await _load_forge_recipes(conn)
        have = await _player_inventory_qty_by_code(conn, tg_id)

    out: List[RecipeStatusDTO] = []
    for r in recipes:
        ok, miss = _calc_missing_inventory(r.ingredients, have)
        out.append(RecipeStatusDTO(recipe=r, can_forge=ok, missing=miss))
    return out


@router.post("/forge/start", response_model=ForgeStartResponse)
async def forge_start(tg_id: int, body: ForgeStartBody) -> ForgeStartResponse:
    if tg_id <= 0:
        raise HTTPException(400, "INVALID_TG_ID")

    await _ensure_blacksmith_tables()
    await _seed_blacksmith_demo_if_empty()

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            r = await conn.fetchrow(
                """
                SELECT
                  code, forge_hits, base_progress_per_hit, heat_sensitivity,
                  rhythm_min_ms, rhythm_max_ms
                FROM blacksmith_recipes
                WHERE code=$1
                """,
                body.recipe_code,
            )
            if not r:
                raise HTTPException(404, "RECIPE_NOT_FOUND")

            irows = await conn.fetch(
                """
                SELECT material_code, qty, role
                FROM blacksmith_recipe_ingredients
                WHERE recipe_code=$1
                """,
                body.recipe_code,
            )
            ingredients = [
                IngredientDTO(
                    material_code=str(x["material_code"]),
                    qty=int(x["qty"]),
                    role=str(x["role"]),
                )
                for x in irows
            ]

            # ✅ deduct upfront from INVENTORY
            await _deduct_inventory_items(conn, tg_id, ingredients)

            started_at = datetime.now(timezone.utc)
            hits = int(r["forge_hits"] or 60)
            base = float(r["base_progress_per_hit"] or (1.0 / max(1, hits)))

            row = await conn.fetchrow(
                """
                INSERT INTO player_blacksmith_forge(
                  tg_id, recipe_code, status, started_at,
                  required_hits, base_progress_per_hit, heat_sensitivity, rhythm_min_ms, rhythm_max_ms
                )
                VALUES($1,$2,'started',$3,$4,$5,$6,$7,$8)
                RETURNING id
                """,
                tg_id,
                body.recipe_code,
                started_at,
                hits,
                base,
                float(r["heat_sensitivity"] or 0.65),
                int(r["rhythm_min_ms"] or 120),
                int(r["rhythm_max_ms"] or 220),
            )
            forge_id = int(row["id"])

    return ForgeStartResponse(
        forge_id=forge_id,
        recipe_code=body.recipe_code,
        required_hits=int(r["forge_hits"] or 60),
        base_progress_per_hit=float(r["base_progress_per_hit"] or (1.0 / max(1, int(r["forge_hits"] or 60)))),
        heat_sensitivity=float(r["heat_sensitivity"] or 0.65),
        rhythm_window_ms=(int(r["rhythm_min_ms"] or 120), int(r["rhythm_max_ms"] or 220)),
    )


@router.post("/forge/claim", response_model=ForgeClaimResponse)
async def forge_claim(tg_id: int, body: ForgeClaimBody) -> ForgeClaimResponse:
    if tg_id <= 0:
        raise HTTPException(400, "INVALID_TG_ID")

    await _ensure_blacksmith_tables()

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            f = await conn.fetchrow(
                """
                SELECT id, tg_id, recipe_code, status
                FROM player_blacksmith_forge
                WHERE id=$1
                FOR UPDATE
                """,
                body.forge_id,
            )
            if not f:
                raise HTTPException(404, "FORGE_NOT_FOUND")
            if int(f["tg_id"]) != tg_id:
                raise HTTPException(403, "FORGE_NOT_YOURS")
            if str(f["status"]) != "started":
                raise HTTPException(400, "FORGE_NOT_ACTIVE")
            if str(f["recipe_code"]) != body.recipe_code:
                raise HTTPException(400, "RECIPE_MISMATCH")

            rr = await conn.fetchrow(
                """
                SELECT output_item_code, output_amount
                FROM blacksmith_recipes
                WHERE code=$1
                """,
                body.recipe_code,
            )
            if not rr:
                raise HTTPException(404, "RECIPE_NOT_FOUND")

            item_code = str(rr["output_item_code"])
            amount = int(rr["output_amount"] or 1)
            meta = await _get_item_meta(conn, item_code)

            await conn.execute(
                """
                UPDATE player_blacksmith_forge
                   SET status='claimed',
                       claimed_at=now()
                 WHERE id=$1
                """,
                body.forge_id,
            )

    # ⚠️ видачу робимо після транзакції (give_item_to_player бере інший коннект)
    await give_item_to_player(
        tg_id,
        item_code=item_code,
        name=meta["name"] or item_code,
        category=meta["category"],
        emoji=meta["emoji"],
        rarity=meta["rarity"],
        description=meta["description"],
        qty=amount,
        slot=meta["slot"],
    )

    return ForgeClaimResponse(ok=True, item_code=item_code, amount=amount)