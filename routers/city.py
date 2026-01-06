# src/routers/city.py
from __future__ import annotations

import datetime as dt
from typing import List, Tuple, Set, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from db import get_pool

router = APIRouter(prefix="/api/city", tags=["city"])

_AREAS: List[dict] = [
    {"key": "forest_edge", "name": "Лісовий Узлісок", "desc": "Легкі бої, безпечні стежки."},
    {"key": "old_graves", "name": "Старі Могили", "desc": "Неспокійні душі та нічні згарища."},
    {"key": "swamp", "name": "Чорне Болото", "desc": "Повільні ходи, отрути та твань."},
    {"key": "clan_steppes", "name": "Козацькі Степи", "desc": "Швидкі наїзники, славний лут."},
]


# ────────────────────────────────────────────────────────────────────
# Pydantic моделі
# ────────────────────────────────────────────────────────────────────
class NearbyPlayer(BaseModel):
    tg_id: int
    name: str
    level: int
    race_key: str
    class_key: str


class CityMenuItem(BaseModel):
    key: str
    title: str
    icon: str


class CityPayload(BaseModel):
    title: str
    tagline: str
    nearby: List[NearbyPlayer] = Field(default_factory=list)  # ✅ fix
    menu: List[CityMenuItem] = Field(default_factory=list)    # ✅ fix


class AreaItem(BaseModel):
    key: str
    name: str
    desc: str = ""


class TouchReq(BaseModel):
    location: str = "city"


# ────────────────────────────────────────────────────────────────────
# tg_id helper (initData -> request.state.tg_id)
# ────────────────────────────────────────────────────────────────────
def _get_tg_id(request: Request, legacy_query_tg_id: Optional[int] = None) -> int:
    """
    Очікуємо, що tg_id вже покладений у request.state.tg_id middleware-ом,
    який валідує X-Init-Data.
    Залишив legacy fallback через query, щоб не зламати фронт миттєво.
    Коли перейдеш повністю на initData — прибери legacy_query_tg_id.
    """
    tg_id = getattr(request.state, "tg_id", None)
    if tg_id is not None:
        return int(tg_id)

    if legacy_query_tg_id is not None:
        return int(legacy_query_tg_id)

    raise HTTPException(401, "MISSING_TG_ID")  # або "Missing X-Init-Data"


# ────────────────────────────────────────────────────────────────────
# DB helpers
# ────────────────────────────────────────────────────────────────────
async def _is_registered(tg_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT name, race_key, class_key, gender FROM players WHERE tg_id=$1",
            tg_id,
        )
    return bool(row and row["name"] and row["race_key"] and row["class_key"] and row["gender"])


async def _ensure_presence_schema(conn) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS presence (
            tg_id BIGINT PRIMARY KEY,
            location TEXT NOT NULL,
            updated_at TIMESTAMP NOT NULL DEFAULT now()
        );
        """
    )


async def _touch_presence(tg_id: int, location: str = "city") -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_presence_schema(conn)
        await conn.execute(
            """
            INSERT INTO presence(tg_id, location, updated_at)
            VALUES ($1, $2, now())
            ON CONFLICT (tg_id)
            DO UPDATE SET location = EXCLUDED.location, updated_at = now()
            """,
            tg_id, location,
        )


async def _list_players_in_city(me_tg: int, limit: int = 6) -> List[Tuple[int, str, int, str, str]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_presence_schema(conn)
        rows = await conn.fetch(
            """
            SELECT p.tg_id, p.name, COALESCE(p.level,1) AS level, p.race_key, p.class_key
            FROM presence pr
            JOIN players p ON p.tg_id = pr.tg_id
            WHERE pr.location = 'city'
              AND pr.tg_id <> $1
              AND pr.updated_at > now() - INTERVAL '5 minutes'
            ORDER BY pr.updated_at DESC
            LIMIT $2
            """,
            me_tg, limit
        )

    res: List[Tuple[int, str, int, str, str]] = []
    for r in rows or []:
        res.append((int(r["tg_id"]), r["name"], int(r["level"]), str(r["race_key"]), str(r["class_key"])))
    return res


async def _get_player_professions(tg_id: int) -> Set[str]:
    pool = await get_pool()
    profs: Set[str] = set()
    async with pool.acquire() as conn:
        try:
            r1 = await conn.fetchrow("SELECT profession_key FROM players WHERE tg_id=$1", tg_id)
            if r1 and r1.get("profession_key"):
                profs.add(str(r1["profession_key"]))
        except Exception:
            pass
        try:
            rows = await conn.fetch("SELECT profession_key FROM player_professions WHERE tg_id=$1", tg_id)
            for rr in rows or []:
                if rr and rr.get("profession_key"):
                    profs.add(str(rr["profession_key"]))
        except Exception:
            pass
    return profs


# ────────────────────────────────────────────────────────────────────
# Меню для MiniApp
# ────────────────────────────────────────────────────────────────────
def _city_menu_for(profs: Set[str]) -> List[CityMenuItem]:
    base: List[CityMenuItem] = [
        CityMenuItem(key="quests",   title="Квести",           icon="🗺️"),
        CityMenuItem(key="zastava",  title="Застава",          icon="🏰"),
        CityMenuItem(key="ratings",  title="Рейтинги",         icon="🏆"),
        CityMenuItem(key="perun",    title="Суд Перуна",       icon="⚖️"),
        CityMenuItem(key="tavern",   title="Корчма",           icon="🍺"),
        CityMenuItem(key="kleynods", title="Клейноди",         icon="💎"),
        CityMenuItem(key="workshop", title="Майстерня",        icon="🛠️"),
        CityMenuItem(key="prof",     title="Професії",         icon="🏛️"),
        CityMenuItem(key="profile",  title="Профіль",          icon="👤"),
        CityMenuItem(key="forum",    title="Форум",            icon="💬"),
        CityMenuItem(key="settings", title="Налаштування",     icon="⚙️"),
        CityMenuItem(key="invite",   title="Запросити друга",  icon="🔗"),
        CityMenuItem(key="areas",    title="Околиці",          icon="🌍"),
    ]
    if "herb" in profs:
        base.append(CityMenuItem(key="herb", title="Травник", icon="🌿"))
    if "mining" in profs:
        base.append(CityMenuItem(key="mining", title="Рудокоп", icon="⛏️"))
    if "jew" in profs:
        base.append(CityMenuItem(key="jew", title="Ювелір", icon="💍"))
    return base


# ────────────────────────────────────────────────────────────────────
# Основні ендпоінти
# ────────────────────────────────────────────────────────────────────
@router.get("/", response_model=CityPayload)
async def get_city(
    request: Request,
    tg_id: Optional[int] = Query(None, description="LEGACY: Telegram user id (remove later)"),
):
    uid = _get_tg_id(request, legacy_query_tg_id=tg_id)

    if not await _is_registered(uid):
        raise HTTPException(403, "NOT_REGISTERED")

    await _touch_presence(uid, "city")
    nearby_raw = await _list_players_in_city(uid)
    profs = await _get_player_professions(uid)

    nearby = [
        NearbyPlayer(tg_id=p[0], name=p[1], level=p[2], race_key=p[3], class_key=p[4])
        for p in nearby_raw
    ]

    return CityPayload(
        title="Берегинів",
        tagline="Тут починається твоя історія. Місто, де кожен крок може стати легендою.",
        nearby=nearby,
        menu=_city_menu_for(profs),
    )


@router.get("/areas", response_model=List[AreaItem])
async def list_areas():
    return [AreaItem(**a) for a in _AREAS]


@router.post("/presence/touch")
async def touch_presence(request: Request, req: TouchReq):
    uid = _get_tg_id(request)
    await _touch_presence(uid, req.location or "city")
    return {"ok": True, "ts": dt.datetime.utcnow().isoformat() + "Z"}


@router.get("/open")
async def city_open():
    return {
        "text": (
            "<h3>👑 Берегинів</h3>"
            "<p>Тут починається твоя історія. Берегинів — місто, де кожен крок може стати легендою.</p>"
        ),
        "buttons": [
            {"title": "📜 Квести",        "href": "/city/quests"},
            {"title": "🏰 Застава",       "href": "/zastavy"},
            {"title": "🏆 Рейтинги",      "href": "/ratings"},
            {"title": "⚖️ Суд Перуна",    "href": "/perun"},
            {"title": "🍺 Корчма",        "href": "/tavern"},
            {"title": "💎 Клейноди",      "href": "/kleynody"},
            {"title": "🛠️ Майстерня",     "href": "/workshop"},
            {"title": "🏛️ Професії",      "href": "/professions"},
            {"title": "👤 Профіль",       "href": "/profile"},
            {"title": "💬 Форум",         "href": "/forum"},
            {"title": "⚙️ Налаштування",  "href": "/settings"},
            {"title": "🌍 Околиці",       "href": "/areas"},
        ],
    }