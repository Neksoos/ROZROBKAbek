from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel

from services.daily_login import process_daily_login  # type: ignore


router = APIRouter(prefix="/api/daily-login", tags=["daily-login"])


# tg_id: беремо або з X-Tg-Id, або з query (?tg_id=), або з request.state (через middleware)
async def get_tg_id(
    x_tg_id: Optional[str] = Header(default=None, alias="X-Tg-Id"),
    tg_id_q: Optional[int] = Query(default=None, alias="tg_id"),
) -> int:
    if tg_id_q is not None and int(tg_id_q) > 0:
        return int(tg_id_q)

    if x_tg_id:
        try:
            v = int(x_tg_id)
            if v > 0:
                return v
        except Exception:
            pass

    raise HTTPException(status_code=401, detail="Missing tg id")


class DailyLoginResponse(BaseModel):
    xp_gain: int
    coins_gain: int
    got_kleynod: bool


@router.post("/claim", response_model=DailyLoginResponse)
async def claim_daily_login(tg_id: int = Depends(get_tg_id)) -> DailyLoginResponse:
    xp_gain, coins_gain, got_kleynod = await process_daily_login(tg_id)
    return DailyLoginResponse(
        xp_gain=int(xp_gain or 0),
        coins_gain=int(coins_gain or 0),
        got_kleynod=bool(got_kleynod),
    )