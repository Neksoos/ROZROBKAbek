# routers/admin_notify.py
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from routers.admin_guard import require_admin
from services.notifications import (
    send_broadcast_to_all,
    send_reengagement_to_inactive,
)

router = APIRouter(
    prefix="/admin/notify",
    tags=["admin-notify"],
)


class NotifyAllDTO(BaseModel):
    text: str
    limit: int | None = None


class NotifyInactiveDTO(BaseModel):
    text: str
    days_inactive: int = 3
    limit: int | None = None


@router.post("/all")
async def notify_all(
    dto: NotifyAllDTO,
    _admin=Depends(require_admin),
):
    """
    Розсилка всім гравцям.
    """
    sent = await send_broadcast_to_all(dto.text, dto.limit)
    return {"ok": True, "sent": sent}


@router.post("/inactive")
async def notify_inactive(
    dto: NotifyInactiveDTO,
    _admin=Depends(require_admin),
):
    """
    Розсилка тим, хто не заходив X днів.
    """
    sent = await send_reengagement_to_inactive(
        dto.text,
        dto.days_inactive,
        dto.limit,
    )
    return {"ok": True, "sent": sent}