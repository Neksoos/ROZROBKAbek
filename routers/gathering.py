# routers/gathering.py
from __future__ import annotations

import json
from typing import Optional, List, Dict, Any
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Header, Query
from pydantic import BaseModel
from loguru import logger

from services.gathering_tasks import (
    GatheringTask,
    GatheringAlreadyInProgress,
    GatheringTaskNotFound,
    GatheringNotReady,
    get_active_task,
    start_gathering_task,
    complete_gathering_task,
)

# ✅ якщо ти перейшов на новий rewards-сервіс
from services.rewards import distribute_drops

# ✅ для /api/gathering/state (story-flow в Redis)
from routers.redis_manager import get_redis

router = APIRouter(prefix="/api/gathering", tags=["gathering"])


# ─────────────────────────────────────────────
# initData -> tg_id
# ─────────────────────────────────────────────
def _tg_id_from_init_data(x_init_data: str | None) -> int:
    if not x_init_data or not x_init_data.strip():
        raise HTTPException(status_code=401, detail="Missing X-Init-Data")

    try:
        qs = parse_qs(x_init_data, keep_blank_values=True)
        user_raw = (qs.get("user") or [None])[0]
        if not user_raw:
            raise ValueError("user missing")

        user = json.loads(user_raw)
        tg_id = int(user.get("id"))
        return tg_id
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid X-Init-Data")


def _story_key(tg_id: int) -> str:
    # ⚠️ має співпадати з тим, що використовує gathering_story router
    return f"gather_story:{tg_id}"


# ─────────────────────────────────────────────
# DTO
# ─────────────────────────────────────────────
class GatheringTaskDTO(BaseModel):
    id: int
    tg_id: int
    area_key: str
    source_type: str
    started_at: str
    finishes_at: str
    seconds_left: int
    resolved: bool
    finished: bool
    result: Optional[Dict[str, Any]] = None


class GatheringStatusResponse(BaseModel):
    ok: bool
    task: Optional[GatheringTaskDTO] = None


class GatheringStartRequest(BaseModel):
    area_key: str
    source_type: str  # "ore" | "herb" | "stone"
    duration_minutes: Optional[int] = None
    risk: Optional[str] = None  # "low"|"medium"|"high"|"extreme" (опційно)


class GatheringStartResponse(BaseModel):
    ok: bool
    task: GatheringTaskDTO


class GatheringCompleteResponse(BaseModel):
    ok: bool
    task: GatheringTaskDTO
    drops: List[Dict[str, Any]]


class GatheringStateResponse(BaseModel):
    ok: bool = True
    active: bool
    story: Optional[Dict[str, Any]] = None
    area_key: Optional[str] = None
    profession_code: Optional[str] = None
    eta_seconds: Optional[int] = None


def _to_dto(task: GatheringTask) -> GatheringTaskDTO:
    return GatheringTaskDTO(
        id=task.id,
        tg_id=task.tg_id,
        area_key=task.area_key,
        source_type=task.source_type,
        started_at=task.started_at.isoformat(),
        finishes_at=task.finishes_at.isoformat(),
        seconds_left=task.seconds_left,
        resolved=task.resolved,
        finished=task.is_finished,
        result=task.result_json,
    )


# ─────────────────────────────────────────────
# API
# ─────────────────────────────────────────────
@router.get("/status", response_model=GatheringStatusResponse)
async def gathering_status(
    x_init_data: str | None = Header(default=None, alias="X-Init-Data"),
) -> GatheringStatusResponse:
    tg_id = _tg_id_from_init_data(x_init_data)
    task = await get_active_task(tg_id)
    return GatheringStatusResponse(ok=True, task=_to_dto(task) if task else None)


@router.get("/state", response_model=GatheringStateResponse)
async def gathering_state(
    x_init_data: str | None = Header(default=None, alias="X-Init-Data"),
    tg_id: Optional[int] = Query(default=None, description="(optional legacy) Telegram user id"),
) -> GatheringStateResponse:
    """
    ✅ endpoint для фронта: GET /api/gathering/state

    Повертає стан story-flow (інтерактивний похід), який зберігається в Redis.
    Якщо активної story нема або вона finished → active=false.
    """
    # пріоритет: initData; fallback: tg_id (щоб не ламати старі клієнти)
    if x_init_data and x_init_data.strip():
        real_tg_id = _tg_id_from_init_data(x_init_data)
    else:
        if tg_id is None:
            raise HTTPException(status_code=401, detail="Missing X-Init-Data")
        real_tg_id = int(tg_id)

    redis = await get_redis()
    raw = await redis.get(_story_key(real_tg_id))

    if not raw:
        return GatheringStateResponse(active=False, story=None)

    try:
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        story = json.loads(raw)
    except Exception:
        await redis.delete(_story_key(real_tg_id))
        return GatheringStateResponse(active=False, story=None)

    if story.get("finished"):
        return GatheringStateResponse(active=False, story=None)

    return GatheringStateResponse(
        active=True,
        story=story,
        area_key=story.get("area_key"),
        profession_code=story.get("profession_code") or story.get("source_type"),
        eta_seconds=story.get("eta_seconds"),
    )


@router.post("/start", response_model=GatheringStartResponse)
async def gathering_start(
    payload: GatheringStartRequest,
    x_init_data: str | None = Header(default=None, alias="X-Init-Data"),
) -> GatheringStartResponse:
    tg_id = _tg_id_from_init_data(x_init_data)

    area_key = payload.area_key
    source_type = payload.source_type
    duration = payload.duration_minutes or 10
    risk = payload.risk

    try:
        task = await start_gathering_task(
            tg_id=tg_id,
            area_key=area_key,
            source_type=source_type,
            duration_minutes=duration,
            risk=risk,
        )
    except GatheringAlreadyInProgress as e:
        logger.warning(f"gathering_start: already in progress tg_id={tg_id}: {e}")
        raise HTTPException(
            status_code=409,
            detail={
                "error": "ALREADY_IN_PROGRESS",
                "message": "У героя вже є активний похід на збір.",
            },
        )

    return GatheringStartResponse(ok=True, task=_to_dto(task))


@router.post("/complete", response_model=GatheringCompleteResponse)
async def gathering_complete(
    x_init_data: str | None = Header(default=None, alias="X-Init-Data"),
) -> GatheringCompleteResponse:
    tg_id = _tg_id_from_init_data(x_init_data)

    try:
        task, drops = await complete_gathering_task(tg_id)
    except GatheringTaskNotFound:
        raise HTTPException(
            status_code=404,
            detail={"error": "NO_ACTIVE_TASK", "message": "У героя немає активного походу на збір."},
        )
    except GatheringNotReady as e:
        raise HTTPException(
            status_code=400,
            detail={"error": "NOT_READY", "message": str(e)},
        )

    # ✅ кладемо дроп у інвентар / матеріали через rewards
    try:
        drops_payload: List[Dict[str, Any]] = [
            d.as_dict() if hasattr(d, "as_dict") else d for d in (drops or [])
        ]
        await distribute_drops(tg_id, drops_payload)
        drops = drops_payload
    except Exception as e:
        logger.error(f"gathering_complete: distribute_drops failed tg_id={tg_id}: {e}")

    return GatheringCompleteResponse(ok=True, task=_to_dto(task), drops=drops or [])