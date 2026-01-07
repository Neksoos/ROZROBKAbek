from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from db import get_pool
from services.inventory.service import give_item_to_player  # ✅ FIX: правильний імпорт

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


# ... ДАЛІ ФАЙЛ БЕЗ ЗМІН ...
# (весь твій код нижче лишається 1-в-1, бо нам треба було тільки імпорт + фікс give_item_to_player)