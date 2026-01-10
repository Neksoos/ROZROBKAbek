# services/achievements/service.py
from __future__ import annotations

from typing import Dict, List, Optional, Set

from loguru import logger

from db import get_pool

from services.achievements.catalog import AchievementDef, achievements_by_metric

# клейноди — опційно
try:
    from services.wallet import add_kleynody  # type: ignore
except Exception:
    add_kleynody = None  # type: ignore


async def _get_metrics_map(conn, tg_id: int) -> Dict[str, int]:
    rows = await conn.fetch(
        "SELECT key, COALESCE(val,0)::bigint AS val FROM player_metrics WHERE tg_id=$1",
        tg_id,
    )
    out: Dict[str, int] = {}
    for r in rows or []:
        out[str(r["key"])] = int(r["val"] or 0)
    return out


async def _try_mark_event_once_tx(conn, tg_id: int, event_key: str) -> bool:
    """
    Те саме що try_mark_event_once, але в межах транзакції conn.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO player_events(tg_id, event_key)
        VALUES($1,$2)
        ON CONFLICT (tg_id, event_key) DO NOTHING
        RETURNING tg_id
        """,
        tg_id,
        event_key,
    )
    return row is not None


async def _grant_reward_tx(conn, tg_id: int, coins: int, kleynody: int) -> None:
    if coins > 0:
        await conn.execute(
            "UPDATE players SET chervontsi = chervontsi + $2 WHERE tg_id = $1",
            tg_id,
            int(coins),
        )

    # клейноди: якщо у тебе це окремий сервіс, він може працювати через інший пул.
    # Тому викликаємо вже ПІСЛЯ транзакції у check_and_grant().
    # Тут просто повернемо, щоб логіка була чиста.
    return


def _event_key_for_achv(achv_key: str) -> str:
    return f"achv:{achv_key}"


def _format_unlock_message(a: AchievementDef) -> str:
    parts = [f"🏆 Досягнення: {a.name}"]
    if a.reward.coins:
        parts.append(f"💰 +{a.reward.coins} червонців")
    if a.reward.kleynody:
        parts.append(f"💠 +{a.reward.kleynody} клейнодів")
    return " • ".join(parts)


async def check_and_grant(
    tg_id: int,
    changed_metric_keys: Optional[List[str]] = None,
) -> List[str]:
    """
    Перевіряє ачівки і видає нагороди.
    Повертає список повідомлень, які можна показати гравцю.

    changed_metric_keys:
      - якщо передати, перевіряє тільки ачівки, пов’язані з цими метриками (швидше)
      - якщо None, перевіряє все (повільніше, але ок для адмінки/ресинху)
    """
    if tg_id <= 0:
        return []

    by_metric = achievements_by_metric()

    # які ачівки взагалі перевіряти
    candidate: List[AchievementDef] = []
    if changed_metric_keys:
        seen: Set[str] = set()
        for mk in changed_metric_keys:
            for a in by_metric.get(str(mk), []):
                if a.key not in seen:
                    seen.add(a.key)
                    candidate.append(a)
    else:
        # всі
        for lst in by_metric.values():
            candidate.extend(lst)
        # унікалізація
        uniq: Dict[str, AchievementDef] = {}
        for a in candidate:
            uniq[a.key] = a
        candidate = list(uniq.values())

    if not candidate:
        return []

    messages: List[str] = []
    kleynody_to_add_total = 0

    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                metrics = await _get_metrics_map(conn, tg_id)

                for a in candidate:
                    cur = int(metrics.get(a.metric_key, 0))
                    if cur < int(a.need):
                        continue

                    ev = _event_key_for_achv(a.key)

                    first = await _try_mark_event_once_tx(conn, tg_id, ev)
                    if not first:
                        continue  # вже відкрито раніше

                    # видаємо нагороду в межах транзакції (монети)
                    await _grant_reward_tx(conn, tg_id, a.reward.coins, a.reward.kleynody)

                    # клейноди — після транзакції
                    if a.reward.kleynody:
                        kleynody_to_add_total += int(a.reward.kleynody)

                    messages.append(_format_unlock_message(a))

    except Exception:
        logger.exception("achievements.check_and_grant FAILED tg_id={}", tg_id)
        return []

    # клейноди — окремо (якщо сервіс існує)
    if kleynody_to_add_total > 0 and add_kleynody:
        try:
            await add_kleynody(tg_id, int(kleynody_to_add_total))
        except Exception:
            logger.exception("achievements: add_kleynody FAILED tg_id={} n={}", tg_id, kleynody_to_add_total)

    return messages