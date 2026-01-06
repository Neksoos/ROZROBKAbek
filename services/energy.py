from __future__ import annotations

from datetime import date
from typing import Tuple

from loguru import logger
from db import get_pool  # ← абсолютний імпорт замість ..database


BASE_ENERGY_MAX = 240  # добовий ліміт


async def _normalize_player_energy(conn, tg_id: int) -> Tuple[int, int]:
    """
    Приватна утиліта.
    Викликається перед кожною операцією з наснагою.
    Дає (energy, energy_max) після daily reset.
    """
    today = date.today()

    row = await conn.fetchrow(
        """
        SELECT energy, energy_max, energy_last_reset
        FROM players
        WHERE tg_id = $1
        """,
        tg_id,
    )

    if not row:
        return BASE_ENERGY_MAX, BASE_ENERGY_MAX

    energy = row["energy"]
    energy_max = row["energy_max"]
    last_reset = row["energy_last_reset"]

    if energy_max is None or energy_max <= 0:
        energy_max = BASE_ENERGY_MAX

    # --- Daily reset ---
    if last_reset is None or last_reset < today:
        energy = energy_max
        await conn.execute(
            """
            UPDATE players
            SET energy = $2,
                energy_max = $3,
                energy_last_reset = $4
            WHERE tg_id = $1
            """,
            tg_id,
            energy,
            energy_max,
            today,
        )

    # --- Санітарна нормалізація ---
    if energy < 0 or energy > energy_max:
        energy = max(0, min(energy, energy_max))
        await conn.execute(
            "UPDATE players SET energy = $2 WHERE tg_id = $1",
            tg_id,
            energy,
        )

    return energy, energy_max


async def get_energy(tg_id: int) -> Tuple[int, int]:
    """
    Повертає (energy, energy_max) після нормалізації.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await _normalize_player_energy(conn, tg_id)


async def spend_energy(tg_id: int, amount: int) -> Tuple[int, int]:
    """
    Знімає amount наснаги.
    Якщо не вистачає — кидає ValueError.
    Повертає (energy_after, energy_max).
    """
    if amount <= 0:
        raise ValueError("ENERGY_AMOUNT_INVALID")

    pool = await get_pool()
    async with pool.acquire() as conn:
        energy, energy_max = await _normalize_player_energy(conn, tg_id)

        if energy < amount:
            raise ValueError("NO_ENERGY")

        new_energy = energy - amount

        await conn.execute(
            "UPDATE players SET energy = $2 WHERE tg_id = $1",
            tg_id,
            new_energy,
        )

        return new_energy, energy_max