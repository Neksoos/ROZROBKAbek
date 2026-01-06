# routers/night_watch_api.py
from __future__ import annotations
import os
from fastapi import APIRouter, Header, HTTPException
from typing import Optional
from services import night_watch as nw

router = APIRouter(prefix="/api/nightwatch", tags=["nightwatch"])

@router.get("/leaderboard")
async def leaderboard(limit: int = 10):
    return {"items": await nw.get_week_leaderboard(limit=limit)}

@router.post("/finalize")
async def finalize(x_admin_token: Optional[str] = Header(None)):
    token = os.getenv("NIGHT_WATCH_ADMIN_TOKEN")
    if not token or x_admin_token != token:
        raise HTTPException(403, "FORBIDDEN")
    winners = await nw.finalize_current_week()
    return {"ok": True, "winners": winners}