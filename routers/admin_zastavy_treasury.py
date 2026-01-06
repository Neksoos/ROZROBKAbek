from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel

from config import settings
from db import get_pool
from services.fort_treasury import (
    get_zastava_treasury,
    get_zastava_treasury_log,
    change_zastava_treasury,
)

router = APIRouter(prefix="/admin/zastavy", tags=["admin_zastavy_treasury"])


# ---- спільна перевірка адмін-токена -------------------------------


async def verify_admin_token(x_admin_token: Optional[str] = Header(None)) -> None:
    if not x_admin_token:
        raise HTTPException(status_code=401, detail={"error": "NO_ADMIN_TOKEN"})

    if x_admin_token != settings.ADMIN_SECRET:
        raise HTTPException(status_code=401, detail={"error": "INVALID_ADMIN_TOKEN"})


# ---- Pydantic-моделі ----------------------------------------------


class TreasuryState(BaseModel):
    zastava_id: int
    chervontsi: int
    kleynody: int
    updated_at: Optional[str]


class TreasuryStateResponse(BaseModel):
    ok: bool = True
    treasury: TreasuryState


class TreasuryLogItem(BaseModel):
    id: int
    zastava_id: int
    tg_id: int
    delta_chervontsi: int
    delta_kleynody: int
    action: str
    source: str
    comment: Optional[str] = None
    created_at: str


class TreasuryLogResponse(BaseModel):
    ok: bool = True
    items: List[TreasuryLogItem]


class TreasuryChangeRequest(BaseModel):
    # хто ініціював операцію; для адмінки можна ставити 0 або свій tg_id
    actor_tg_id: int = 0

    # зміна балансів (можуть бути відʼємні)
    delta_chervontsi: int = 0
    delta_kleynody: int = 0

    # службові мітки
    action: str = "MANUAL"
    source: str = "ADMIN"
    comment: Optional[str] = None


class TreasuryChangeResponse(BaseModel):
    ok: bool = True
    treasury: TreasuryState


# ---- Ендпойнти ----------------------------------------------------


@router.get(
    "/{zastava_id}/treasury",
    response_model=TreasuryStateResponse,
)
async def api_get_zastava_treasury(
    zastava_id: int,
    _: None = Depends(verify_admin_token),
):
    """
    Поточний стан казни обраної застави.
    """
    state_raw = await get_zastava_treasury(zastava_id)
    state = TreasuryState(
        zastava_id=state_raw["zastava_id"],
        chervontsi=state_raw["chervontsi"],
        kleynody=state_raw["kleynody"],
        updated_at=state_raw["updated_at"].isoformat()
        if state_raw["updated_at"]
        else None,
    )
    return TreasuryStateResponse(ok=True, treasury=state)


@router.get(
    "/{zastava_id}/treasury/log",
    response_model=TreasuryLogResponse,
)
async def api_get_zastava_treasury_log(
    zastava_id: int,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: None = Depends(verify_admin_token),
):
    """
    Історія казни застави з пагінацією.
    """
    rows = await get_zastava_treasury_log(
        zastava_id=zastava_id,
        limit=limit,
        offset=offset,
    )

    items: List[TreasuryLogItem] = []
    for r in rows:
        items.append(
            TreasuryLogItem(
                id=r["id"],
                zastava_id=r["zastava_id"],
                tg_id=r["tg_id"],
                delta_chervontsi=r["delta_chervontsi"],
                delta_kleynody=r["delta_kleynody"],
                action=r["action"],
                source=r["source"],
                comment=r["comment"],
                created_at=r["created_at"].isoformat(),
            )
        )

    return TreasuryLogResponse(ok=True, items=items)


@router.post(
    "/{zastava_id}/treasury/change",
    response_model=TreasuryChangeResponse,
)
async def api_change_zastava_treasury(
    zastava_id: int,
    body: TreasuryChangeRequest,
    _: None = Depends(verify_admin_token),
):
    """
    Ручна зміна казни з адмінки (видача/зняття, бонуси, штрафи).
    """
    if body.delta_chervontsi == 0 and body.delta_kleynody == 0:
        raise HTTPException(
            status_code=400,
            detail={"error": "NO_DELTA", "message": "Немає змін по балансах"},
        )

    state_raw = await change_zastava_treasury(
        zastava_id=zastava_id,
        tg_id=body.actor_tg_id,
        delta_chervontsi=body.delta_chervontsi,
        delta_kleynody=body.delta_kleynody,
        action=body.action,
        source=body.source,
        comment=body.comment,
    )

    state = TreasuryState(
        zastava_id=state_raw["zastava_id"],
        chervontsi=state_raw["chervontsi"],
        kleynody=state_raw["kleynody"],
        updated_at=state_raw["updated_at"].isoformat()
        if state_raw["updated_at"]
        else None,
    )

    return TreasuryChangeResponse(ok=True, treasury=state)