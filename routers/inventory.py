from __future__ import annotations

from fastapi import APIRouter, Query

from services.inventory.models import (
    ConsumeRequest,
    EquipRequest,
    InventoryItem,
    InventoryListResponse,
    UnequipSlotRequest,
)

# re-export з service
from services.inventory.service import (
    give_item_to_player,
    consume,
    equip,
    get_item,
    list_inventory,
    unequip,
    unequip_slot,
)

router = APIRouter(prefix="/api/inventory", tags=["inventory"])


# ─────────────────────────────
# equip / unequip
# ─────────────────────────────
@router.post("/equip/{inv_id}")
async def equip_item(inv_id: int, req: EquipRequest):
    return await equip(inv_id, req)


@router.post("/unequip/{inv_id}")
async def unequip_item(inv_id: int, req: EquipRequest):
    return await unequip(inv_id, req)


@router.post("/unequip-slot/{slot}")
async def unequip_slot_item(slot: str, req: UnequipSlotRequest):
    return await unequip_slot(slot, req)


# ─────────────────────────────
# list / get
# ─────────────────────────────
@router.get("", response_model=InventoryListResponse)
async def list_inventory_api(tg_id: int = Query(...)):
    return await list_inventory(tg_id)


@router.get("/{inv_id}", response_model=InventoryItem)
async def get_item_api(inv_id: int, tg_id: int = Query(...)):
    return await get_item(inv_id, tg_id)


# ─────────────────────────────
# consume
# ─────────────────────────────
@router.post("/consume/{inv_id}")
async def consume_api(inv_id: int, req: ConsumeRequest):
    return await consume(inv_id, req)