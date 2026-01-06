from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db import get_pool

router = APIRouter(prefix="/api/materials", tags=["materials"])


class MaterialDTO(BaseModel):
    material_id: int
    code: str
    name: str
    qty: int
    rarity: Optional[str] = None
    category: Optional[str] = None
    icon: Optional[str] = None


class MaterialsResponse(BaseModel):
    ok: bool = True
    materials: List[MaterialDTO]


@router.get("", response_model=MaterialsResponse)
async def get_materials(tg_id: int) -> MaterialsResponse:
    if tg_id <= 0:
        raise HTTPException(400, "INVALID_TG_ID")

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
              pm.material_id,
              pm.qty,
              i.code,
              i.name,
              i.rarity,
              i.category
            FROM player_materials pm
            JOIN items i ON i.id = pm.material_id
            WHERE pm.tg_id = $1
              AND pm.qty > 0
            ORDER BY
              i.category NULLS LAST,
              i.rarity NULLS LAST,
              i.name ASC
            """,
            tg_id,
        )

    materials = [
        MaterialDTO(
            material_id=int(r["material_id"]),
            code=str(r["code"]),
            name=str(r["name"]),
            qty=int(r["qty"] or 0),
            rarity=r["rarity"],
            category=r["category"],
            icon=f"{str(r['code'])}.png" if r["code"] else None,
        )
        for r in rows
    ]

    return MaterialsResponse(ok=True, materials=materials)