# services/battle/deps.py
from __future__ import annotations

import json
from urllib.parse import parse_qs

from fastapi import HTTPException


def tg_id_from_init_data(x_init_data: str | None) -> int:
    if not x_init_data or not x_init_data.strip():
        raise HTTPException(status_code=401, detail="Missing X-Init-Data")

    try:
        qs = parse_qs(x_init_data, keep_blank_values=True)
        user_raw = (qs.get("user") or [None])[0]
        if not user_raw:
            raise ValueError("user missing")

        user = json.loads(user_raw)
        tg_id = int(user.get("id"))
        return tg_id
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid X-Init-Data")