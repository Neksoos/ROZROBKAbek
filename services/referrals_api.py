from __future__ import annotations
import os
import httpx
from typing import Optional
from loguru import logger

BACKEND_BASE = os.getenv("BACKEND_BASE", "http://localhost:8080")


async def bind_referral(tg_id: int, payload: Optional[str]):
    """Виклик бекенду — підʼєднати юзера до інвайтера."""
    if not payload:
        return

    url = f"{BACKEND_BASE}/api/referrals/bind"
    headers = {"X-Tg-Id": str(tg_id)}
    data = {"payload": payload}

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.post(url, json=data, headers=headers)
            r.raise_for_status()
            logger.info(f"[bind_ref] {tg_id} → {payload}: {r.json()}")
    except Exception as e:
        logger.warning(f"[bind_ref] failed: {e}")