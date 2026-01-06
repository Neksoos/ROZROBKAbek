from __future__ import annotations

from typing import Optional, List, Dict, Any
from fastapi import APIRouter
from db import get_pool

router = APIRouter(prefix="/craft_materials", tags=["craft_materials"])


def _category(code: str) -> str:
    if code.startswith("alch_flask_"):
        return "flask"
    if code.startswith("alch_base_"):
        return "base"
    return "other"


@router.get("/shop")
async def craft_materials_shop(q: Optional[str] = None) -> Dict[str, Any]:
    """
    Реміснича лавка.
    Повертає тільки shop-матеріали:
    - alch_flask_*
    - alch_base_*
    """
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT
            id,
            code,
            name,
            descr,
            appearance_text
        FROM craft_materials
        WHERE code LIKE 'alch_flask_%'
           OR code LIKE 'alch_base_%'
        ORDER BY
          CASE
            WHEN code LIKE 'alch_flask_%' THEN 1
            WHEN code LIKE 'alch_base_%' THEN 2
            ELSE 9
          END,
          id ASC
        """
    )

    items: List[Dict[str, Any]] = []

    for r in rows:
        code = str(r["code"])
        items.append(
            {
                "id": r["id"],
                "code": code,
                "name": r["name"],
                "descr": r["descr"],
                "appearance_text": r["appearance_text"],
                "category": _category(code),  # flask | base
                "price": None,  # на майбутнє
            }
        )

    if q:
        qq = q.lower().strip()
        items = [
            i
            for i in items
            if qq in (i["name"] or "").lower()
            or qq in (i["code"] or "").lower()
            or qq in (i["descr"] or "").lower()
            or qq in (i["appearance_text"] or "").lower()
        ]

    return {
        "ok": True,
        "items": items,
    }


# 🔁 alias щоб фронт не падав
@router.get("/list")
async def craft_materials_list(q: Optional[str] = None):
    return await craft_materials_shop(q=q)