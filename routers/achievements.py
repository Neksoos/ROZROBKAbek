# routers/achievements.py
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Set, Tuple
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from loguru import logger
from starlette.requests import Request

from db import get_pool

# ✅ клейноди (опційно): якщо є сервіс гаманця — підключимо
try:
    from services.wallet import add_kleynody  # type: ignore
except Exception:
    add_kleynody = None  # type: ignore

router = APIRouter(prefix="/api/achievements", tags=["achievements"])


# ─────────────────────────────────────────────
# DTO
# ─────────────────────────────────────────────

class RewardDTO(BaseModel):
    chervontsi: int = 0
    kleynody: int = 0
    badge: Optional[str] = None
    title: Optional[str] = None


class TierDTO(BaseModel):
    tier: int
    target: int
    reward: RewardDTO


class AchievementDTO(BaseModel):
    code: str
    name: str
    category: str
    description: str
    metric_key: str
    claim_once_per_tier: bool = True
    tiers: List[TierDTO]


class AchDefsResponse(BaseModel):
    achievements: List[AchievementDTO]


class TierStatusDTO(BaseModel):
    tier: int
    target: int
    reward: RewardDTO
    achieved: bool
    claimed: bool


class AchievementStatusDTO(BaseModel):
    code: str
    name: str
    category: str
    description: str
    metric_key: str
    current_value: int
    tiers: List[TierStatusDTO]


class ClaimBody(BaseModel):
    achievement_code: str = Field(..., min_length=3)
    tier: int = Field(..., ge=1)


class ClaimResponse(BaseModel):
    ok: bool = True
    achievement_code: str
    tier: int
    granted: RewardDTO


# ─────────────────────────────────────────────
# AUTH HELPERS
# ─────────────────────────────────────────────

def _require_tg_id(request: Request) -> int:
    tg_id = getattr(request.state, "tg_id", None)
    try:
        tg_id = int(tg_id) if tg_id is not None else 0
    except Exception:
        tg_id = 0
    if tg_id <= 0:
        # ✅ не даємо підставляти tg_id через query взагалі
        raise HTTPException(status_code=401, detail="Missing tg id (initData required)")
    return tg_id


# ─────────────────────────────────────────────
# DB ensure + seed
# ─────────────────────────────────────────────

async def ensure_achievements_tables() -> None:
    """
    ВАЖЛИВО:
    - player_metrics / player_events НЕ створюємо тут, бо вони вже є з міграції achievements.
    - Метрики використовують колонку val.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS achievements (
              code text PRIMARY KEY,
              name text NOT NULL,
              category text NOT NULL,
              description text NOT NULL,
              metric_key text NOT NULL,
              claim_once_per_tier boolean NOT NULL DEFAULT true,
              tiers jsonb NOT NULL DEFAULT '[]'::jsonb,
              created_at timestamptz NOT NULL DEFAULT now(),
              updated_at timestamptz NOT NULL DEFAULT now()
            );
            """
        )

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS player_achievement_claims (
              tg_id bigint NOT NULL,
              achievement_code text NOT NULL REFERENCES achievements(code) ON DELETE CASCADE,
              tier int NOT NULL,
              claimed_at timestamptz NOT NULL DEFAULT now(),
              PRIMARY KEY (tg_id, achievement_code, tier)
            );
            """
        )

        # індекси
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_ach_metric ON achievements(metric_key);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_claims_tg ON player_achievement_claims(tg_id);")

        # (опційно) індекси з міграції — тут це буде no-op, але корисно якщо міграцію пропустили
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_player_metrics_key ON player_metrics(key);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_player_events_event_key ON player_events(event_key);")


async def seed_achievements_if_empty() -> None:
    """
    Мінімальний seed (приклад).
    tiers: jsonb масив об'єктів:
      [{"tier":1,"target":10,"reward":{"chervontsi":120,"badge":null,"title":null,"kleynody":0}}, ...]
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT count(*) AS c FROM achievements;")
        if row and int(row["c"] or 0) > 0:
            return

        await conn.execute(
            """
            INSERT INTO achievements(code, name, category, description, metric_key, claim_once_per_tier, tiers)
            VALUES
              (
                'ach_tutorial_first_login',
                'Перший вхід',
                'tutorial',
                'Зайти в гру вперше.',
                'login_days_total',
                true,
                '[{"tier":1,"target":1,"reward":{"chervontsi":50,"kleynody":0,"badge":"badge_first_step","title":null}}]'::jsonb
              ),
              (
                'ach_combat_kills_total',
                'Мисливець',
                'combat',
                'Набити загальну кількість перемог над ворогами.',
                'kills_total',
                true,
                '[
                  {"tier":1,"target":10,"reward":{"chervontsi":120,"kleynody":0,"badge":null,"title":null}},
                  {"tier":2,"target":50,"reward":{"chervontsi":350,"kleynody":0,"badge":"badge_hunter","title":null}},
                  {"tier":3,"target":200,"reward":{"chervontsi":900,"kleynody":0,"badge":null,"title":"Мисливець з курганів"}}
                ]'::jsonb
              ),
              (
                'ach_blacksmith_crafts',
                'Ковальська рутина',
                'craft',
                'Крафт у коваля: зробити певну кількість виробів.',
                'craft_blacksmith_count',
                true,
                '[
                  {"tier":1,"target":5,"reward":{"chervontsi":200,"kleynody":0,"badge":null,"title":null}},
                  {"tier":2,"target":25,"reward":{"chervontsi":650,"kleynody":0,"badge":"badge_smith","title":null}},
                  {"tier":3,"target":100,"reward":{"chervontsi":1800,"kleynody":0,"badge":null,"title":"Старший коваль"}}
                ]'::jsonb
              )
            ;
            """
        )


# ─────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────

def _coerce_tiers(raw: Any) -> List[Dict[str, Any]]:
    """
    Буває що стара таблиця зберігає tiers як TEXT з JSON.
    Тоді asyncpg повертає str, і list(str) ламає все.
    Тут приводимо до list[dict].
    """
    if raw is None:
        return []

    # якщо в БД tiers зберігся як JSON-рядок
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []

    if isinstance(raw, list):
        out: List[Dict[str, Any]] = []
        for x in raw:
            if isinstance(x, str):
                try:
                    x = json.loads(x)
                except Exception:
                    continue
            if isinstance(x, dict):
                out.append(x)
        return out

    return []


async def get_metric_val(conn: Any, tg_id: int, key: str) -> int:
    # ✅ у вашій схемі це val, не value
    v = await conn.fetchval(
        "SELECT COALESCE(val,0)::bigint FROM player_metrics WHERE tg_id=$1 AND key=$2",
        tg_id,
        key,
    )
    return int(v or 0)


async def get_claimed_set(conn: Any, tg_id: int) -> Set[Tuple[str, int]]:
    rows = await conn.fetch(
        "SELECT achievement_code, tier FROM player_achievement_claims WHERE tg_id=$1",
        tg_id,
    )
    return {(str(r["achievement_code"]), int(r["tier"])) for r in (rows or [])}


def _parse_reward(d: Dict[str, Any]) -> RewardDTO:
    if isinstance(d, str):
        try:
            d = dict(json.loads(d))
        except Exception:
            d = {}
    return RewardDTO(
        chervontsi=int((d or {}).get("chervontsi") or 0),
        kleynody=int((d or {}).get("kleynody") or 0),
        badge=(d or {}).get("badge"),
        title=(d or {}).get("title"),
    )


# ─────────────────────────────────────────────
# endpoints
# ─────────────────────────────────────────────

@router.get("/defs", response_model=AchDefsResponse)
async def defs() -> AchDefsResponse:
    await ensure_achievements_tables()
    await seed_achievements_if_empty()

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT code, name, category, description, metric_key, claim_once_per_tier, tiers
            FROM achievements
            ORDER BY category, name
            """
        )

    out: List[AchievementDTO] = []
    for r in rows:
        tiers_raw = _coerce_tiers(r["tiers"])
        tiers: List[TierDTO] = []
        for t in tiers_raw:
            tiers.append(
                TierDTO(
                    tier=int(t.get("tier")),
                    target=int(t.get("target")),
                    reward=_parse_reward(dict(t.get("reward") or {})),
                )
            )
        out.append(
            AchievementDTO(
                code=str(r["code"]),
                name=str(r["name"]),
                category=str(r["category"]),
                description=str(r["description"]),
                metric_key=str(r["metric_key"]),
                claim_once_per_tier=bool(r["claim_once_per_tier"]),
                tiers=tiers,
            )
        )

    return AchDefsResponse(achievements=out)


@router.get("/status", response_model=List[AchievementStatusDTO])
async def status(request: Request) -> List[AchievementStatusDTO]:
    tg_id = _require_tg_id(request)

    await ensure_achievements_tables()
    await seed_achievements_if_empty()

    pool = await get_pool()
    async with pool.acquire() as conn:
        ach_rows = await conn.fetch(
            """
            SELECT code, name, category, description, metric_key, tiers
            FROM achievements
            ORDER BY category, name
            """
        )
        claimed = await get_claimed_set(conn, tg_id)

        metric_keys = sorted({str(r["metric_key"]) for r in ach_rows})
        metric_rows = await conn.fetch(
            "SELECT key, val FROM player_metrics WHERE tg_id=$1 AND key = ANY($2::text[])",
            tg_id,
            metric_keys,
        )
        metric_map = {str(m["key"]): int(m["val"] or 0) for m in (metric_rows or [])}

    out: List[AchievementStatusDTO] = []
    for a in ach_rows:
        code = str(a["code"])
        key = str(a["metric_key"])
        cur = int(metric_map.get(key, 0))

        tiers_raw = _coerce_tiers(a["tiers"])
        tiers: List[TierStatusDTO] = []
        for t in tiers_raw:
            tier_n = int(t.get("tier"))
            target = int(t.get("target"))
            achieved = cur >= target
            is_claimed = (code, tier_n) in claimed
            tiers.append(
                TierStatusDTO(
                    tier=tier_n,
                    target=target,
                    reward=_parse_reward(dict(t.get("reward") or {})),
                    achieved=achieved,
                    claimed=is_claimed,
                )
            )

        out.append(
            AchievementStatusDTO(
                code=code,
                name=str(a["name"]),
                category=str(a["category"]),
                description=str(a["description"]),
                metric_key=key,
                current_value=cur,
                tiers=tiers,
            )
        )

    return out


@router.post("/claim", response_model=ClaimResponse)
async def claim(request: Request, body: ClaimBody) -> ClaimResponse:
    tg_id = _require_tg_id(request)

    await ensure_achievements_tables()
    await seed_achievements_if_empty()

    pool = await get_pool()
    reward: RewardDTO
    kleynody_to_add = 0

    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                a = await conn.fetchrow(
                    """
                    SELECT code, metric_key, tiers
                    FROM achievements
                    WHERE code=$1
                    FOR UPDATE
                    """,
                    body.achievement_code,
                )
                if not a:
                    raise HTTPException(404, "ACHIEVEMENT_NOT_FOUND")

                already = await conn.fetchval(
                    """
                    SELECT 1
                    FROM player_achievement_claims
                    WHERE tg_id=$1 AND achievement_code=$2 AND tier=$3
                    """,
                    tg_id,
                    body.achievement_code,
                    body.tier,
                )
                if already:
                    raise HTTPException(400, "TIER_ALREADY_CLAIMED")

                metric_key = str(a["metric_key"])
                cur = await get_metric_val(conn, tg_id, metric_key)

                tiers_raw = _coerce_tiers(a["tiers"])
                tier_obj: Optional[Dict[str, Any]] = None
                for t in tiers_raw:
                    if int(t.get("tier")) == int(body.tier):
                        tier_obj = t
                        break
                if not tier_obj:
                    raise HTTPException(404, "TIER_NOT_FOUND")

                target = int(tier_obj.get("target"))
                if cur < target:
                    raise HTTPException(
                        400,
                        detail={"code": "NOT_ACHIEVED", "current": cur, "target": target},
                    )

                reward = _parse_reward(dict(tier_obj.get("reward") or {}))

                # ✅ запис claim
                await conn.execute(
                    """
                    INSERT INTO player_achievement_claims(tg_id, achievement_code, tier, claimed_at)
                    VALUES($1,$2,$3,$4)
                    """,
                    tg_id,
                    body.achievement_code,
                    int(body.tier),
                    datetime.now(timezone.utc),
                )

                # ✅ видача монет — атомарно в транзакції
                if reward.chervontsi > 0:
                    await conn.execute(
                        "UPDATE players SET chervontsi = chervontsi + $2 WHERE tg_id = $1",
                        tg_id,
                        int(reward.chervontsi),
                    )

                # ✅ kleynody краще після транзакції (бо може бути інший пул/сервіс)
                if reward.kleynody > 0:
                    kleynody_to_add = int(reward.kleynody)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("achievement claim failed")
        raise HTTPException(500, detail={"code": "ACH_CLAIM_INTERNAL", "error": str(e)})

    # ✅ видача клейнодів після транзакції
    if kleynody_to_add > 0:
        if add_kleynody:
            try:
                await add_kleynody(tg_id, kleynody_to_add)
            except Exception:
                logger.exception(
                    "achievement: add_kleynody FAILED tg_id={} n={}",
                    tg_id,
                    kleynody_to_add,
                )
                # не падаємо — claim уже записаний
        else:
            logger.warning(
                "achievement: kleynody reward requested but services.wallet.add_kleynody is missing"
            )

    return ClaimResponse(ok=True, achievement_code=body.achievement_code, tier=int(body.tier), granted=reward)