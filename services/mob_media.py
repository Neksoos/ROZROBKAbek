# services/mob_media.py
from __future__ import annotations

from typing import Optional, Dict
from loguru import logger

# DB пул у форматі мініапа
try:
    from db import get_pool  # type: ignore
except Exception:
    get_pool = None  # type: ignore


MOB_SCREEN_KEY = "mob:screen"  # залишив як константу для сумісності


def mob_key(mob_code: str) -> str:
    """
    Формує ключ прив'язки зображення для моба (як було у images.mob_key).
    """
    return f"mob:{str(mob_code).strip()}"


async def _lookup_bound_image_url(key: str) -> Optional[str]:
    """
    Шукає прив'язане зображення для ключа.
    Очікується таблиця:
        images_bindings(key TEXT PRIMARY KEY, image_url TEXT NOT NULL)
    Якщо структура інша — адаптуй SQL нижче.
    """
    if not get_pool:
        logger.warning("mob_media: no DB pool")
        return None

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS images_bindings(
                    key TEXT PRIMARY KEY,
                    image_url TEXT NOT NULL
                );
            """)
            row = await conn.fetchrow(
                "SELECT image_url FROM images_bindings WHERE key=$1",
                key,
            )
            return str(row["image_url"]) if row and row["image_url"] else None
    except Exception as e:
        logger.warning(f"mob_media: lookup failed for key='{key}': {e}")
        return None


async def get_mob_media(
    mob_code: str,
    caption: Optional[str] = None,
    actions: Optional[list[dict]] = None,
) -> Optional[Dict]:
    """
    Повертає опис медіа для моба, щоб фронт показав картинку + підпис + кнопки.
    Формат:
    {
      "key": "mob:<code>",
      "image_url": "https://...",
      "caption": "текст або ''",
      "actions": [ { "type":"postback"|"link", "title":"...", "data":"..." } ]
    }
    Якщо зображення не прив'язане — None (хай фронт зробить текстовий фолбек).
    """
    key = mob_key(mob_code)
    image_url = await _lookup_bound_image_url(key)
    if not image_url:
        return None

    return {
        "key": key,
        "image_url": image_url,
        "caption": caption or "",
        "actions": actions or [],
    }