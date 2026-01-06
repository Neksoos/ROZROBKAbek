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


# ✅ ЛИШЕ "подяки"
class PostLikeResponse(BaseModel):
    ok: bool = True
    likes_cnt: int
    liked: bool


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
        "SELECT id, is_deleted, is_closed FROM forum_topics WHERE id=$1",
        topic_id,
    )
    if not row or bool(row["is_deleted"]):
        raise HTTPException(status_code=404, detail="TOPIC_NOT_FOUND")
    return dict(row)


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
        # category exists?
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

            # first post
            await conn.execute(
                """
                INSERT INTO forum_posts(topic_id, author_tg, body)
                VALUES ($1, $2, $3)
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
# Get topic + posts
# ─────────────────────────────────────────────
@router.get("/topics/{topic_id}", response_model=TopicWithPostsResponse)
async def get_topic(
    topic_id: int = Path(..., ge=1),
    me: int = Depends(get_tg_id),  # ✅ тепер використовується лише для "liked"
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

        # ✅ ВАЖЛИВО: перший пост вже показуємо як topic.body,
        # тому в списку replies його прибираємо, щоб не було дубля.
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

        # ✅ тут лише додано likes_cnt + liked, все інше як було
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

              COALESCE(lc.likes_cnt, 0) AS likes_cnt,
              CASE WHEN ml.post_id IS NULL THEN FALSE ELSE TRUE END AS liked

            FROM forum_posts fp
            LEFT JOIN players p ON p.tg_id = fp.author_tg

            LEFT JOIN (
              SELECT post_id, COUNT(1)::INT AS likes_cnt
              FROM forum_likes
              GROUP BY post_id
            ) lc ON lc.post_id = fp.id

            LEFT JOIN forum_likes ml
              ON ml.post_id = fp.id AND ml.voter_tg = $2

            WHERE fp.topic_id=$1
              AND fp.is_deleted=FALSE
              AND ($5 = 0 OR fp.id <> $5)   -- ✅ прибрали перший пост
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
# Create post (reply)
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

    pool = await get_pool()
    async with pool.acquire() as conn:
        topic = await _topic_exists(conn, topic_id)
        if bool(topic.get("is_closed")):
            raise HTTPException(status_code=400, detail="TOPIC_CLOSED")

        author_name, author_level = await _get_player_brief(conn, me)

        async with conn.transaction():
            prow = await conn.fetchrow(
                """
                INSERT INTO forum_posts(topic_id, author_tg, body)
                VALUES ($1, $2, $3)
                RETURNING
                  id,
                  topic_id,
                  author_tg,
                  body,
                  created_at::TEXT AS created_at,
                  updated_at::TEXT AS updated_at,
                  is_deleted
                """,
                topic_id,
                me,
                body,
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

        # ✅ лишаємо як було; likes_cnt/liked = дефолти (0/False)
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
        )


# ─────────────────────────────────────────────
# ✅ ЛИШЕ "подяки": toggle like
# POST /api/forum/posts/{post_id}/like
# ─────────────────────────────────────────────
@router.post("/posts/{post_id}/like", response_model=PostLikeResponse)
async def toggle_post_like(
    post_id: int = Path(..., ge=1),
    me: int = Depends(get_tg_id),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        # пост існує?
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