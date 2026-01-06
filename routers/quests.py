"""Quest API router for Kyrhanu.

This router exposes HTTP endpoints for starting a quest, viewing
quest details and advancing through quest stages.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# ✅ ABSOLUTE imports
from services.quest_engine import start_quest, get_player_stage, advance
from services.npc_quests import quests_json_for_npc, quest_json

router = APIRouter(prefix="/api/quests", tags=["quests"])


class QuestStartReq(BaseModel):
    tg_id: int


class QuestChoiceReq(BaseModel):
    tg_id: int
    choice_label: str


@router.get("/npc/{npc_key}")
async def npc_quests(npc_key: str):
    return {"quests": quests_json_for_npc(npc_key)}


@router.get("/detail/{quest_key}")
async def quest_detail(quest_key: str):
    q = quest_json(quest_key)
    if not q:
        raise HTTPException(404, "QUEST_NOT_FOUND")
    return q


@router.post("/start/{quest_key}")
async def quest_start(quest_key: str, req: QuestStartReq):
    start_quest(req.tg_id, quest_key)
    stage = get_player_stage(req.tg_id, quest_key)
    return {
        "quest_key": quest_key,
        "stage": stage.id,
        "text_lines": stage.text_lines,
        "choices": stage.choices,
        "is_final": stage.is_final,
    }


@router.post("/choice/{quest_key}")
async def quest_choice(quest_key: str, req: QuestChoiceReq):
    try:
        return await advance(req.tg_id, quest_key, req.choice_label)
    except ValueError as e:
        raise HTTPException(400, str(e))