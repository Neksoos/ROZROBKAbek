# services/battle/state.py
from __future__ import annotations

import json
from typing import Optional


async def save_battle(r, tg_id: int, data: dict) -> None:
    await r.set(f"battle:{tg_id}", json.dumps(data), ex=600)


async def load_battle(r, tg_id: int) -> Optional[dict]:
    raw = await r.get(f"battle:{tg_id}")
    return json.loads(raw) if raw else None