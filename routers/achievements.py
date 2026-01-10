# routers/achievements.py
from __future__ import annotations

from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from loguru import logger

from db import get_pool

router = APIRouter(prefix="/api/achievements", tags=["achievements"])


# ─────────────────────────────────────────────
# DTO
# ─────────────────────────────────────────────

class RewardDTO(BaseModel):
    # залишаємо максимально гнучко, бо у вас можуть бути різні валюти/бейджі/титули
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
# DB ensure + seed
# ─────────────────────────────────────────────

async def ensure_achievements_tables() -> None:
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
            CREATE TABLE IF NOT EXISTS player_metrics (
              tg_id bigint NOT NULL,
              key text NOT NULL,
              value bigint NOT NULL DEFAULT 0,
              updated_at timestamptz NOT NULL DEFAULT now(),
              PRIMARY KEY (tg_id, key)
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

        await conn.execute("CREATE INDEX IF NOT EXISTS idx_ach_metric ON achievements(metric_key);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_metrics_tg ON player_metrics(tg_id);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_claims_tg ON player_achievement_claims(tg_id);")


async def seed_achievements_if_empty() -> None:
    """
    Мінімальний seed (ти потім підставиш свій JSON повністю).
    ВАЖЛИВО: tiers зберігаємо як jsonb масив об'єктів:
      [{"tier":1,"target":10,"reward":{"chervontsi":120,"badge":null,"title":null,"kleynody":0}}, ...]
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT count(*) AS c FROM achievements;")
        if row and int(row["c"] or 0) > 0:
            return

        # мінімальний набір (приклад)
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

async def get_metric(conn: Any, tg_id: int, key: str) -> int:
    v = await conn.fetchval("SELECT value FROM player_metrics WHERE tg_id=$1 AND key=$2", tg_id, key)
    return int(v or 0)


async def get_claimed_set(conn: Any, tg_id: int) -> set[tuple[str, int]]:
    rows = await conn.fetch(
        "SELECT achievement_code, tier FROM player_achievement_claims WHERE tg_id=$1",
        tg_id,
    )
    return {(str(r["achievement_code"]), int(r["tier"])) for r in (rows or [])}


def _parse_reward(d: Dict[str, Any]) -> RewardDTO:
    return RewardDTO(
        chervontsi=int(d.get("chervontsi") or 0),
        kleynody=int(d.get("kleynody") or 0),
        badge=d.get("badge"),
        title=d.get("title"),
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
        tiers_raw = list(r["tiers"] or [])
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
async def status(tg_id: int) -> List[AchievementStatusDTO]:
    if tg_id <= 0:
        raise HTTPException(400, "INVALID_TG_ID")

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

        # оптимізація: унікальні metric_key одним запитом
        metric_keys = sorted({str(r["metric_key"]) for r in ach_rows})
        metric_rows = await conn.fetch(
            "SELECT key, value FROM player_metrics WHERE tg_id=$1 AND key = ANY($2::text[])",
            tg_id,
            metric_keys,
        )
        metric_map = {str(m["key"]): int(m["value"] or 0) for m in (metric_rows or [])}

    out: List[AchievementStatusDTO] = []
    for a in ach_rows:
        code = str(a["code"])
        key = str(a["metric_key"])
        cur = int(metric_map.get(key, 0))
        tiers_raw = list(a["tiers"] or [])

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
async def claim(tg_id: int, body: ClaimBody) -> ClaimResponse:
    if tg_id <= 0:
        raise HTTPException(400, "INVALID_TG_ID")

    await ensure_achievements_tables()
    await seed_achievements_if_empty()

    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                a = await conn.fetchrow(
                    """
                    SELECT code, name, metric_key, tiers
                    FROM achievements
                    WHERE code=$1
                    FOR UPDATE
                    """,
                    body.achievement_code,
                )
                if not a:
                    raise HTTPException(404, "ACHIEVEMENT_NOT_FOUND")

                # вже забрано?
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

                key = str(a["metric_key"])
                cur = await get_metric(conn, tg_id, key)

                tiers_raw = list(a["tiers"] or [])
                tier_obj = None
                for t in tiers_raw:
                    if int(t.get("tier")) == int(body.tier):
                        tier_obj = t
                        break
                if not tier_obj:
                    raise HTTPException(404, "TIER_NOT_FOUND")

                target = int(tier_obj.get("target"))
                if cur < target:
                    raise HTTPException(400, detail={"code": "NOT_ACHIEVED", "current": cur, "target": target})

                reward = _parse_reward(dict(tier_obj.get("reward") or {}))

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

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("achievement claim failed")
        raise HTTPException(500, detail={"code": "ACH_CLAIM_INTERNAL", "error": str(e)})

    # ⚠️ Нагороду тут ВИДАВАТИ треба через ваші сервіси валюти/профілю.
    # Я залишив як "повертаємо що треба видати", а ти підключиш реальну видачу:
    # - додати chervontsi (або іншу валюту)
    # - зберегти badge/title в профіль (якщо у вас таке є)

    return ClaimResponse(ok=True, achievement_code=body.achievement_code, tier=int(body.tier), granted=reward)