# services/battle/models.py
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class Mob(BaseModel):
    code: str
    name: str
    hp: int
    hp_max: int
    level: int

    phys_attack: int
    magic_attack: int
    phys_defense: int
    magic_defense: int

    atk_legacy: Optional[int] = Field(None, alias="atk")

    class Config:
        allow_population_by_field_name = True
        extra = "allow"


class Hero(BaseModel):
    name: str
    hp: int
    hp_max: int
    mp: int
    mp_max: int

    phys_attack: int
    magic_attack: int
    phys_defense: int
    magic_defense: int

    atk: int
    def_: int
    def_legacy: Optional[int] = Field(None, alias="def")

    energy: int
    energy_max: int

    class Config:
        allow_population_by_field_name = True
        extra = "allow"


class BattleDTO(BaseModel):
    id: int
    state: str
    turn: int
    area_key: str
    mob: Mob
    hero: Hero
    note: str
    loot: List[str]


class BattleStartRequest(BaseModel):
    mob_id: int


class BattleActionRequest(BaseModel):
    battle_id: Optional[int] = None
    mode: Optional[str] = None  # ✅ "hp" | "mp" (optional, default = auto)