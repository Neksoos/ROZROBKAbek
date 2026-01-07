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
              cm.code,
              cm.name,
              cm.rarity,
              cm.profession,
              cm.source_type
            FROM player_materials pm
            JOIN craft_materials cm ON cm.id = pm.material_id
            WHERE pm.tg_id = $1
              AND pm.qty > 0
            ORDER BY
              cm.profession NULLS LAST,
              cm.source_type NULLS LAST,
              cm.rarity NULLS LAST,
              cm.name ASC
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
            # ⚠️ раніше тут була items.category; для craft_materials віддаємо profession
            category=r["profession"],
            icon=f"{str(r['code'])}.png" if r["code"] else None,
        )
        for r in rows
    ]

    return MaterialsResponse(ok=True, materials=materials)
