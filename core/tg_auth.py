# core/tg_auth.py
from __future__ import annotations

import hmac
import hashlib
import json
import os
import time
from typing import Any, Dict
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")


def _parse_init_data(init_data: str) -> Dict[str, str]:
    pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=False)
    return {k: v for k, v in pairs}


def _verify_init_data(init_data: str) -> Dict[str, str]:
    data = _parse_init_data(init_data)

    hash_received = (data.get("hash") or "").strip()
    if not hash_received:
        raise HTTPException(status_code=401, detail="initData hash missing")

    auth_date_raw = data.get("auth_date")
    if auth_date_raw:
        try:
            auth_date = int(auth_date_raw)
            if int(time.time()) - auth_date > 86400 * 7:
                raise HTTPException(status_code=401, detail="initData expired")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=401, detail="initData auth_date invalid")

    check_pairs = [f"{k}={v}" for k, v in sorted(data.items()) if k != "hash"]
    data_check_string = "\n".join(check_pairs)

    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode("utf-8"), hashlib.sha256).digest()
    hash_calculated = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(hash_calculated, hash_received):
        raise HTTPException(status_code=401, detail="initData hash invalid")

    return data


def _extract_user(data: Dict[str, str]) -> Dict[str, Any]:
    user_raw = data.get("user")
    if not user_raw:
        raise HTTPException(status_code=401, detail="initData user missing")
    try:
        return json.loads(user_raw)
    except Exception:
        raise HTTPException(status_code=401, detail="initData user invalid json")


async def get_verified_initdata(
    x_init_data: str | None = Header(default=None, alias="X-Init-Data"),
) -> Dict[str, str]:
    if not x_init_data or not x_init_data.strip():
        raise HTTPException(status_code=401, detail="X-Init-Data header missing")
    return _verify_init_data(x_init_data)


async def get_tg_user(
    x_init_data: str | None = Header(default=None, alias="X-Init-Data"),
) -> Dict[str, Any]:
    if not x_init_data or not x_init_data.strip():
        raise HTTPException(status_code=401, detail="X-Init-Data header missing")

    verified = _verify_init_data(x_init_data)
    return _extract_user(verified)