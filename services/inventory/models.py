# services/inventory/models.py
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class InventoryItem(BaseModel):
    id: int
    item_id: int
    item_code: str
    emoji: Optional[str]
    name: str
    description: Optional[str] = None
    rarity: Optional[str] = None
    slot: Optional[str] = None
    stats: Dict[str, Any]
    qty: int
    is_equipped: bool


class InventoryListResponse(BaseModel):
    items: List[InventoryItem]


class EquipRequest(BaseModel):
    tg_id: int


class UnequipSlotRequest(BaseModel):
    tg_id: int


class ConsumeRequest(BaseModel):
    tg_id: int
    qty: int = 1