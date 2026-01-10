# routers/forum.py
from __future__ import annotations

from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Header, Query, Path
from pydantic import BaseModel, Field

from db import get_pool

router = APIRouter(prefix="/api/forum", tags=["forum"])


# ─────────────────────────────────────────────
# tg_id (через proxy з X-Tg-Id)
# ─────────────────────────────────────────────
async def get_tg_id(
    x_tg_id: Optional[str] = Header(default=None, alias="X-Tg-Id"),
    tg_id_q: Optional[int] = Query(default=None, alias="tg_id"),
) -> int:
    if tg_id_q:
        return int(tg_id_q)
    if not x_tg_id:
        raise HTTPException(status_code=401, detail="Missing X-Tg-Id")
    try:
        v = int(x_tg_id)
        if v <= 0:
            raise ValueError()
        return v
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid X-Tg-Id")


# ─────────────────────────────────────────────
# Forum settings
# ─────────────────────────────────────────────
FORUM_CAT_COST = {"chervontsi": 1000, "kleynody": 10}
FORUM_CAT_MIN_LEVEL = 3
FORUM_CAT_COOLDOWN_HOURS = 24
FORUM_CAT_MAX_PER_30D = 3


# ─────────────────────────────────────────────
# DTOs
# ─────────────────────────────────────────────
class CategoryDTO(BaseModel):
    id: int
    slug: str
    title: str
    sort_order: int


class TopicShortDTO(BaseModel):
    id: int
    category_id: int
    title: str

    author_tg: int
    author_name: str
    author_level: int

    created_at: str
    last_post_at: str
    replies_cnt: int

    is_closed: bool
    is_pinned: bool


class TopicFullDTO(TopicShortDTO):
    body: str


class PostDTO(BaseModel):
    id: int
    topic_id: int
    author_tg: int
    author_name: str
    author_level: int
    body: str
    created_at: str
    updated_at: str
    is_deleted: bool

    # ✅ reply-to
    reply_to_post_id: Optional[int] = None
    reply_to_author_tg: Optional[int] = None
    reply_to_author_name: Optional[str] = None
    reply_to_body_snippet: Optional[str] = None

    # ✅ ЛИШЕ "подяки"
    likes_cnt: int = 0
    liked: bool = False


class TopicsListResponse(BaseModel):
    ok: bool = True
    topics: List[TopicShortDTO]
    page: int
    per_page: int
    has_more: bool


class TopicWithPostsResponse(BaseModel):
    ok: bool = True
    topic: TopicFullDTO
    posts: List[PostDTO]
    page: int
    per_page: int
    has_more: bool


class TopicCreateRequest(BaseModel):
    category_id: int = Field(..., ge=1)
    title: str = Field(..., min_length=3, max_length=150)
    body: str = Field(..., min_length=1, max_length=4000)


class PostCreateRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=4000)
    reply_to_post_id: Optional[int] = Field(default=None, ge=1)


class PostLikeResponse(BaseModel):
    ok: bool = True
    likes_cnt: int
    liked: bool


class CategoryCreatePaidRequest(BaseModel):
    title: str = Field(..., min_length=3, max_length=60)
    description: str = Field(default="", max_length=400)
    pay_currency: str = Field(..., pattern="^(chervontsi|kleynody)$")


class CategoryCreatePaidResponse(BaseModel):
    ok: bool = True
    category: CategoryDTO
    paid_currency: str
    paid_amount: int


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def _norm_pagination(page: int, per_page: int) -> tuple[int, int]:
    if page < 1:
        page = 1
    if per_page < 1:
        per_page = 20
    if per_page > 50:
        per_page = 50
    return page, per_page


async def _get_player_brief(conn, tg_id: int) -> tuple[str, int]:
    row = await conn.fetchrow(
        "SELECT COALESCE(name,'') AS name, COALESCE(level,1) AS level FROM players WHERE tg_id=$1",
        tg_id,
    )
    if not row:
        return ("Невідомий", 1)
    name = (row["name"] or "").strip() or "Невідомий"
    level = int(row["level"] or 1)
    return (name, level)


async def _topic_exists(conn, topic_id: int) -> Dict[str, Any]:
    row = await conn.fetchrow(
        "SELECT id, is_deleted, is_closed, title FROM forum_topics WHERE id=$1",
        topic_id,
    )
    if not row or bool(row["is_deleted"]):
        raise HTTPException(status_code=404, detail="TOPIC_NOT_FOUND")
    return dict(row)


def _snippet(s: str, n: int = 80) -> str:
    s = (s or "").strip().replace("\n", " ")
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def _make_slug(title: str) -> str:
    # простий slugger без залежностей: лат/цифри/дефіс
    s = (title or "").strip().lower()
    out = []
    prev_dash = False
    for ch in s:
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif ch in (" ", "_", "-", ".", "/"):
            if not prev_dash:
                out.append("-")
                prev_dash = True
    slug = "".join(out).strip("-")
    if not slug:
        slug = "category"
    return slug[:48]


async def _send_forum_reply_mail(
    conn,
    *,
    sender_tg: int,
    sender_name: str,
    recipient_tg: int,
    recipient_name: str,
    topic_title: str,
    reply_text: str,
) -> None:
    if recipient_tg <= 0 or recipient_tg == sender_tg:
        return

    body = (
        f"💬 Відповідь на форумі\n"
        f"Тема: {topic_title}\n"
        f"Від: {sender_name}\n\n"
        f"{_snippet(reply_text, 400)}"
    )

    # schema A
    try:
        await conn.execute(
            """
            INSERT INTO mail_messages(sender_tg, recipient_tg, body, created_at, is_read, deleted_by_recipient, sender_name)
            VALUES ($1, $2, $3, now(), FALSE, FALSE, $4)
            """,
            sender_tg,
            recipient_tg,
            body,
            sender_name,
        )
        return
    except Exception:
        pass

    # schema B
    try:
        await conn.execute(
            """
            INSERT INTO mail_messages(sender_tg, rcpt_tg, rcpt_name, sender_name, body, sent_at, deleted_in, deleted_out, deleted_by_recipient, is_read)
            VALUES ($1, $2, $3, $4, $5, now(), FALSE, FALSE, FALSE, FALSE)
            """,
            sender_tg,
            recipient_tg,
            recipient_name,
            sender_name,
            body,
        )
        return
    except Exception:
        return


# ─────────────────────────────────────────────
# Categories
# ─────────────────────────────────────────────
@router.get("/categories", response_model=List[CategoryDTO])
async def list_categories():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, slug, title, sort_order
            FROM forum_categories
            WHERE is_hidden = FALSE
            ORDER BY sort_order ASC, id ASC
            """
        )
        return [CategoryDTO(**dict(r)) for r in (rows or [])]


@router.post("/categories/create-paid", response_model=CategoryCreatePaidResponse)
async def create_category_paid(payload: CategoryCreatePaidRequest, me: int = Depends(get_tg_id)):
    title = payload.title.strip()
    pay_currency = payload.pay_currency
    pay_amount = int(FORUM_CAT_COST[pay_currency])

    slug = _make_slug(title)

    pool = await get_pool()
    async with pool.acquire() as conn:
        # 1) мін рівень
        prow = await conn.fetchrow("SELECT COALESCE(level,1) AS level FROM players WHERE tg_id=$1", me)
        lvl = int(prow["level"] if prow else 1)
        if lvl < FORUM_CAT_MIN_LEVEL:
            raise HTTPException(403, detail=f"MIN_LEVEL_{FORUM_CAT_MIN_LEVEL}")

        # 2) кулдаун
        recent = await conn.fetchval(
            """
            SELECT 1
            FROM forum_category_creations
            WHERE creator_tg=$1 AND created_at > now() - ($2::interval)
            LIMIT 1
            """,
            me,
            f"{FORUM_CAT_COOLDOWN_HOURS} hours",
        )
        if recent:
            raise HTTPException(429, detail="CATEGORY_CREATE_COOLDOWN")

        # 3) ліміт 30 днів
        cnt30 = await conn.fetchval(
            """
            SELECT COUNT(1)
            FROM forum_category_creations
            WHERE creator_tg=$1 AND created_at > now() - interval '30 days'
            """,
            me,
        )
        if int(cnt30 or 0) >= FORUM_CAT_MAX_PER_30D:
            raise HTTPException(429, detail="CATEGORY_CREATE_LIMIT_30D")

        # 4) slug зайнятий?
        exists = await conn.fetchval(
            "SELECT 1 FROM forum_categories WHERE lower(slug)=lower($1) LIMIT 1",
            slug,
        )
        if exists:
            raise HTTPException(400, detail="SLUG_TAKEN")

        # 5) транзакція: списання + створення
        async with conn.transaction():
            bal = await conn.fetchrow(
                "SELECT chervontsi, kleynody FROM players WHERE tg_id=$1 FOR UPDATE",
                me,
            )
            if not bal:
                raise HTTPException(404, detail="PLAYER_NOT_FOUND")

            have_ch = int(bal["chervontsi"] or 0)
            have_kl = int(bal["kleynody"] or 0)

            if pay_currency == "chervontsi" and have_ch < pay_amount:
                raise HTTPException(400, detail="NOT_ENOUGH_CHERVONTSI")
            if pay_currency == "kleynody" and have_kl < pay_amount:
                raise HTTPException(400, detail="NOT_ENOUGH_KLEYNODY")

            if pay_currency == "chervontsi":
                await conn.execute(
                    "UPDATE players SET chervontsi = chervontsi - $2 WHERE tg_id=$1",
                    me,
                    pay_amount,
                )
            else:
                await conn.execute(
                    "UPDATE players SET kleynody = kleynody - $2 WHERE tg_id=$1",
                    me,
                    pay_amount,
                )

            max_sort = await conn.fetchval("SELECT COALESCE(MAX(sort_order),0) FROM forum_categories")
            sort_order = int(max_sort or 0) + 1

            crow = await conn.fetchrow(
                """
                INSERT INTO forum_categories(slug, title, sort_order, is_hidden, created_by_tg)
                VALUES ($1, $2, $3, FALSE, $4)
                RETURNING id, slug, title, sort_order
                """,
                slug,
                title,
                sort_order,
                me,
            )
            cat_id = int(crow["id"])

            await conn.execute(
                """
                INSERT INTO forum_category_creations(creator_tg, category_id, pay_currency, pay_amount)
                VALUES ($1, $2, $3, $4)
                """,
                me,
                cat_id,
                pay_currency,
                pay_amount,
            )

        return CategoryCreatePaidResponse(
            category=CategoryDTO(
                id=int(crow["id"]),
                slug=str(crow["slug"]),
                title=str(crow["title"]),
                sort_order=int(crow["sort_order"]),
            ),
            paid_currency=pay_currency,
            paid_amount=pay_amount,
        )


# ─────────────────────────────────────────────
# Topics list
# ─────────────────────────────────────────────
@router.get("/topics", response_model=TopicsListResponse)
async def list_topics(
    me: int = Depends(get_tg_id),
    category_id: Optional[int] = Query(default=None),
    order: str = Query(default="hot"),  # hot | new | mine
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=50),
):
    page, per_page = _norm_pagination(page, per_page)
    offset = (page - 1) * per_page

    pool = await get_pool()
    async with pool.acquire() as conn:
        where = ["ft.is_deleted = FALSE"]
        params: list = []

        if category_id is not None:
            where.append(f"ft.category_id = ${len(params) + 1}")
            params.append(int(category_id))

        if order == "mine":
            where.append(f"ft.author_tg = ${len(params) + 1}")
            params.append(int(me))

        where_sql = " WHERE " + " AND ".join(where)

        if order == "new":
            order_sql = " ORDER BY ft.created_at DESC"
        elif order == "mine":
            order_sql = " ORDER BY ft.created_at DESC"
        else:
            order_sql = " ORDER BY ft.is_pinned DESC, ft.replies_cnt DESC, ft.last_post_at DESC"

        rows = await conn.fetch(
            f"""
            SELECT
              ft.id,
              ft.category_id,
              ft.title,
              ft.author_tg,
              COALESCE(p.name,'') AS author_name,
              COALESCE(p.level,1) AS author_level,
              ft.created_at::TEXT AS created_at,
              ft.last_post_at::TEXT AS last_post_at,
              ft.replies_cnt,
              ft.is_closed,
              ft.is_pinned
            FROM forum_topics ft
            LEFT JOIN players p ON p.tg_id = ft.author_tg
            {where_sql}
            {order_sql}
            LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
            """,
            *params,
            per_page,
            offset,
        )

        topics = [TopicShortDTO(**dict(r)) for r in (rows or [])]
        return TopicsListResponse(
            topics=topics,
            page=page,
            per_page=per_page,
            has_more=(len(topics) == per_page),
        )


# ─────────────────────────────────────────────
# Create topic (topic + first post)
# ─────────────────────────────────────────────
@router.post("/topics", response_model=TopicFullDTO)
async def create_topic(payload: TopicCreateRequest, me: int = Depends(get_tg_id)):
    title = payload.title.strip()
    body = payload.body.strip()
    if not title or not body:
        raise HTTPException(status_code=400, detail="EMPTY_TITLE_OR_BODY")

    pool = await get_pool()
    async with pool.acquire() as conn:
        cat = await conn.fetchval(
            "SELECT 1 FROM forum_categories WHERE id=$1 AND is_hidden=FALSE",
            payload.category_id,
        )
        if not cat:
            raise HTTPException(status_code=400, detail="CATEGORY_NOT_FOUND")

        author_name, author_level = await _get_player_brief(conn, me)

        async with conn.transaction():
            trow = await conn.fetchrow(
                """
                INSERT INTO forum_topics(category_id, author_tg, title, body)
                VALUES ($1, $2, $3, $4)
                RETURNING
                  id,
                  category_id,
                  author_tg,
                  title,
                  body,
                  created_at::TEXT AS created_at,
                  last_post_at::TEXT AS last_post_at,
                  replies_cnt,
                  is_closed,
                  is_pinned
                """,
                payload.category_id,
                me,
                title,
                body,
            )
            if not trow:
                raise HTTPException(status_code=500, detail="TOPIC_CREATE_FAILED")

            topic_id = int(trow["id"])

            await conn.execute(
                """
                INSERT INTO forum_posts(topic_id, author_tg, body, reply_to_post_id)
                VALUES ($1, $2, $3, NULL)
                """,
                topic_id,
                me,
                body,
            )

        return TopicFullDTO(
            id=topic_id,
            category_id=int(trow["category_id"]),
            title=str(trow["title"]),
            author_tg=int(trow["author_tg"]),
            author_name=author_name,
            author_level=author_level,
            created_at=str(trow["created_at"]),
            last_post_at=str(trow["last_post_at"]),
            replies_cnt=int(trow["replies_cnt"] or 0),
            is_closed=bool(trow["is_closed"]),
            is_pinned=bool(trow["is_pinned"]),
            body=str(trow["body"]),
        )


# ─────────────────────────────────────────────
# Get topic + posts (with reply-to info)
# ─────────────────────────────────────────────
@router.get("/topics/{topic_id}", response_model=TopicWithPostsResponse)
async def get_topic(
    topic_id: int = Path(..., ge=1),
    me: int = Depends(get_tg_id),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=50),
):
    page, per_page = _norm_pagination(page, per_page)
    offset = (page - 1) * per_page

    pool = await get_pool()
    async with pool.acquire() as conn:
        trow = await conn.fetchrow(
            """
            SELECT
              ft.id,
              ft.category_id,
              ft.title,
              ft.body,
              ft.author_tg,
              COALESCE(p.name,'') AS author_name,
              COALESCE(p.level,1) AS author_level,
              ft.created_at::TEXT AS created_at,
              ft.last_post_at::TEXT AS last_post_at,
              ft.replies_cnt,
              ft.is_closed,
              ft.is_pinned,
              ft.is_deleted
            FROM forum_topics ft
            LEFT JOIN players p ON p.tg_id = ft.author_tg
            WHERE ft.id=$1
            """,
            topic_id,
        )
        if not trow or bool(trow["is_deleted"]):
            raise HTTPException(status_code=404, detail="TOPIC_NOT_FOUND")

        first_post_id = await conn.fetchval(
            """
            SELECT id
            FROM forum_posts
            WHERE topic_id=$1 AND is_deleted=FALSE
            ORDER BY created_at ASC, id ASC
            LIMIT 1
            """,
            topic_id,
        )
        first_post_id = int(first_post_id or 0)

        rows = await conn.fetch(
            """
            SELECT
              fp.id,
              fp.topic_id,
              fp.author_tg,
              COALESCE(p.name,'') AS author_name,
              COALESCE(p.level,1) AS author_level,
              fp.body,
              fp.created_at::TEXT AS created_at,
              fp.updated_at::TEXT AS updated_at,
              fp.is_deleted,

              fp.reply_to_post_id,
              rp.author_tg AS reply_to_author_tg,
              COALESCE(rpp.name,'') AS reply_to_author_name,
              LEFT(COALESCE(rp.body,''), 120) AS reply_to_body_snippet,

              COALESCE(lc.likes_cnt, 0) AS likes_cnt,
              CASE WHEN ml.post_id IS NULL THEN FALSE ELSE TRUE END AS liked

            FROM forum_posts fp
            LEFT JOIN players p ON p.tg_id = fp.author_tg

            LEFT JOIN forum_posts rp ON rp.id = fp.reply_to_post_id
            LEFT JOIN players rpp ON rpp.tg_id = rp.author_tg

            LEFT JOIN (
              SELECT post_id, COUNT(1)::INT AS likes_cnt
              FROM forum_likes
              GROUP BY post_id
            ) lc ON lc.post_id = fp.id

            LEFT JOIN forum_likes ml
              ON ml.post_id = fp.id AND ml.voter_tg = $2

            WHERE fp.topic_id=$1
              AND fp.is_deleted=FALSE
              AND ($5 = 0 OR fp.id <> $5)
            ORDER BY fp.created_at ASC
            LIMIT $3 OFFSET $4
            """,
            topic_id,
            me,
            per_page,
            offset,
            first_post_id,
        )

        total_cnt = await conn.fetchval(
            """
            SELECT COUNT(1)
            FROM forum_posts
            WHERE topic_id=$1 AND is_deleted=FALSE
              AND ($2 = 0 OR id <> $2)
            """,
            topic_id,
            first_post_id,
        )
        total_cnt = int(total_cnt or 0)

        topic = TopicFullDTO(
            id=int(trow["id"]),
            category_id=int(trow["category_id"]),
            title=str(trow["title"]),
            author_tg=int(trow["author_tg"]),
            author_name=str(trow["author_name"] or ""),
            author_level=int(trow["author_level"] or 1),
            created_at=str(trow["created_at"]),
            last_post_at=str(trow["last_post_at"]),
            replies_cnt=int(trow["replies_cnt"] or 0),
            is_closed=bool(trow["is_closed"]),
            is_pinned=bool(trow["is_pinned"]),
            body=str(trow["body"] or ""),
        )

        posts = [PostDTO(**dict(r)) for r in (rows or [])]
        has_more = (offset + len(posts)) < total_cnt

        return TopicWithPostsResponse(
            topic=topic,
            posts=posts,
            page=page,
            per_page=per_page,
            has_more=has_more,
        )


# ─────────────────────────────────────────────
# Create post (reply) + mail notification
# ─────────────────────────────────────────────
@router.post("/topics/{topic_id}/posts", response_model=PostDTO)
async def create_post(
    topic_id: int = Path(..., ge=1),
    payload: PostCreateRequest = ...,
    me: int = Depends(get_tg_id),
):
    body = payload.body.strip()
    if not body:
        raise HTTPException(status_code=400, detail="EMPTY_BODY")

    reply_to_post_id = int(payload.reply_to_post_id) if payload.reply_to_post_id else None

    pool = await get_pool()
    async with pool.acquire() as conn:
        topic = await _topic_exists(conn, topic_id)
        if bool(topic.get("is_closed")):
            raise HTTPException(status_code=400, detail="TOPIC_CLOSED")

        author_name, author_level = await _get_player_brief(conn, me)

        reply_target: Optional[Dict[str, Any]] = None
        if reply_to_post_id:
            rrow = await conn.fetchrow(
                """
                SELECT fp.id, fp.topic_id, fp.author_tg, COALESCE(p.name,'') AS author_name, fp.body
                FROM forum_posts fp
                LEFT JOIN players p ON p.tg_id = fp.author_tg
                WHERE fp.id=$1 AND fp.is_deleted=FALSE
                """,
                reply_to_post_id,
            )
            if not rrow:
                raise HTTPException(status_code=404, detail="REPLY_TARGET_NOT_FOUND")
            if int(rrow["topic_id"]) != int(topic_id):
                raise HTTPException(status_code=400, detail="REPLY_TARGET_WRONG_TOPIC")
            reply_target = dict(rrow)

        async with conn.transaction():
            prow = await conn.fetchrow(
                """
                INSERT INTO forum_posts(topic_id, author_tg, body, reply_to_post_id)
                VALUES ($1, $2, $3, $4)
                RETURNING
                  id,
                  topic_id,
                  author_tg,
                  body,
                  reply_to_post_id,
                  created_at::TEXT AS created_at,
                  updated_at::TEXT AS updated_at,
                  is_deleted
                """,
                topic_id,
                me,
                body,
                reply_to_post_id,
            )

            await conn.execute(
                """
                UPDATE forum_topics
                SET last_post_at = now(),
                    replies_cnt  = replies_cnt + 1
                WHERE id=$1
                """,
                topic_id,
            )

            if reply_target:
                await _send_forum_reply_mail(
                    conn,
                    sender_tg=me,
                    sender_name=author_name,
                    recipient_tg=int(reply_target["author_tg"] or 0),
                    recipient_name=str(reply_target.get("author_name") or "Гравець"),
                    topic_title=str(topic.get("title") or "Форум"),
                    reply_text=body,
                )

        return PostDTO(
            id=int(prow["id"]),
            topic_id=int(prow["topic_id"]),
            author_tg=int(prow["author_tg"]),
            author_name=author_name,
            author_level=author_level,
            body=str(prow["body"]),
            created_at=str(prow["created_at"]),
            updated_at=str(prow["updated_at"]),
            is_deleted=bool(prow["is_deleted"]),
            reply_to_post_id=int(prow["reply_to_post_id"]) if prow["reply_to_post_id"] else None,
            reply_to_author_tg=int(reply_target["author_tg"]) if reply_target else None,
            reply_to_author_name=str(reply_target.get("author_name") or "") if reply_target else None,
            reply_to_body_snippet=_snippet(str(reply_target.get("body") or ""), 120) if reply_target else None,
        )


# ─────────────────────────────────────────────
# Toggle like (подяка)
# ─────────────────────────────────────────────
@router.post("/posts/{post_id}/like", response_model=PostLikeResponse)
async def toggle_post_like(
    post_id: int = Path(..., ge=1),
    me: int = Depends(get_tg_id),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM forum_posts WHERE id=$1 AND is_deleted=FALSE",
            post_id,
        )
        if not exists:
            raise HTTPException(status_code=404, detail="POST_NOT_FOUND")

        async with conn.transaction():
            already = await conn.fetchval(
                "SELECT 1 FROM forum_likes WHERE post_id=$1 AND voter_tg=$2",
                post_id,
                me,
            )

            if already:
                await conn.execute(
                    "DELETE FROM forum_likes WHERE post_id=$1 AND voter_tg=$2",
                    post_id,
                    me,
                )
                liked = False
            else:
                await conn.execute(
                    "INSERT INTO forum_likes(post_id, voter_tg) VALUES ($1, $2)",
                    post_id,
                    me,
                )
                liked = True

            likes_cnt = await conn.fetchval(
                "SELECT COUNT(1) FROM forum_likes WHERE post_id=$1",
                post_id,
            )

        return PostLikeResponse(likes_cnt=int(likes_cnt or 0), liked=liked)