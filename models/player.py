# models/player.py
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class PlayerDTO(BaseModel):
    tg_id: int
    name: str
    gender: Optional[str] = None
    race_key: Optional[str] = None
    class_key: Optional[str] = None
    chervontsi: int = 0
    kleynody: int = 0
    locale: Optional[str] = "uk"