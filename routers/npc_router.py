from __future__ import annotations

from typing import Optional, Any, Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import inspect

# ────────────────────────────────────────────────────────────────────
# Реальні сервіси NPC (мовлення + спавн)
# ────────────────────────────────────────────────────────────────────
try:
    # самі NPC (діалекти, регіони, тощо)
    from services.npc_defs import get_npc as _get_npc  # type: ignore

    # двигун фраз (слоти greet/offer/accept/reject/smalltalk/…)
    from services.npc_engine import (  # type: ignore
        quest_intro,
        quest_offer,
        quest_accept,
        quest_reject,
        quest_smalltalk,
        PlayerContext,
        maybe_pick_npc,
    )
except Exception:
    # Фоли, щоб бек не падав, якщо сервісів нема (наприклад, у тестах)
    async def _get_npc(npc_key: str):
        class DummyNpc:
            key = npc_key
            name = "Таємничий Мандрівник"
            region = "Полісся"
        return DummyNpc()

    def quest_intro(_npc):  # type: ignore
        return "— Дай-но хвильку, човнику. Маю ділечко."

    def quest_offer(_npc):  # type: ignore
        return "Потребую витривалої душі. Поможеш — не пожалуєш."

    def quest_accept(_npc):  # type: ignore
        return "— Домовились. Я позначив завдання у твоєму журналі."

    def quest_reject(_npc):  # type: ignore
        return "— Еге ж, не на кожну ношу плечі знайдеш. Колись іншим разом."

    def quest_smalltalk(_npc):  # type: ignore
        return "— Додаткова підказка: глянь у крамницю — знадобиться мотуззя."

    # заглушки під спавн
    class PlayerContext:  # type: ignore
        def __init__(self, uid: int, level: int, screen_key: str, hour: int):
            self.uid = uid
            self.level = level
            self.screen_key = screen_key
            self.hour = hour

    def maybe_pick_npc(_ctx: PlayerContext, *, force: bool = False):  # type: ignore
        return None


# ────────────────────────────────────────────────────────────────────
# Квести NPC (чисті дані)
# ────────────────────────────────────────────────────────────────────
try:
    from services.npc_quests import (  # type: ignore
        quests_json_for_npc,
        quest_json,
    )
except Exception:
    def quests_json_for_npc(_npc_key: str) -> List[Dict[str, Any]]:  # type: ignore
        return []

    def quest_json(_quest_key: str) -> Optional[Dict[str, Any]]:  # type: ignore
        return None


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────

async def _get_npc_safe(npc_key: str):
    """
    _get_npc може бути sync або async (через try/except fallback).
    Цей хелпер повертає NPC-обʼєкт в обох випадках.
    """
    res = _get_npc(npc_key)
    if inspect.isawaitable(res):
        return await res
    return res


# ────────────────────────────────────────────────────────────────────
# Pydantic-схеми
# ────────────────────────────────────────────────────────────────────

class EncounterRequest(BaseModel):
    tg_id: int


class ResultRequest(BaseModel):
    tg_id: int


class TipRequest(BaseModel):
    tg_id: int


class NPCInfo(BaseModel):
    key: str
    name: str
    region: Optional[str] = None


class EncounterResponse(BaseModel):
    npc: NPCInfo
    greet: str
    offer: str


class ResultResponse(BaseModel):
    ok: bool = True
    message: str


class TipResponse(BaseModel):
    tip: str


class QuestsResponse(BaseModel):
    quests: List[Dict[str, Any]]


class QuestResponse(BaseModel):
    quest: Dict[str, Any]


# ── Спавн рандомного NPC (брама для maybe_pick_npc) ────────────────

class SpawnRequest(BaseModel):
    tg_id: int
    level: int
    screen_key: str
    hour: Optional[int] = None


class SpawnNPC(BaseModel):
    key: str
    name: str
    region: Optional[str] = None
    tags: List[str]
    accent_notes: Optional[str] = None


class SpawnResponse(BaseModel):
    ok: bool = True
    npc: Optional[SpawnNPC] = None


# ────────────────────────────────────────────────────────────────────
# FastAPI router
# ────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/npc", tags=["npc"])


@router.post("/spawn", response_model=SpawnResponse)
async def npc_spawn(req: SpawnRequest):
    import time as _time

    hour = req.hour
    if hour is None:
        hour = int(_time.localtime().tm_hour)

    ctx = PlayerContext(
        uid=req.tg_id,
        level=req.level,
        screen_key=req.screen_key,
        hour=int(hour),
    )

    data = maybe_pick_npc(ctx)
    if not data:
        return SpawnResponse(ok=True, npc=None)

    npc = SpawnNPC(
        key=str(data.get("key")),
        name=str(data.get("name")),
        region=data.get("region"),
        tags=list(data.get("tags") or []),
        accent_notes=data.get("accent_notes"),
    )
    return SpawnResponse(ok=True, npc=npc)


@router.post("/{npc_key}/encounter", response_model=EncounterResponse)
async def npc_encounter(npc_key: str, req: EncounterRequest):
    npc = await _get_npc_safe(npc_key)
    if not npc:
        raise HTTPException(status_code=404, detail="NPC_NOT_FOUND")

    greet = quest_intro(npc)
    offer = quest_offer(npc)

    return EncounterResponse(
        npc=NPCInfo(
            key=str(getattr(npc, "key", npc_key)),
            name=str(getattr(npc, "name", "Незнайомець")),
            region=getattr(npc, "region", None),
        ),
        greet=greet,
        offer=offer,
    )


@router.post("/{npc_key}/accept", response_model=ResultResponse)
async def npc_accept(npc_key: str, req: ResultRequest):
    npc = await _get_npc_safe(npc_key)
    if not npc:
        raise HTTPException(status_code=404, detail="NPC_NOT_FOUND")

    msg = quest_accept(npc)
    return ResultResponse(message=msg or "Прийнято.")


@router.post("/{npc_key}/decline", response_model=ResultResponse)
async def npc_decline(npc_key: str, req: ResultRequest):
    npc = await _get_npc_safe(npc_key)
    if not npc:
        raise HTTPException(status_code=404, detail="NPC_NOT_FOUND")

    msg = quest_reject(npc)
    return ResultResponse(message=msg or "Відмовився.")


@router.post("/{npc_key}/more", response_model=TipResponse)
async def npc_more(npc_key: str, req: TipRequest):
    npc = await _get_npc_safe(npc_key)
    if not npc:
        raise HTTPException(status_code=404, detail="NPC_NOT_FOUND")

    tip = quest_smalltalk(npc)
    return TipResponse(tip=tip or "— Та що там ще казати…")


@router.get("/{npc_key}/quests", response_model=QuestsResponse)
async def npc_quests(npc_key: str):
    npc = await _get_npc_safe(npc_key)
    if not npc:
        raise HTTPException(status_code=404, detail="NPC_NOT_FOUND")

    quests = quests_json_for_npc(npc_key)
    return QuestsResponse(quests=quests)


@router.get("/quest/{quest_key}", response_model=QuestResponse)
async def npc_quest_detail(quest_key: str):
    q = quest_json(quest_key)
    if not q:
        raise HTTPException(status_code=404, detail="QUEST_NOT_FOUND")
    return QuestResponse(quest=q)