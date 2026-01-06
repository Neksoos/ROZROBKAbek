from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List

from data.world_data import AREAS, MOBS

router = APIRouter(prefix="/api/areas", tags=["areas"])


class AreaItem(BaseModel):
    key: str
    name: str
    min_level: int


class AreasResponse(BaseModel):
    areas: List[AreaItem]


class MobItem(BaseModel):
    id: int
    name: str
    level: int
    base_hp: int
    base_attack: int
    area_key: str


class MobListResponse(BaseModel):
    area_key: str
    area_name: str
    items: List[MobItem]


@router.get("", response_model=AreasResponse)
async def get_areas():
    return AreasResponse(
        areas=[AreaItem(**a) for a in AREAS]
    )


@router.get("/{area_key}/mobs", response_model=MobListResponse)
async def get_mobs(area_key: str):
    area = next((a for a in AREAS if a["key"] == area_key), None)
    if not area:
        raise HTTPException(404, "Area not found")

    raw = next((group for key, group in MOBS if key == area_key), [])

    items = [
        MobItem(
            id=m_id,
            name=name,
            level=level,
            base_hp=level * 50,
            base_attack=int(level * 2.5),
            area_key=area_key,
        )
        for (m_id, name, level) in raw
    ]

    return MobListResponse(
        area_key=area_key,
        area_name=area["name"],
        items=items,
    )