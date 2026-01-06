from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from loguru import logger

from db import get_pool
from services.char_stats import get_full_stats_for_player  # hp_max, mp_max, atk, def
from services.energy import get_energy  # уже існуючий сервіс наснаги


# -------------------------------------------------------------------
# Параметри регенерації
# -------------------------------------------------------------------
# HP:
#   10% від максимуму кожні 10 хвилин
#   ≈ 60 хв = повний фул (10% * 6 = 60%)
#   на практиці герой рідко в нулі, тому фул трохи швидше
HP_REGEN_PER_10_MIN = 0.10

# MP:
#   20% від максимуму кожні 10 хвилин
#   ≈ 30–40 хв до фулу
MP_REGEN_PER_10_MIN = 0.20

# Якщо regen_at = NULL (ще ніколи не рахували реген),
# вважаємо, що минуло MINUTES_IF_NEVER_REGEN хвилин,
# щоб герой одразу отримав помітний приріст, а не 0.
MINUTES_IF_NEVER_REGEN = 15.0


@dataclass
class RegenResult:
    hp_before: int
    hp_after: int
    mp_before: int
    mp_after: int

    energy_cur: int
    energy_max: int

    @property
    def hp_delta(self) -> int:
        return self.hp_after - self.hp_before

    @property
    def mp_delta(self) -> int:
        return self.mp_after - self.mp_before

    def as_dict(self) -> dict:
        return {
            "hp_before": self.hp_before,
            "hp_after": self.hp_after,
            "hp_delta": self.hp_delta,
            "mp_before": self.mp_before,
            "mp_after": self.mp_after,
            "mp_delta": self.mp_delta,
            "energy": {
                "current": self.energy_cur,
                "max": self.energy_max,
            },
        }


async def _regen_hp_mp(tg_id: int) -> tuple[int, int, int, int]:
    """
    Чистий реген HP/MP по часу.

    Повертає:
        (hp_before, hp_after, mp_before, mp_after)

    Логіка:
      • беремо hp/mp + regen_at з players
      • рахуємо, скільки хвилин пройшло з regen_at до зараз
      • додаємо HP/MP пропорційно часу, але не вище hp_max/mp_max
      • оновлюємо hp/mp + regen_at = now
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT hp, mp, regen_at
            FROM players
            WHERE tg_id = $1
            """,
            tg_id,
        )
        if not row:
            # героя ще немає – нічого не робимо
            return 0, 0, 0, 0

        hp_before = int(row["hp"] or 0)
        mp_before = int(row["mp"] or 0)
        regen_at = row["regen_at"]

        # поточний час (UTC)
        now = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)

        # Якщо regen_at порожній – вважаємо, що останній раз
        # регенували MINUTES_IF_NEVER_REGEN хвилин тому.
        if regen_at is None:
            regen_at = now - dt.timedelta(minutes=MINUTES_IF_NEVER_REGEN)

        # Захист від кривих дат у БД (майбутнє тощо)
        if regen_at > now:
            regen_at = now

        minutes_passed = (now - regen_at).total_seconds() / 60.0

        # Якщо минуло менше 30 секунд – вважаємо, що нічого не змінилось
        if minutes_passed <= 0.5:
            return hp_before, hp_before, mp_before, mp_before

        # макси беремо з повної статистики
        try:
            stats = await get_full_stats_for_player(tg_id)
            hp_max = int(stats.get("hp_max", 1))
            mp_max = int(stats.get("mp_max", 0))
        except Exception as e:
            logger.error(f"regen: get_full_stats_for_player({tg_id}) failed: {e}")
            hp_max = max(1, hp_before)
            mp_max = max(0, mp_before)

        # у хп/мп за хвилину
        hp_regen_per_min = HP_REGEN_PER_10_MIN * hp_max / 10.0
        mp_regen_per_min = MP_REGEN_PER_10_MIN * mp_max / 10.0

        hp_gain = int(minutes_passed * hp_regen_per_min)
        mp_gain = int(minutes_passed * mp_regen_per_min)

        # якщо приріст нульовий – не чіпаємо
        if hp_gain <= 0 and mp_gain <= 0:
            return hp_before, hp_before, mp_before, mp_before

        hp_after = min(hp_max, hp_before + hp_gain)
        mp_after = min(mp_max, mp_before + mp_gain)

        try:
            await conn.execute(
                """
                UPDATE players
                SET hp = $2,
                    mp = $3,
                    regen_at = $4
                WHERE tg_id = $1
                """,
                tg_id,
                hp_after,
                mp_after,
                now,
            )
        except Exception as e:
            logger.error(f"HP/MP regen update failed for {tg_id}: {e}")

        return hp_before, hp_after, mp_before, mp_after


async def apply_full_regen(tg_id: int) -> RegenResult:
    """
    ЄДИНА функція, яку треба викликати при вході в гру / місто.

    1) Рахує реген HP/MP по часу та оновлює players.hp/mp + regen_at.
    2) Через services.energy.get_energy повертає актуальну наснагу
       (там всередині вже є свій daily reset).
    3) Повертає обʼєкт з дельтами для UI (попап «⭐ Регенеровано»).
    """
    hp_before, hp_after, mp_before, mp_after = await _regen_hp_mp(tg_id)

    # наснага: get_energy всередині сам робить нормалізацію/дейтресет
    try:
        energy_cur, energy_max = await get_energy(tg_id)
    except Exception as e:
        logger.error(f"Energy regen/get failed for {tg_id}: {e}")
        energy_cur, energy_max = 0, 0

    return RegenResult(
        hp_before=hp_before,
        hp_after=hp_after,
        mp_before=mp_before,
        mp_after=mp_after,
        energy_cur=energy_cur,
        energy_max=energy_max,
    )