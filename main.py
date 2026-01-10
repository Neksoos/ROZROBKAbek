from __future__ import annotations

import json
import os
import hmac
import hashlib
import time
import urllib.parse
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from db import get_pool, run_migrations

# ───────── ROUTERS ─────────
from routers.admin_auth import router as admin_auth_router
from routers.admin_notify import router as admin_notify_router
from routers.admin_players import router as admin_players_router

from routers.areas import router as areas_router
from routers.auth import router as auth_router
from routers.battle import router as battle_router
from routers.city import router as city_router
from routers.city_entry import router as city_entry_router
from routers.daily_login_router import router as daily_login_router  # ✅ NEW
from routers.forum import router as forum_router

from routers.gathering import router as gathering_router
from routers.gathering_story import router as gathering_story_router
from routers.gathering_professions_ui import router as gathering_professions_ui_router

from routers.inventory import router as inventory_router
from routers.materials import router as materials_router
from routers.alchemy import router as alchemy_router
from routers.blacksmith import router as blacksmith_router  # ✅ NEW

from routers.craft_materials import router as craft_materials_router  # ✅ NEW

from routers.achievements import router as achievements_router  # ✅ NEW (ACHIEVEMENTS)

from routers.mail import router as mail_router
from routers.night_watch_api import router as night_watch_router
from routers.npc_router import router as npc_router
from routers.perun import router as perun_router
from routers.profile import router as profile_router
from routers.professions import router as professions_router
from routers.quests import router as quests_router
from routers.ratings import router as ratings_router
from routers.redis_manager import close_redis, get_redis
from routers.referrals import router as referrals_router
from routers.registration import router as registration_router
from routers.tavern import router as tavern_router
from routers.tavern_chat import router as tavern_chat_router
from routers.zastava import router as zastava_router
from routers.zastavy_chat import router as zastavy_chat_router

from seed_craft_materials import seed_craft_materials
from seed_equipment import seed_equipment_items
from seed_gathering_resources import seed_gathering_resources
from seed_junk_loot import seed_junk_loot

APP_VERSION = os.getenv("APP_VERSION", "dev")
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

app = FastAPI(title="Kyhranu API", version=APP_VERSION)

# ─────────────────────────────────────────────
# CORS
# ─────────────────────────────────────────────
allowed_origins = {
    "http://localhost:3000",
    "https://web.telegram.org",
    "https://telegram.org",
    "https://t.me",
    "https://kyrhanu-frontend-production.up.railway.app",
}
if FRONTEND_ORIGIN:
    allowed_origins.add(FRONTEND_ORIGIN)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(allowed_origins),
    allow_origin_regex=r"^https:\/\/([a-z0-9-]+\.)*(railway\.app|telegram\.org|t\.me)$",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "X-Init-Data",
        "X-Tg-Id",
    ],
)

# ─────────────────────────────────────────────
# Telegram helpers
# ─────────────────────────────────────────────
def _parse_init_data(init_data: str) -> dict[str, str]:
    return dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))


def _verify_init_data(init_data: str) -> dict[str, str]:
    if not BOT_TOKEN:
        raise HTTPException(500, "BOT_TOKEN not configured")

    data = _parse_init_data(init_data)
    hash_string = data.get("hash", "")
    auth_date = int(data.get("auth_date", "0"))

    if int(time.time()) - auth_date > 86400 * 7:
        raise HTTPException(401, "initData expired")

    check_data = "\n".join(f"{k}={v}" for k, v in sorted(data.items()) if k != "hash")
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc_hash = hmac.new(secret_key, check_data.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calc_hash, hash_string):
        raise HTTPException(401, "invalid initData")

    return data


def _extract_tg_id(verified: dict) -> Optional[int]:
    try:
        return int(json.loads(verified.get("user", "{}"))["id"])
    except Exception:
        return None


# ─────────────────────────────────────────────
# ✅ GLOBAL TG ID MIDDLEWARE (SAFE)
# ─────────────────────────────────────────────
class TgIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not hasattr(request.state, "tg_id"):
            init_data = request.headers.get("X-Init-Data")
            if init_data:
                try:
                    verified = _verify_init_data(init_data)
                    tg_id = _extract_tg_id(verified)
                    if tg_id:
                        request.state.tg_id = tg_id
                except Exception:
                    pass
        return await call_next(request)


app.add_middleware(TgIdMiddleware)

# ─────────────────────────────────────────────
# ChatLevelGuard (FIXED)
# ─────────────────────────────────────────────
MIN_CHAT_LEVEL = 3


def _tg_id_from_query(request: Request) -> Optional[int]:
    raw = request.query_params.get("tg_id")
    if not raw:
        return None
    try:
        n = int(raw)
        return n if n > 0 else None
    except Exception:
        return None


class ChatLevelGuard(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        is_chat_write = request.method in ("POST", "PUT", "PATCH") and (
            path.startswith("/chat/tavern") or path.startswith("/api/zastavy/chat")
        )
        if not is_chat_write:
            return await call_next(request)

        tg_id = getattr(request.state, "tg_id", None)
        if not tg_id:
            tg_id = _tg_id_from_query(request)

        if not tg_id:
            return JSONResponse(401, {"detail": "Missing tg id"})

        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COALESCE(level,1) AS level FROM players WHERE tg_id=$1",
                tg_id,
            )

        if (row["level"] if row else 1) < MIN_CHAT_LEVEL:
            return JSONResponse(403, {"error": f"💬 Чат доступний з {MIN_CHAT_LEVEL} рівня"})

        return await call_next(request)


app.add_middleware(ChatLevelGuard)

# ─────────────────────────────────────────────
# STARTUP / SHUTDOWN
# ─────────────────────────────────────────────
@app.on_event("startup")
async def startup_event():
    await get_redis()
    await run_migrations()

    for fn in (
        seed_gathering_resources,
        seed_craft_materials,
        seed_equipment_items,
        seed_junk_loot,
    ):
        try:
            await fn()
        except Exception as e:
            logger.warning(e)


@app.on_event("shutdown")
async def shutdown_event():
    await close_redis()


@app.get("/health")
async def health():
    return {"ok": True, "version": APP_VERSION}


# ─────────────────────────────────────────────
# ROUTERS
# ─────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(city_router)
app.include_router(city_entry_router)
app.include_router(daily_login_router)  # ✅ NEW
app.include_router(registration_router)
app.include_router(zastava_router)
app.include_router(profile_router)

app.include_router(battle_router, prefix="/api")
app.include_router(professions_router)

app.include_router(gathering_router)
app.include_router(gathering_story_router)
app.include_router(gathering_professions_ui_router)

app.include_router(inventory_router)
app.include_router(materials_router)
app.include_router(alchemy_router)
app.include_router(blacksmith_router)  # ✅ NEW

app.include_router(achievements_router)  # ✅ NEW (ACHIEVEMENTS)

app.include_router(craft_materials_router, prefix="/api")  # ✅ NEW

app.include_router(areas_router)
app.include_router(mail_router)
app.include_router(npc_router)
app.include_router(perun_router)
app.include_router(referrals_router)
app.include_router(tavern_chat_router)
app.include_router(zastavy_chat_router)
app.include_router(tavern_router)
app.include_router(ratings_router)
app.include_router(night_watch_router)
app.include_router(forum_router)
app.include_router(quests_router)

app.include_router(admin_auth_router, prefix="/api")
app.include_router(admin_players_router, prefix="/api")
app.include_router(admin_notify_router, prefix="/api")