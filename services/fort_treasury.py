from __future__ import annotations

from typing import Any, Dict, List, Optional

from db import get_pool


def _row_to_state(row) -> Dict[str, Any]:
    if not row:
        # порожня казна (застава ще нічого не кидала)
        return {
            "zastava_id": None,
            "chervontsi": 0,
            "kleynody": 0,
            "updated_at": None,
        }

    return {
        "zastava_id": row["zastava_id"],
        "chervontsi": int(row["chervontsi"]),
        "kleynody": int(row["kleynody"]),
        "updated_at": row["updated_at"],
    }


async def get_zastava_treasury(zastava_id: int) -> Dict[str, Any]:
    """
    Повернути поточний стан казни застави.
    Якщо запису немає – повертає нулі, але без створення рядка.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT zastava_id, chervontsi, kleynody, updated_at
            FROM fort_treasury
            WHERE zastava_id = $1
            """,
            zastava_id,
        )

    state = _row_to_state(row)
    # якщо не було рядка – підставляємо id, щоб фронту було зручніше
    if state["zastava_id"] is None:
        state["zastava_id"] = zastava_id
    return state


async def change_zastava_treasury(
    *,
    zastava_id: int,
    tg_id: int,
    delta_chervontsi: int,
    delta_kleynody: int,
    action: str,
    source: str,
    comment: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Змінює казну застави на delta_* (можуть бути відʼємними),
    не дозволяє піти нижче нуля та пише запис у fort_treasury_log.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # upsert з клампом до нуля
            row = await conn.fetchrow(
                """
                INSERT INTO fort_treasury (zastava_id, chervontsi, kleynody)
                VALUES ($1, GREATEST(0, $2), GREATEST(0, $3))
                ON CONFLICT (zastava_id) DO UPDATE
                SET
                    chervontsi = GREATEST(
                        0,
                        fort_treasury.chervontsi + EXCLUDED.chervontsi
                    ),
                    kleynody   = GREATEST(
                        0,
                        fort_treasury.kleynody   + EXCLUDED.kleynody
                    ),
                    updated_at = now()
                RETURNING zastava_id, chervontsi, kleynody, updated_at
                """,
                zastava_id,
                delta_chervontsi,
                delta_kleynody,
            )

            await conn.execute(
                """
                INSERT INTO fort_treasury_log
                    (zastava_id, tg_id,
                     delta_chervontsi, delta_kleynody,
                     action, source, comment)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                zastava_id,
                tg_id,
                delta_chervontsi,
                delta_kleynody,
                action,
                source,
                comment,
            )

    return _row_to_state(row)


async def get_zastava_treasury_log(
    *,
    zastava_id: int,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """
    Витягує історію казни для застави з пагінацією.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                id,
                zastava_id,
                tg_id,
                delta_chervontsi,
                delta_kleynody,
                action,
                source,
                comment,
                created_at
            FROM fort_treasury_log
            WHERE zastava_id = $1
            ORDER BY created_at DESC, id DESC
            LIMIT $2 OFFSET $3
            """,
            zastava_id,
            limit,
            offset,
        )

    result: List[Dict[str, Any]] = []
    for r in rows:
        result.append(
            {
                "id": r["id"],
                "zastava_id": r["zastava_id"],
                "tg_id": r["tg_id"],
                "delta_chervontsi": int(r["delta_chervontsi"]),
                "delta_kleynody": int(r["delta_kleynody"]),
                "action": r["action"],
                "source": r["source"],
                "comment": r["comment"],
                "created_at": r["created_at"],
            }
        )
    return result