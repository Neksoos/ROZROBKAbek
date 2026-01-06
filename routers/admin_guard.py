from fastapi import Header, HTTPException
from config import settings


async def require_admin(
    x_admin_token: str = Header(None)
):
    """
    Доступ тільки для адміна.
    Фронт має передавати X-Admin-Token у хедерах.
    """
    if not x_admin_token or x_admin_token != settings.ADMIN_SECRET:
        raise HTTPException(
            status_code=401,
            detail={"error": "ADMIN_AUTH_FAILED"}
        )
    return True