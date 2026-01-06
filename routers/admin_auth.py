# routers/admin_auth.py

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import settings

router = APIRouter(
    prefix="/admin",
    tags=["admin"]
)


class AdminLoginDTO(BaseModel):
    token: str


@router.post("/login")
async def admin_login(dto: AdminLoginDTO):
    """
    Перевірка адмін-токена з .env.
    FastAPI повертає 401, якщо токен неправильний.
    """
    if dto.token != settings.ADMIN_SECRET:
        raise HTTPException(
            status_code=401,
            detail={"error": "INVALID_TOKEN"}
        )

    return {"ok": True}