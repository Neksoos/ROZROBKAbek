# services/zone.py
from __future__ import annotations
from typing import Optional
from loguru import logger

# Redis (опціонально)
try:
    from redis_manager import get_redis  # type: ignore
except Exception:
    get_redis = None  # type: ignore

# Валідні ключі зон (КАНОН)
try:
    from content.areas import AREAS_BY_KEY  # type: ignore
except Exception:
    AREAS_BY_KEY = {}

# ───────────────────────────────────────────────
# Псевдоніми / legacy / опечатки → канонічний ключ
# Канон: slums / suburbs / swamp / ruins / quarry / ridge / crown
# ───────────────────────────────────────────────
_ALIASES: dict[str, str] = {
    # українські назви з кнопок / UI
    "Нетриця": "slums",
    "Передмістя": "suburbs",
    "Болота Чорнолісся": "swamp",
    "Руїни Форпосту": "ruins",
    "Занедбаний Кар'єр": "quarry",
    "Занедбаний Кар’єр": "quarry",
    "Вітряний Хребет": "ridge",
    "Крижана Корона": "crown",

    # англ. варіанти / опечатки
    "suburb": "suburbs",
    "peredsmistia": "suburbs",
    "ruiny_f": "ruins",

    # legacy ключі зі старих міграцій / БД
    "netrytsia": "slums",
    "peredmistia": "suburbs",
    "peredmistya": "suburbs",
}

def _normalize_area_key(k: Optional[str]) -> Optional[str]:
    """
    Приводить вхідне значення до канонічного ключа з AREAS_BY_KEY.
    Підтримує українські назви, legacy-ключі та опечатки.
    """
    if not k:
        return None

    s = str(k).strip()
    s = _ALIASES.get(s, s)  # мапимо, якщо є псевдонім / legacy

    return s if s in AREAS_BY_KEY else None


# Fallback-памʼять (на випадок, коли Redis недоступний)
_zone_memory: dict[int, str] = {}


async def set_area_for_user(tg_id: int, area_key: str) -> None:
    """
    Зберігає вибрану зону користувача ТІЛЬКИ у канонічному вигляді.
    Спершу в Redis, інакше — в локальну памʼять процесу.
    """
    canon = _normalize_area_key(area_key)
    if not canon:
        logger.warning(
            "set_area_for_user: unknown area_key=%r (uid=%s). Known=%s",
            area_key, tg_id, list(AREAS_BY_KEY.keys()),
        )
        return

    # Redis
    if get_redis:
        try:
            r = await get_redis()
            await r.set(f"zone:{tg_id}", canon, ex=7 * 24 * 3600)  # 7 днів
            return
        except Exception as e:
            logger.debug("Redis set_area_for_user failed: %s", e)

    # Fallback
    _zone_memory[tg_id] = canon


async def get_area_for_user(tg_id: int) -> Optional[str]:
    """
    Повертає КАНОНІЧНИЙ ключ зони користувача або None.
    """
    # Redis
    if get_redis:
        try:
            r = await get_redis()
            val = await r.get(f"zone:{tg_id}")
            if val:
                s = val.decode() if isinstance(val, bytes) else str(val)
                canon = _normalize_area_key(s)
                if not canon:
                    logger.warning(
                        "get_area_for_user: stored invalid area_key=%r for uid=%s. Known=%s",
                        s, tg_id, list(AREAS_BY_KEY.keys()),
                    )
                return canon
        except Exception as e:
            logger.debug("Redis get_area_for_user failed: %s", e)

    # Fallback
    stored = _zone_memory.get(tg_id)
    return _normalize_area_key(stored) if stored else None
