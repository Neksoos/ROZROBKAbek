# routers/auth.py
from __future__ import annotations

import os
import hashlib
from typing import Optional, Any

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from db import get_pool, ensure_min_schema
from models.player import PlayerDTO
from core.tg_auth import get_verified_initdata, get_tg_user

router = APIRouter(prefix="/api/auth", tags=["auth"])

PWD_SALT = os.getenv("PWD_SALT", "CHANGE_ME_PASSWORD_SALT")


class VerifyResp(BaseModel):
    ok: bool
    player: PlayerDTO


class PasswordRegisterReq(BaseModel):
    login: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=6, max_length=64)
    name: str = Field(..., min_length=3, max_length=16)
    gender: Optional[str] = None
    race_key: Optional[str] = None
    class_key: Optional[str] = None
    locale: Optional[str] = "uk"


class PasswordLoginReq(BaseModel):
    login: str
    password: str


class PasswordAuthResp(BaseModel):
    ok: bool = True
    player: PlayerDTO


def _hash_password(raw: str) -> str:
    return hashlib.sha256((PWD_SALT + raw).encode("utf-8")).hexdigest()


async def _ensure_auth_columns() -> None:
    """
    db.ensure_min_schema() в тебе створює players без login/password_hash/locale.
    Тому тут гарантовано додаємо колонки, щоб auth не падав.
    """
    await ensure_min_schema()
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""ALTER TABLE players ADD COLUMN IF NOT EXISTS login TEXT;""")
        await conn.execute("""ALTER TABLE players ADD COLUMN IF NOT EXISTS password_hash TEXT;""")
        await conn.execute("""ALTER TABLE players ADD COLUMN IF NOT EXISTS locale TEXT DEFAULT 'uk';""")

        # ці можуть бути потрібні у тебе по коду
        await conn.execute("""ALTER TABLE players ADD COLUMN IF NOT EXISTS gender TEXT;""")
        await conn.execute("""ALTER TABLE players ADD COLUMN IF NOT EXISTS race_key TEXT;""")
        await conn.execute("""ALTER TABLE players ADD COLUMN IF NOT EXISTS class_key TEXT;""")


def _row_to_player_dto(row) -> PlayerDTO:
    d = dict(row)
    return PlayerDTO(
        tg_id=int(d["tg_id"]),
        name=d.get("name") or "",
        gender=d.get("gender"),
        race_key=d.get("race_key"),
        class_key=d.get("class_key"),
        chervontsi=int(d.get("chervontsi") or 0),
        kleynody=int(d.get("kleynody") or 0),
        locale=d.get("locale") or "uk",
    )


async def _ensure_player_for_tg(tg_id: int, fallback_name: str, locale: str = "uk") -> PlayerDTO:
    await _ensure_auth_columns()

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM players WHERE tg_id=$1", int(tg_id))
        if not row:
            # мінімальна реєстрація TG-гравця
            await conn.execute(
                """
                INSERT INTO players (tg_id, name, locale, chervontsi, kleynody)
                VALUES ($1, $2, COALESCE($3,'uk'), 50, 0)
                ON CONFLICT (tg_id) DO NOTHING
                """,
                int(tg_id),
                fallback_name,
                locale,
            )
            row = await conn.fetchrow("SELECT * FROM players WHERE tg_id=$1", int(tg_id))

    if not row:
        raise HTTPException(500, "failed_to_create_player")

    return _row_to_player_dto(row)


# ─────────────────────────────────────────────
# ✅ DEPENDENCIES ДЛЯ ІНШИХ РОУТЕРІВ
# ─────────────────────────────────────────────

async def get_tg_id(
    u: dict[str, Any] = Depends(get_tg_user),
) -> int:
    """
    Повертає tg_id тільки з перевіреного initData.
    """
    if not u or u.get("id") is None:
        raise HTTPException(401, "Invalid initData: user.id missing")
    return int(u["id"])


async def get_player(
    u: dict[str, Any] = Depends(get_tg_user),
    data: dict[str, str] = Depends(get_verified_initdata),
) -> PlayerDTO:
    """
    Повертає PlayerDTO, гарантовано створений.
    """
    tg_id = int(u["id"])
    first_name = u.get("first_name")
    username = u.get("username")
    locale = u.get("language_code") or data.get("language_code") or "uk"
    fallback_name = first_name or username or f"Гість{tg_id % 10000}"
    return await _ensure_player_for_tg(tg_id, fallback_name, locale)


# ─────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────

@router.post("/verify", response_model=VerifyResp)
async def verify(
    u: dict[str, Any] = Depends(get_tg_user),
    data: dict[str, str] = Depends(get_verified_initdata),
):
    tg_id = int(u["id"])
    first_name = u.get("first_name")
    username = u.get("username")
    locale = u.get("language_code") or data.get("language_code") or "uk"
    fallback_name = first_name or username or f"Гість{tg_id % 10000}"
    player = await _ensure_player_for_tg(tg_id, fallback_name, locale)
    return VerifyResp(ok=True, player=player)


@router.post("/register-password", response_model=PasswordAuthResp)
async def register_password(req: PasswordRegisterReq):
    login = req.login.strip().lower()
    if not login:
        raise HTTPException(400, "login_required")

    pw_hash = _hash_password(req.password)

    await _ensure_auth_columns()
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchrow("SELECT 1 FROM players WHERE login=$1 LIMIT 1", login)
        if exists:
            raise HTTPException(409, "login_taken")

        row_max = await conn.fetchrow("SELECT COALESCE(MAX(tg_id), 0) AS m FROM players")
        new_id = int(row_max["m"]) + 1

        await conn.execute(
            """
            INSERT INTO players
                (tg_id, login, password_hash,
                 name, gender, race_key, class_key,
                 locale, chervontsi, kleynody)
            VALUES
                ($1,   $2,    $3,
                 $4,   $5,    $6,       $7,
                 COALESCE($8,'uk'), 50, 0)
            """,
            new_id,
            login,
            pw_hash,
            req.name,
            req.gender,
            req.race_key,
            req.class_key,
            req.locale,
        )

        row = await conn.fetchrow("SELECT * FROM players WHERE tg_id=$1", new_id)

    return PasswordAuthResp(ok=True, player=_row_to_player_dto(row))


@router.post("/login-password", response_model=PasswordAuthResp)
async def login_password(req: PasswordLoginReq):
    login = req.login.strip().lower()
    pw_hash = _hash_password(req.password)

    await _ensure_auth_columns()
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM players WHERE login=$1", login)

    if not row:
        raise HTTPException(401, "invalid_credentials")

    d = dict(row)
    if (d.get("password_hash") or "") != pw_hash:
        raise HTTPException(401, "invalid_credentials")

    return PasswordAuthResp(ok=True, player=_row_to_player_dto(row))