from __future__ import annotations

import json
from typing import List, Literal, Optional
from urllib.parse import parse_qs

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from db import get_pool


# ───────────────────────────────────────
# РОУТЕРИ
# ───────────────────────────────────────
# Основний (як ти і хочеш)
router = APIRouter(prefix="/api/professions", tags=["professions"])

# Дзеркальний (якщо десь префікси/проксі з’їхали)
router_public = APIRouter(prefix="/professions", tags=["professions"])


# ───────────────────────────────────────
# ЦІНИ
# ───────────────────────────────────────
SECOND_PROFESSION_COST_KLEY = 200   # друга професія
CHANGE_PROFESSION_COST_KLEY = 350   # скинути професію / змінити

MAX_GATHERING_PROFESSIONS = 2
MAX_CRAFT_PROFESSIONS = 2
MAX_TOTAL_PROFESSIONS = 2


# ───────────────────────────────────────
# headers -> tg_id
# ───────────────────────────────────────
def _tg_id_from_headers(x_init_data: str | None, x_tg_id: str | None) -> int:
    # 1) простий хедер (ваш фронт саме так і шле)
    if x_tg_id and x_tg_id.strip():
        try:
            return int(x_tg_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid X-Tg-Id")

    # 2) Telegram WebApp initData (fallback)
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


# ───────────────────────────────────────
# DTO
# ───────────────────────────────────────
Kind = Literal["gathering", "craft"]


class ProfessionDTO(BaseModel):
    id: int
    code: str
    name: str
    descr: str
    kind: Kind
    min_level: int
    icon: Optional[str] = None


class PlayerProfessionDTO(BaseModel):
    profession: ProfessionDTO
    level: int
    xp: int


class ProfessionsMeResponse(BaseModel):
    ok: bool
    player_level: int
    professions: List[PlayerProfessionDTO]
    limits: dict
    costs: dict


class ChooseProfessionRequest(BaseModel):
    profession_code: str


class GenericResponse(BaseModel):
    ok: bool
    detail: str | None = None


# ───────────────────────────────────────
# helpers
# ───────────────────────────────────────
async def _get_player_by_tg(tg_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT
                id,
                COALESCE(level, 1)    AS level,
                COALESCE(kleynody, 0) AS kleynody
            FROM players
            WHERE tg_id = $1
            """,
            tg_id,
        )


async def _get_profession_by_code(code: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT id, code, name, descr, kind, min_level, icon
            FROM professions
            WHERE code = $1
            """,
            code,
        )


async def _get_player_professions(player_id: int) -> list[PlayerProfessionDTO]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                pp.level,
                pp.xp,
                pr.id,
                pr.code,
                pr.name,
                pr.descr,
                pr.kind,
                pr.min_level,
                pr.icon
            FROM player_professions pp
            JOIN professions pr ON pr.id = pp.profession_id
            WHERE pp.player_id = $1
            ORDER BY pr.kind, pr.min_level, pr.id
            """,
            player_id,
        )

    result: list[PlayerProfessionDTO] = []
    for r in rows:
        result.append(
            PlayerProfessionDTO(
                profession=ProfessionDTO(
                    id=r["id"],
                    code=r["code"],
                    name=r["name"],
                    descr=r["descr"],
                    kind=r["kind"],
                    min_level=r["min_level"],
                    icon=r["icon"],
                ),
                level=r["level"],
                xp=r["xp"],
            )
        )
    return result


async def _remove_profession_with_cost(
    *,
    player_id: int,
    player_kley: int,
    profession_code: str,
    cost: int,
):
    if player_kley < cost:
        raise HTTPException(
            status_code=400,
            detail=f"Недостатньо клейнодів (потрібно {cost}).",
        )

    prof = await _get_profession_by_code(profession_code)
    if not prof:
        raise HTTPException(status_code=404, detail="Profession not found")

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            updated = await conn.execute(
                """
                UPDATE players
                SET kleynody = kleynody - $1
                WHERE id = $2 AND kleynody >= $1
                """,
                cost,
                player_id,
            )
            if updated.endswith("0"):
                raise HTTPException(status_code=400, detail="Недостатньо клейнодів.")

            result = await conn.execute(
                """
                DELETE FROM player_professions
                WHERE player_id = $1 AND profession_id = $2
                """,
                player_id,
                prof["id"],
            )
            if result.endswith("0"):
                raise HTTPException(status_code=404, detail="У гравця немає такої професії.")


# ───────────────────────────────────────
# CORE handlers (щоб не дублювати логіку)
# ───────────────────────────────────────
async def _handle_list_professions():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, code, name, descr, kind, min_level, icon
            FROM professions
            ORDER BY kind, min_level, id
            """
        )

    return {
        "ok": True,
        "professions": [
            ProfessionDTO(
                id=r["id"],
                code=r["code"],
                name=r["name"],
                descr=r["descr"],
                kind=r["kind"],
                min_level=r["min_level"],
                icon=r["icon"],
            )
            for r in rows
        ],
    }


async def _handle_me(tg_id: int) -> ProfessionsMeResponse:
    player = await _get_player_by_tg(tg_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    player_id = int(player["id"])
    player_level = int(player["level"])

    profs = await _get_player_professions(player_id)

    gathering_count = sum(1 for p in profs if p.profession.kind == "gathering")
    craft_count = sum(1 for p in profs if p.profession.kind == "craft")

    limits = {
        "gathering": {"max": MAX_GATHERING_PROFESSIONS, "current": gathering_count},
        "craft": {"max": MAX_CRAFT_PROFESSIONS, "current": craft_count},
    }

    return ProfessionsMeResponse(
        ok=True,
        player_level=player_level,
        professions=profs,
        limits=limits,
        costs={"second": SECOND_PROFESSION_COST_KLEY, "change": CHANGE_PROFESSION_COST_KLEY},
    )


async def _handle_choose(tg_id: int, payload: ChooseProfessionRequest) -> GenericResponse:
    player = await _get_player_by_tg(tg_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    player_id = int(player["id"])
    player_kley = int(player["kleynody"])

    prof = await _get_profession_by_code(payload.profession_code)
    if not prof:
        raise HTTPException(status_code=404, detail="Profession not found")

    player_profs = await _get_player_professions(player_id)
    total_count = len(player_profs)

    if any(p.profession.code == prof["code"] for p in player_profs):
        return GenericResponse(ok=True, detail="Професія вже обрана.")

    if total_count >= MAX_TOTAL_PROFESSIONS:
        raise HTTPException(status_code=400, detail="Досягнуто максимум професій.")

    gathering_count = sum(1 for p in player_profs if p.profession.kind == "gathering")
    craft_count = sum(1 for p in player_profs if p.profession.kind == "craft")

    if prof["kind"] == "gathering" and gathering_count >= MAX_GATHERING_PROFESSIONS:
        raise HTTPException(status_code=400, detail="Досягнуто максимум збиральних професій.")
    if prof["kind"] == "craft" and craft_count >= MAX_CRAFT_PROFESSIONS:
        raise HTTPException(status_code=400, detail="Досягнуто максимум крафтових професій.")

    if total_count == 1 and player_kley < SECOND_PROFESSION_COST_KLEY:
        raise HTTPException(
            status_code=400,
            detail=f"Недостатньо клейнодів (потрібно {SECOND_PROFESSION_COST_KLEY}).",
        )

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            if total_count == 1:
                updated = await conn.execute(
                    """
                    UPDATE players
                    SET kleynody = kleynody - $1
                    WHERE id = $2 AND kleynody >= $1
                    """,
                    SECOND_PROFESSION_COST_KLEY,
                    player_id,
                )
                if updated.endswith("0"):
                    raise HTTPException(status_code=400, detail="Недостатньо клейнодів.")

            await conn.execute(
                """
                INSERT INTO player_professions (player_id, profession_id, level, xp)
                VALUES ($1, $2, 1, 0)
                """,
                player_id,
                prof["id"],
            )

    return GenericResponse(ok=True, detail="Професію обрано.")


async def _handle_abandon(tg_id: int, payload: ChooseProfessionRequest) -> GenericResponse:
    player = await _get_player_by_tg(tg_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    player_id = int(player["id"])
    player_kley = int(player["kleynody"])

    await _remove_profession_with_cost(
        player_id=player_id,
        player_kley=player_kley,
        profession_code=payload.profession_code,
        cost=CHANGE_PROFESSION_COST_KLEY,
    )
    return GenericResponse(ok=True, detail="Професію скинуто.")


# ───────────────────────────────────────
# /api/professions/*
# ───────────────────────────────────────
@router.get("", response_model=dict)
async def list_professions():
    return await _handle_list_professions()


@router.get("/me", response_model=ProfessionsMeResponse)
async def my_professions(
    x_init_data: str | None = Header(default=None, alias="X-Init-Data"),
    x_tg_id: str | None = Header(default=None, alias="X-Tg-Id"),
):
    tg_id = _tg_id_from_headers(x_init_data, x_tg_id)
    return await _handle_me(tg_id)


@router.post("/choose", response_model=GenericResponse)
async def choose_profession(
    payload: ChooseProfessionRequest,
    x_init_data: str | None = Header(default=None, alias="X-Init-Data"),
    x_tg_id: str | None = Header(default=None, alias="X-Tg-Id"),
):
    tg_id = _tg_id_from_headers(x_init_data, x_tg_id)
    return await _handle_choose(tg_id, payload)


@router.post("/abandon", response_model=GenericResponse)
async def abandon_profession(
    payload: ChooseProfessionRequest,
    x_init_data: str | None = Header(default=None, alias="X-Init-Data"),
    x_tg_id: str | None = Header(default=None, alias="X-Tg-Id"),
):
    tg_id = _tg_id_from_headers(x_init_data, x_tg_id)
    return await _handle_abandon(tg_id, payload)


@router.post("/change", response_model=GenericResponse)
async def change_profession(
    payload: ChooseProfessionRequest,
    x_init_data: str | None = Header(default=None, alias="X-Init-Data"),
    x_tg_id: str | None = Header(default=None, alias="X-Tg-Id"),
):
    tg_id = _tg_id_from_headers(x_init_data, x_tg_id)
    # "change" = те саме що abandon (як у тебе було)
    return await _handle_abandon(tg_id, payload)


# ───────────────────────────────────────
# /professions/* (дзеркало)
# ───────────────────────────────────────
@router_public.get("", response_model=dict)
async def list_professions_public():
    return await _handle_list_professions()


@router_public.get("/me", response_model=ProfessionsMeResponse)
async def my_professions_public(
    x_init_data: str | None = Header(default=None, alias="X-Init-Data"),
    x_tg_id: str | None = Header(default=None, alias="X-Tg-Id"),
):
    tg_id = _tg_id_from_headers(x_init_data, x_tg_id)
    return await _handle_me(tg_id)


@router_public.post("/choose", response_model=GenericResponse)
async def choose_profession_public(
    payload: ChooseProfessionRequest,
    x_init_data: str | None = Header(default=None, alias="X-Init-Data"),
    x_tg_id: str | None = Header(default=None, alias="X-Tg-Id"),
):
    tg_id = _tg_id_from_headers(x_init_data, x_tg_id)
    return await _handle_choose(tg_id, payload)


@router_public.post("/abandon", response_model=GenericResponse)
async def abandon_profession_public(
    payload: ChooseProfessionRequest,
    x_init_data: str | None = Header(default=None, alias="X-Init-Data"),
    x_tg_id: str | None = Header(default=None, alias="X-Tg-Id"),
):
    tg_id = _tg_id_from_headers(x_init_data, x_tg_id)
    return await _handle_abandon(tg_id, payload)


@router_public.post("/change", response_model=GenericResponse)
async def change_profession_public(
    payload: ChooseProfessionRequest,
    x_init_data: str | None = Header(default=None, alias="X-Init-Data"),
    x_tg_id: str | None = Header(default=None, alias="X-Tg-Id"),
):
    tg_id = _tg_id_from_headers(x_init_data, x_tg_id)
    return await _handle_abandon(tg_id, payload)