# routers/area_mobs.py
from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List

# імпортуємо ЄДИНЕ джерело правди
from data.world_data import AREAS, AREAS_BY_KEY, MOBS_BY_AREA, calc_base_hp, calc_base_attack

router = APIRouter(prefix="/areas", tags=["areas"])


# ─────────────────────────────────────────────
# MODELS
# ─────────────────────────────────────────────

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
    is_training: bool


class MobListResponse(BaseModel):
    area_key: str
    area_name: str
    items: List[MobItem]


# ─────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────

@router.get("", response_model=AreasResponse)
async def get_areas():
    return AreasResponse(
        areas=[AreaItem(**a) for a in AREAS]
    )


@router.get("/{area_key}/mobs", response_model=MobListResponse)
async def get_mobs(area_key: str):
    if area_key not in AREAS_BY_KEY:
        raise HTTPException(404, "AREA_NOT_FOUND")

    area = AREAS_BY_KEY[area_key]

    raw_mobs = MOBS_BY_AREA.get(area_key, [])

    items = [
        MobItem(
            id=m["id"],
            name=m["name"],
            level=m["level"],
            base_hp=calc_base_hp(m["level"]),
            base_attack=calc_base_attack(m["level"]),
            area_key=m["area_key"],
            is_training=m.get("is_training", False)
        )
        for m in raw_mobs
    ]

    return MobListResponse(
        area_key=area_key,
        area_name=area["name"],
        items=items,
    )