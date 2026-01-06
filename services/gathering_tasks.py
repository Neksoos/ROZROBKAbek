# services/gathering_tasks.py
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional, List, Dict, Any

from loguru import logger
from db import get_pool
from services.gathering_loot import roll_gathering_loot


@dataclass
class GatheringTask:
    id: int
    tg_id: int
    area_key: str
    source_type: str
    started_at: dt.datetime
    finishes_at: dt.datetime
    resolved: bool
    risk: Optional[str] = None
    result_json: Optional[Dict[str, Any]] = None

    @property
    def is_finished(self) -> bool:
        now = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
        return now >= self.finishes_at

    @property
    def seconds_left(self) -> int:
        now = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
        delta = (self.finishes_at - now).total_seconds()
        return max(0, int(delta))


class GatheringError(Exception):
    pass


class GatheringAlreadyInProgress(GatheringError):
    pass


class GatheringTaskNotFound(GatheringError):
    pass


class GatheringNotReady(GatheringError):
    pass


def _row_to_task(row) -> GatheringTask:
    return GatheringTask(
        id=row["id"],
        tg_id=row["tg_id"],
        area_key=row["area_key"],
        source_type=row["source_type"],
        started_at=row["started_at"],
        finishes_at=row["finishes_at"],
        resolved=row["resolved"],
        risk=row.get("risk") if isinstance(row, dict) else row["risk"] if "risk" in row else None,
        result_json=row["result_json"],
    )


async def get_active_task(tg_id: int) -> Optional[GatheringTask]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT *
            FROM gathering_tasks
            WHERE tg_id = $1
              AND resolved = FALSE
              AND finishes_at > NOW() - INTERVAL '1 minute'
            ORDER BY id DESC
            LIMIT 1
            """,
            tg_id,
        )
    return _row_to_task(row) if row else None


async def start_gathering_task(
    tg_id: int,
    area_key: str,
    source_type: str,
    duration_minutes: int,
    risk: Optional[str] = None,
) -> GatheringTask:
    if duration_minutes <= 0:
        raise ValueError("duration_minutes must be > 0")

    existing = await get_active_task(tg_id)
    if existing and not existing.is_finished:
        raise GatheringAlreadyInProgress(f"Player {tg_id} already has active task #{existing.id}")

    now = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
    finishes_at = now + dt.timedelta(minutes=duration_minutes)

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO gathering_tasks
                (tg_id, area_key, source_type, started_at, finishes_at, resolved, risk)
            VALUES
                ($1,   $2,       $3,          $4,         $5,           FALSE,   $6)
            RETURNING *
            """,
            tg_id,
            area_key,
            source_type,
            now,
            finishes_at,
            risk,
        )

    task = _row_to_task(row)
    logger.info(
        "gathering: start task id=%s tg_id=%s area=%s source=%s duration=%s risk=%s",
        task.id,
        tg_id,
        area_key,
        source_type,
        duration_minutes,
        risk,
    )
    return task


async def complete_gathering_task(tg_id: int) -> tuple[GatheringTask, List[Dict[str, Any]]]:
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT *
            FROM gathering_tasks
            WHERE tg_id = $1
              AND resolved = FALSE
            ORDER BY id DESC
            LIMIT 1
            """,
            tg_id,
        )

        if not row:
            raise GatheringTaskNotFound(f"Player {tg_id} has no active gathering task")

        task = _row_to_task(row)

        now = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
        if now < task.finishes_at:
            raise GatheringNotReady(f"Task #{task.id} not finished yet, seconds_left={task.seconds_left}")

        drops_raw = await roll_gathering_loot(
            tg_id=tg_id,
            area_key=task.area_key,
            source_type=task.source_type,
            risk=task.risk,
        )
        drops = [d.as_dict() for d in drops_raw]

        result_json = {"drops": drops, "finished_at": now.isoformat()}

        await conn.execute(
            """
            UPDATE gathering_tasks
            SET resolved    = TRUE,
                result_json = $2,
                updated_at  = NOW()
            WHERE id = $1
            """,
            task.id,
            result_json,
        )

    task.result_json = result_json
    task.resolved = True

    logger.info("gathering: complete task id=%s tg_id=%s drops=%s", task.id, tg_id, len(drops))
    return task, drops