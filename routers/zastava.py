from __future__ import annotations

import re
from typing import Optional, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from db import get_pool
from services.fort_recruit import (
    get_member_fort,
    get_fort_name,
    is_leader,
)
from services.fort_levels import (
    get_fort_level,
    bonuses_summary,
)

# ✅ tg_id беремо з initData (X-Init-Data)
from routers.auth import get_tg_id  # type: ignore

router = APIRouter(
    prefix="/api/zastavy",   # ендпоінти /api/zastavy/...
    tags=["zastavy"],
)

# ─────────────────────────────────────────────
# Pydantic-моделі (під фронт)
# ─────────────────────────────────────────────


class FortBonuses(BaseModel):
    hp_pct: float = 0.0
    atk_pct: float = 0.0
    income_pct: float = 0.0
    drop_pct: float = 0.0


class FortData(BaseModel):
    id: int
    name: str
    rank: int = 1
    level: int
    xp: int
    xp_needed: int
    bonuses: FortBonuses
    member_count: Optional[int] = None
    max_members: Optional[int] = None


class Balances(BaseModel):
    chervontsi: int = 0
    kleynody: int = 0


class FortPrice(BaseModel):
    """
    Ціни, які показуємо гравцю.
    Логіка: МОЖНА створити або за 1000 червонців, АБО за 10 клейнодів.
    """
    chervontsi: int
    kleynody: int


class FortStatusResponse(BaseModel):
    ok: bool
    member: bool
    leader: Optional[bool] = None
    error: Optional[str] = None
    fort: Optional[FortData] = None

    # для стану, коли гравець НЕ в заставі
    balances: Optional[Balances] = None
    can_create: Optional[bool] = None
    price: Optional[FortPrice] = None


class LeaveResponse(BaseModel):
    ok: bool
    error: Optional[str] = None


class FortListItem(BaseModel):
    id: int
    name: str
    level: int
    member_count: int


class FortListResponse(BaseModel):
    ok: bool
    forts: List[FortListItem]


class CreateFortRequest(BaseModel):
    name: str
    # "chervontsi" або "kleynody"
    pay_with: str


class CreateFortResponse(BaseModel):
    ok: bool
    error: Optional[str] = None


class JoinRequest(BaseModel):
    fort_id: int


class JoinResponse(BaseModel):
    ok: bool
    error: Optional[str] = None


# ─────────────────────────────────────────────
# Моделі для заявок
# ─────────────────────────────────────────────


class ApplicationItem(BaseModel):
    id: int
    tg_id: int
    player_name: str
    created_at: str  # ISO-рядок


class ApplicationsResponse(BaseModel):
    ok: bool
    applications: List[ApplicationItem] = []
    error: Optional[str] = None


class AppDecisionRequest(BaseModel):
    application_id: int  # id заявки


class AppDecisionResponse(BaseModel):
    ok: bool
    error: Optional[str] = None


# ─────────────────────────────────────────────
# Моделі для списку учасників
# ─────────────────────────────────────────────


class MemberItem(BaseModel):
    tg_id: int
    name: str
    role: str
    level: int


class MembersResponse(BaseModel):
    ok: bool
    members: List[MemberItem] = []
    error: Optional[str] = None


# ─────────────────────────────────────────────
# Моделі для КАЗНИ застави
# ─────────────────────────────────────────────


class TreasuryData(BaseModel):
    chervontsi: int = 0
    kleynody: int = 0
    updated_at: Optional[str] = None


class TreasuryStateResponse(BaseModel):
    ok: bool
    error: Optional[str] = None
    treasury: Optional[TreasuryData] = None


class TreasuryOperationRequest(BaseModel):
    amount: int
    # "chervontsi" або "kleynody"
    currency: str
    comment: Optional[str] = None


class TreasuryLogItem(BaseModel):
    id: int
    zastava_id: int
    tg_id: int
    delta_chervontsi: int
    delta_kleynody: int
    action: str
    source: str
    comment: Optional[str] = None
    created_at: str


class TreasuryLogResponse(BaseModel):
    ok: bool
    items: List[TreasuryLogItem] = []
    error: Optional[str] = None


# ─────────────────────────────────────────────
# DB-хелпери
# ─────────────────────────────────────────────


async def _count_members(fort_id: int) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) AS c FROM fort_members WHERE fort_id=$1",
            fort_id,
        )
        return int(row["c"] or 0)


async def _leave_fort_db(tg_id: int) -> bool:
    """
    Та сама логіка, що й _leave_fort у aiogram-роутері,
    тільки без aiogram-залежностей.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT role, fort_id FROM fort_members WHERE tg_id=$1",
            tg_id,
        )
        if not row:
            return True

        # гетьман не може вийти, якщо він один
        if row["role"] == "hetman":
            cnt = await conn.fetchval(
                "SELECT COUNT(*) FROM fort_members "
                "WHERE fort_id=$1 AND role='hetman'",
                row["fort_id"],
            )
            if int(cnt or 0) <= 1:
                return False

        await conn.execute(
            "DELETE FROM fort_members WHERE tg_id=$1",
            tg_id,
        )
        return True


async def _get_balances(tg_id: int) -> Balances:
    """
    Читаємо баланс гравця з таблиці players.
    Якщо запису нема – повертаємо нулі.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT chervontsi, kleynody FROM players WHERE tg_id=$1",
            tg_id,
        )
        if not row:
            return Balances(chervontsi=0, kleynody=0)

        return Balances(
            chervontsi=int(row["chervontsi"] or 0),
            kleynody=int(row["kleynody"] or 0),
        )


def _parse_bonuses(text: str) -> FortBonuses:
    """
    bonuses_summary(lvl) повертає рядок типу:
    'Бонуси: HP +6%, ATK +3.0%, Дохід +2.4%, Дроп +1.8%'
    Тут швидко парсимо відсотки. Якщо не знайшли – лишаємо 0.
    """

    def grab(pattern: str) -> float:
        m = re.search(pattern, text)
        if not m:
            return 0.0
        try:
            return float(m.group(1).replace(",", "."))
        except Exception:
            return 0.0

    return FortBonuses(
        hp_pct=grab(r"HP\s*\+(\d+(?:[.,]\d+)?)%"),
        atk_pct=grab(r"ATK\s*\+(\d+(?:[.,]\d+)?)%"),
        income_pct=grab(r"Дохід\s*\+(\d+(?:[.,]\d+)?)%"),
        drop_pct=grab(r"Дроп\s*\+(\d+(?:[.,]\d+)?)%"),
    )


async def _build_fort_data(fort_id: int) -> Optional[FortData]:
    name = await get_fort_name(fort_id)
    if not name:
        return None

    lvl, xp, need = await get_fort_level(fort_id)
    bonuses_text = bonuses_summary(lvl)
    bonuses = _parse_bonuses(bonuses_text)

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) AS c FROM fort_members WHERE fort_id=$1",
            fort_id,
        )
        member_count = int(row["c"] or 0)

        # якщо є поле max_members – витягуємо, якщо нема, лишається None
        try:
            row2 = await conn.fetchrow(
                "SELECT max_members FROM forts WHERE id=$1",
                fort_id,
            )
            max_members = (
                int(row2["max_members"])
                if row2 and row2["max_members"] is not None
                else None
            )
        except Exception:
            max_members = None

    return FortData(
        id=fort_id,
        name=name,
        level=lvl,
        xp=xp,
        xp_needed=need,
        bonuses=bonuses,
        member_count=member_count,
        max_members=max_members,
    )


async def _load_treasury(conn, zastava_id: int) -> TreasuryData:
    """
    Зчитати казну застави. Якщо запису нема – повертаємо нулі.
    """
    row = await conn.fetchrow(
        """
        SELECT chervontsi, kleynody, updated_at
        FROM fort_treasury
        WHERE zastava_id = $1
        """,
        zastava_id,
    )
    if not row:
        return TreasuryData(chervontsi=0, kleynody=0, updated_at=None)

    return TreasuryData(
        chervontsi=int(row["chervontsi"] or 0),
        kleynody=int(row["kleynody"] or 0),
        updated_at=row["updated_at"].isoformat() if row["updated_at"] is not None else None,
    )


# ─────────────────────────────────────────────
# ЕНДПОІНТИ ДЛЯ МІНІАПА (initData)
# ─────────────────────────────────────────────


@router.get("/status", response_model=FortStatusResponse)
async def get_zastava_status(tg_id: int = Depends(get_tg_id)) -> FortStatusResponse:
    """
    GET /api/zastavy/status
    tg_id береться з initData.
    """
    price = FortPrice(chervontsi=1000, kleynody=10)

    try:
        fid = await get_member_fort(tg_id)
    except Exception as e:
        return FortStatusResponse(ok=False, member=False, error=str(e))

    if not fid:
        try:
            balances = await _get_balances(tg_id)
        except Exception:
            balances = None

        can_create = (
            balances is not None
            and (balances.chervontsi >= price.chervontsi or balances.kleynody >= price.kleynody)
        )

        return FortStatusResponse(
            ok=True,
            member=False,
            balances=balances,
            price=price,
            can_create=can_create,
        )

    fort = await _build_fort_data(fid)
    if not fort:
        return FortStatusResponse(ok=False, member=False, error="Застава не знайдена.")

    leader_flag = await is_leader(tg_id, fid)
    return FortStatusResponse(ok=True, member=True, leader=bool(leader_flag), fort=fort)


@router.get("/list", response_model=FortListResponse)
async def list_zastavy(q: str = "") -> FortListResponse:
    """
    GET /api/zastavy/list?q=...

    Список усіх застав з рівнем і кількістю учасників.
    q — пошук по назві (ILIKE).
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT f.id,
                   f.name,
                   COUNT(m.tg_id) AS member_count
            FROM forts f
            LEFT JOIN fort_members m ON m.fort_id = f.id
            WHERE $1 = '' OR f.name ILIKE '%' || $1 || '%'
            GROUP BY f.id, f.name
            ORDER BY f.name ASC
            """,
            q,
        )

    forts: List[FortListItem] = []
    for r in rows:
        fort_id = int(r["id"])
        lvl, _xp, _need = await get_fort_level(fort_id)
        forts.append(
            FortListItem(
                id=fort_id,
                name=r["name"],
                level=lvl,
                member_count=int(r["member_count"] or 0),
            )
        )

    return FortListResponse(ok=True, forts=forts)


@router.post("/leave", response_model=LeaveResponse)
async def leave_zastava(tg_id: int = Depends(get_tg_id)) -> LeaveResponse:
    """
    POST /api/zastavy/leave
    tg_id береться з initData.
    """
    fid = await get_member_fort(tg_id)
    if not fid:
        return LeaveResponse(ok=True)

    ok = await _leave_fort_db(tg_id)
    if not ok:
        return LeaveResponse(
            ok=False,
            error=(
                "Гетьман не може покинути заставу, "
                "поки він єдиний гетьман. Признач наступника."
            ),
        )

    return LeaveResponse(ok=True)


@router.post("/create", response_model=CreateFortResponse)
async def create_zastava(
    payload: CreateFortRequest,
    tg_id: int = Depends(get_tg_id),
) -> CreateFortResponse:
    """
    POST /api/zastavy/create
    tg_id береться з initData.
    """
    name = payload.name.strip()
    pay_with = payload.pay_with

    if len(name) < 3 or len(name) > 30:
        return CreateFortResponse(ok=False, error="Назва має містити 3–30 символів.")

    if pay_with not in ("chervontsi", "kleynody"):
        return CreateFortResponse(ok=False, error="Невірний спосіб оплати.")

    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT fort_id FROM fort_members WHERE tg_id=$1",
                    tg_id,
                )
                if row:
                    return CreateFortResponse(ok=False, error="Ти вже перебуваєш у заставі.")

                bal = await conn.fetchrow(
                    "SELECT chervontsi, kleynody FROM players WHERE tg_id=$1",
                    tg_id,
                )
                if not bal:
                    return CreateFortResponse(ok=False, error="Гравця не знайдено.")

                cherv = int(bal["chervontsi"] or 0)
                kleyn = int(bal["kleynody"] or 0)

                if pay_with == "chervontsi" and cherv < 1000:
                    return CreateFortResponse(ok=False, error="Недостатньо червонців для створення застави.")
                if pay_with == "kleynody" and kleyn < 10:
                    return CreateFortResponse(ok=False, error="Недостатньо клейнодів для створення застави.")

                if pay_with == "chervontsi":
                    await conn.execute(
                        "UPDATE players SET chervontsi = COALESCE(chervontsi,0) - 1000 WHERE tg_id=$1",
                        tg_id,
                    )
                else:
                    await conn.execute(
                        "UPDATE players SET kleynody = COALESCE(kleynody,0) - 10 WHERE tg_id=$1",
                        tg_id,
                    )

                row = await conn.fetchrow(
                    "INSERT INTO forts (name, created_by) VALUES ($1, $2) RETURNING id",
                    name,
                    tg_id,
                )
                fort_id = int(row["id"])

                await conn.execute(
                    "INSERT INTO fort_members (tg_id, fort_id, role) VALUES ($1, $2, 'hetman')",
                    tg_id,
                    fort_id,
                )

            return CreateFortResponse(ok=True)
        except Exception as e:
            return CreateFortResponse(ok=False, error=str(e))


@router.post("/join", response_model=JoinResponse)
async def join_zastava(
    payload: JoinRequest,
    tg_id: int = Depends(get_tg_id),
) -> JoinResponse:
    """
    Створює заявку в fort_applications, а не додає одразу в fort_members.
    tg_id береться з initData.
    """
    fort_id = payload.fort_id

    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            async with conn.transaction():
                player = await conn.fetchrow("SELECT tg_id FROM players WHERE tg_id=$1", tg_id)
                if not player:
                    return JoinResponse(ok=False, error="Гравця не знайдено.")

                fort_row = await conn.fetchrow("SELECT id FROM forts WHERE id=$1", fort_id)
                if not fort_row:
                    return JoinResponse(ok=False, error="Заставу не знайдено.")

                row = await conn.fetchrow("SELECT fort_id FROM fort_members WHERE tg_id=$1", tg_id)
                if row:
                    current_id = int(row["fort_id"])
                    if current_id == fort_id:
                        return JoinResponse(ok=True)
                    return JoinResponse(ok=False, error="Ти вже перебуваєш в іншій заставі.")

                app_row = await conn.fetchrow(
                    "SELECT 1 FROM fort_applications WHERE tg_id=$1 AND fort_id=$2",
                    tg_id,
                    fort_id,
                )
                if app_row:
                    return JoinResponse(ok=True)

                await conn.execute(
                    "INSERT INTO fort_applications (tg_id, fort_id) VALUES ($1, $2)",
                    tg_id,
                    fort_id,
                )

            return JoinResponse(ok=True)
        except Exception as e:
            return JoinResponse(ok=False, error=str(e))


# ─────────────────────────────────────────────
# Список учасників застави
# ─────────────────────────────────────────────


@router.get("/members", response_model=MembersResponse)
async def get_members(tg_id: int = Depends(get_tg_id)) -> MembersResponse:
    """
    GET /api/zastavy/members
    tg_id береться з initData.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT fort_id FROM fort_members WHERE tg_id=$1", tg_id)
        if not row:
            return MembersResponse(ok=False, members=[], error="Ти не перебуваєш у жодній заставі.")

        fort_id = int(row["fort_id"])

        rows = await conn.fetch(
            """
            SELECT fm.tg_id, fm.role, p.name, COALESCE(p.level, 1) AS level
            FROM fort_members fm
            LEFT JOIN players p ON p.tg_id = fm.tg_id
            WHERE fm.fort_id=$1
            ORDER BY
              CASE WHEN fm.role = 'hetman' THEN 0 ELSE 10 END,
              p.level DESC,
              p.name ASC
            """,
            fort_id,
        )

        members = [
            MemberItem(
                tg_id=int(r["tg_id"]),
                name=r["name"] or "Безіменний козак",
                role=r["role"] or "voin",
                level=int(r["level"] or 1),
            )
            for r in rows
        ]

        return MembersResponse(ok=True, members=members)


# ─────────────────────────────────────────────
# ЗАЯВКИ: список + схвалення / відхилення
# ─────────────────────────────────────────────


@router.get("/applications", response_model=ApplicationsResponse)
async def get_applications(tg_id: int = Depends(get_tg_id)) -> ApplicationsResponse:
    """
    GET /api/zastavy/applications
    tg_id береться з initData (має бути гетьман).
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT fort_id FROM fort_members WHERE tg_id=$1 AND role='hetman'",
            tg_id,
        )
        if not row:
            return ApplicationsResponse(ok=False, error="Ти не є гетьманом.")

        fid = int(row["fort_id"])

        apps = await conn.fetch(
            """
            SELECT fa.id, fa.tg_id, fa.created_at, p.name
            FROM fort_applications fa
            LEFT JOIN players p ON p.tg_id = fa.tg_id
            WHERE fa.fort_id=$1
            ORDER BY fa.created_at ASC, fa.id ASC
            """,
            fid,
        )

        return ApplicationsResponse(
            ok=True,
            applications=[
                ApplicationItem(
                    id=int(r["id"]),
                    tg_id=int(r["tg_id"]),
                    player_name=r["name"] or "??",
                    created_at=(r["created_at"].isoformat() if r["created_at"] is not None else ""),
                )
                for r in apps
            ],
        )


@router.post("/applications/approve", response_model=AppDecisionResponse)
async def approve_application(
    payload: AppDecisionRequest,
    tg_id: int = Depends(get_tg_id),
) -> AppDecisionResponse:
    """
    Гетьман приймає заявку:
      - перевіряємо, що він гетьман саме цієї застави
      - додаємо target у fort_members
      - видаляємо заявку
    tg_id береться з initData.
    """
    app_id = payload.application_id

    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            async with conn.transaction():
                app = await conn.fetchrow(
                    "SELECT tg_id, fort_id FROM fort_applications WHERE id=$1",
                    app_id,
                )
                if not app:
                    return AppDecisionResponse(ok=False, error="Заявки не знайдено.")

                target_id = int(app["tg_id"])
                fort_id = int(app["fort_id"])

                leader = await conn.fetchrow(
                    "SELECT 1 FROM fort_members WHERE tg_id=$1 AND fort_id=$2 AND role='hetman'",
                    tg_id,
                    fort_id,
                )
                if not leader:
                    return AppDecisionResponse(ok=False, error="Ти не гетьман цієї застави.")

                row = await conn.fetchrow("SELECT fort_id FROM fort_members WHERE tg_id=$1", target_id)
                if row and int(row["fort_id"]) != fort_id:
                    await conn.execute("DELETE FROM fort_applications WHERE id=$1", app_id)
                    return AppDecisionResponse(ok=False, error="Гравець уже вступив в іншу заставу.")

                await conn.execute(
                    "INSERT INTO fort_members (tg_id, fort_id, role) VALUES ($1, $2, 'voin')",
                    target_id,
                    fort_id,
                )

                await conn.execute("DELETE FROM fort_applications WHERE id=$1", app_id)

            return AppDecisionResponse(ok=True)
        except Exception as e:
            return AppDecisionResponse(ok=False, error=str(e))


@router.post("/applications/reject", response_model=AppDecisionResponse)
async def reject_application(
    payload: AppDecisionRequest,
    tg_id: int = Depends(get_tg_id),
) -> AppDecisionResponse:
    """
    Гетьман відхиляє заявку — просто видаляємо, але перевіряємо,
    що він гетьман цієї застави.
    tg_id береться з initData.
    """
    app_id = payload.application_id

    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            async with conn.transaction():
                app = await conn.fetchrow(
                    "SELECT fort_id FROM fort_applications WHERE id=$1",
                    app_id,
                )
                if not app:
                    return AppDecisionResponse(ok=False, error="Заявки не знайдено.")

                fort_id = int(app["fort_id"])

                leader = await conn.fetchrow(
                    "SELECT 1 FROM fort_members WHERE tg_id=$1 AND fort_id=$2 AND role='hetman'",
                    tg_id,
                    fort_id,
                )
                if not leader:
                    return AppDecisionResponse(ok=False, error="Ти не гетьман цієї застави.")

                await conn.execute("DELETE FROM fort_applications WHERE id=$1", app_id)

            return AppDecisionResponse(ok=True)
        except Exception as e:
            return AppDecisionResponse(ok=False, error=str(e))


# ─────────────────────────────────────────────
# КАЗНА ЗАСТАВИ
# ─────────────────────────────────────────────


@router.get("/treasury", response_model=TreasuryStateResponse)
async def get_treasury(tg_id: int = Depends(get_tg_id)) -> TreasuryStateResponse:
    """
    GET /api/zastavy/treasury
    tg_id береться з initData.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT fort_id FROM fort_members WHERE tg_id=$1", tg_id)
        if not row:
            return TreasuryStateResponse(ok=False, error="Ти не перебуваєш у жодній заставі.")

        zastava_id = int(row["fort_id"])
        treasury = await _load_treasury(conn, zastava_id)
        return TreasuryStateResponse(ok=True, treasury=treasury)


@router.get("/treasury/log", response_model=TreasuryLogResponse)
async def get_treasury_log(
    limit: int = 50,
    offset: int = 0,
    tg_id: int = Depends(get_tg_id),
) -> TreasuryLogResponse:
    """
    GET /api/zastavy/treasury/log?limit=50&offset=0
    tg_id береться з initData.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT fort_id FROM fort_members WHERE tg_id=$1", tg_id)
        if not row:
            return TreasuryLogResponse(ok=False, items=[], error="Ти не перебуваєш у жодній заставі.")

        zastava_id = int(row["fort_id"])

        rows = await conn.fetch(
            """
            SELECT id,
                   zastava_id,
                   tg_id,
                   delta_chervontsi,
                   delta_kleynody,
                   action,
                   source,
                   comment,
                   created_at
            FROM fort_treasury_log
            WHERE zastava_id = $1
            ORDER BY created_at DESC, id DESC
            LIMIT $2 OFFSET $3
            """,
            zastava_id,
            limit,
            offset,
        )

        items = [
            TreasuryLogItem(
                id=int(r["id"]),
                zastava_id=int(r["zastava_id"]),
                tg_id=int(r["tg_id"]),
                delta_chervontsi=int(r["delta_chervontsi"] or 0),
                delta_kleynody=int(r["delta_kleynody"] or 0),
                action=r["action"],
                source=r["source"],
                comment=r["comment"],
                created_at=(r["created_at"].isoformat() if r["created_at"] is not None else ""),
            )
            for r in rows
        ]

        return TreasuryLogResponse(ok=True, items=items)


@router.post("/treasury/deposit", response_model=TreasuryStateResponse)
async def deposit_to_treasury(
    payload: TreasuryOperationRequest,
    tg_id: int = Depends(get_tg_id),
) -> TreasuryStateResponse:
    """
    Гравець вносить гроші в казну своєї застави.
    tg_id береться з initData.
    """
    amount = payload.amount
    currency = payload.currency

    if amount <= 0:
        return TreasuryStateResponse(ok=False, error="Сума має бути більшою за нуль.")
    if currency not in ("chervontsi", "kleynody"):
        return TreasuryStateResponse(ok=False, error="Неправильна валюта.")

    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            async with conn.transaction():
                row = await conn.fetchrow("SELECT fort_id FROM fort_members WHERE tg_id=$1", tg_id)
                if not row:
                    return TreasuryStateResponse(ok=False, error="Ти не перебуваєш у жодній заставі.")

                zastava_id = int(row["fort_id"])

                bal = await conn.fetchrow(
                    "SELECT chervontsi, kleynody FROM players WHERE tg_id=$1 FOR UPDATE",
                    tg_id,
                )
                if not bal:
                    return TreasuryStateResponse(ok=False, error="Гравця не знайдено.")

                cherv = int(bal["chervontsi"] or 0)
                kleyn = int(bal["kleynody"] or 0)

                if currency == "chervontsi":
                    if cherv < amount:
                        return TreasuryStateResponse(ok=False, error="Недостатньо червонців.")
                    await conn.execute("UPDATE players SET chervontsi=$1 WHERE tg_id=$2", cherv - amount, tg_id)
                    delta_ch, delta_kl = amount, 0
                else:
                    if kleyn < amount:
                        return TreasuryStateResponse(ok=False, error="Недостатньо клейнодів.")
                    await conn.execute("UPDATE players SET kleynody=$1 WHERE tg_id=$2", kleyn - amount, tg_id)
                    delta_ch, delta_kl = 0, amount

                await conn.execute(
                    """
                    INSERT INTO fort_treasury (zastava_id, chervontsi, kleynody)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (zastava_id) DO UPDATE
                    SET chervontsi = fort_treasury.chervontsi + EXCLUDED.chervontsi,
                        kleynody   = fort_treasury.kleynody   + EXCLUDED.kleynody,
                        updated_at = now()
                    """,
                    zastava_id,
                    delta_ch,
                    delta_kl,
                )

                await conn.execute(
                    """
                    INSERT INTO fort_treasury_log (
                        zastava_id, tg_id, delta_chervontsi, delta_kleynody,
                        action, source, comment
                    )
                    VALUES ($1, $2, $3, $4, 'DEPOSIT', 'PLAYER', $5)
                    """,
                    zastava_id,
                    tg_id,
                    delta_ch,
                    delta_kl,
                    payload.comment,
                )

                treasury = await _load_treasury(conn, zastava_id)

            return TreasuryStateResponse(ok=True, treasury=treasury)
        except Exception as e:
            return TreasuryStateResponse(ok=False, error=str(e))


@router.post("/treasury/withdraw", response_model=TreasuryStateResponse)
async def withdraw_from_treasury(
    payload: TreasuryOperationRequest,
    tg_id: int = Depends(get_tg_id),
) -> TreasuryStateResponse:
    """
    Зняття з казни в кишеню гравця.
    За замовчуванням даємо це робити ТІЛЬКИ гетьману.
    tg_id береться з initData.
    """
    amount = payload.amount
    currency = payload.currency

    if amount <= 0:
        return TreasuryStateResponse(ok=False, error="Сума має бути більшою за нуль.")
    if currency not in ("chervontsi", "kleynody"):
        return TreasuryStateResponse(ok=False, error="Неправильна валюта.")

    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            async with conn.transaction():
                row = await conn.fetchrow("SELECT fort_id, role FROM fort_members WHERE tg_id=$1", tg_id)
                if not row:
                    return TreasuryStateResponse(ok=False, error="Ти не перебуваєш у жодній заставі.")

                zastava_id = int(row["fort_id"])
                role = row["role"] or "voin"
                if role != "hetman":
                    return TreasuryStateResponse(ok=False, error="Тільки гетьман може знімати з казни.")

                treas = await conn.fetchrow(
                    "SELECT chervontsi, kleynody FROM fort_treasury WHERE zastava_id=$1 FOR UPDATE",
                    zastava_id,
                )
                if not treas:
                    return TreasuryStateResponse(ok=False, error="Казна порожня.")

                t_ch = int(treas["chervontsi"] or 0)
                t_kl = int(treas["kleynody"] or 0)

                if currency == "chervontsi":
                    if t_ch < amount:
                        return TreasuryStateResponse(ok=False, error="У казні недостатньо червонців.")
                    new_t_ch, new_t_kl = t_ch - amount, t_kl
                    delta_ch, delta_kl = -amount, 0
                else:
                    if t_kl < amount:
                        return TreasuryStateResponse(ok=False, error="У казні недостатньо клейнодів.")
                    new_t_ch, new_t_kl = t_ch, t_kl - amount
                    delta_ch, delta_kl = 0, -amount

                await conn.execute(
                    """
                    UPDATE fort_treasury
                    SET chervontsi=$2, kleynody=$3, updated_at=now()
                    WHERE zastava_id=$1
                    """,
                    zastava_id,
                    new_t_ch,
                    new_t_kl,
                )

                if currency == "chervontsi":
                    await conn.execute(
                        "UPDATE players SET chervontsi = COALESCE(chervontsi,0) + $2 WHERE tg_id=$1",
                        tg_id,
                        amount,
                    )
                else:
                    await conn.execute(
                        "UPDATE players SET kleynody = COALESCE(kleynody,0) + $2 WHERE tg_id=$1",
                        tg_id,
                        amount,
                    )

                await conn.execute(
                    """
                    INSERT INTO fort_treasury_log (
                        zastava_id, tg_id, delta_chervontsi, delta_kleynody,
                        action, source, comment
                    )
                    VALUES ($1, $2, $3, $4, 'WITHDRAW', 'HETMAN', $5)
                    """,
                    zastava_id,
                    tg_id,
                    delta_ch,
                    delta_kl,
                    payload.comment,
                )

                treasury = await _load_treasury(conn, zastava_id)

            return TreasuryStateResponse(ok=True, treasury=treasury)
        except Exception as e:
            return TreasuryStateResponse(ok=False, error=str(e))