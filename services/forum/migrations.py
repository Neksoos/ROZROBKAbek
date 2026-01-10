# services/forum/migrations.py
from __future__ import annotations

from db import get_pool


async def ensure_forum_schema() -> None:
    """
    Повний мінімальний форум-схематик + лайки/підписки + reply_to + заявки на категорії.
    Без “адміна” — заявки просто висять у status='pending', а хто/як їх апрувить додаси пізніше.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # ─────────────────────────────────────────────
        # 0) extensions (на всяк випадок)
        # ─────────────────────────────────────────────
        await conn.execute("""CREATE EXTENSION IF NOT EXISTS pgcrypto;""")

        # ─────────────────────────────────────────────
        # 1) forum_categories
        # ─────────────────────────────────────────────
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS forum_categories (
              id bigserial PRIMARY KEY,
              slug text NOT NULL,
              title text NOT NULL,
              sort_order int NOT NULL DEFAULT 100,
              is_hidden boolean NOT NULL DEFAULT FALSE,
              created_at timestamptz NOT NULL DEFAULT now()
            );
            """
        )

        await conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS ux_forum_categories_slug ON forum_categories (lower(slug));"""
        )
        await conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_forum_categories_sort ON forum_categories (sort_order ASC, id ASC);"""
        )

        # ─────────────────────────────────────────────
        # 2) forum_topics
        # ─────────────────────────────────────────────
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS forum_topics (
              id bigserial PRIMARY KEY,

              category_id bigint NOT NULL REFERENCES forum_categories(id) ON DELETE CASCADE,

              author_tg bigint NOT NULL,
              title text NOT NULL,
              body text NOT NULL DEFAULT '',

              created_at timestamptz NOT NULL DEFAULT now(),
              last_post_at timestamptz NOT NULL DEFAULT now(),
              replies_cnt int NOT NULL DEFAULT 0,

              is_closed boolean NOT NULL DEFAULT FALSE,
              is_pinned boolean NOT NULL DEFAULT FALSE,
              is_deleted boolean NOT NULL DEFAULT FALSE
            );
            """
        )

        await conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_forum_topics_cat_last ON forum_topics (category_id, last_post_at DESC);"""
        )
        await conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_forum_topics_author_created ON forum_topics (author_tg, created_at DESC);"""
        )
        await conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_forum_topics_hot ON forum_topics (is_pinned DESC, replies_cnt DESC, last_post_at DESC);"""
        )

        # ─────────────────────────────────────────────
        # 3) forum_posts (із reply_to_post_id)
        # ─────────────────────────────────────────────
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS forum_posts (
              id bigserial PRIMARY KEY,

              topic_id bigint NOT NULL REFERENCES forum_topics(id) ON DELETE CASCADE,

              author_tg bigint NOT NULL,
              body text NOT NULL,

              created_at timestamptz NOT NULL DEFAULT now(),
              updated_at timestamptz NOT NULL DEFAULT now(),

              is_deleted boolean NOT NULL DEFAULT FALSE,

              -- ✅ reply-to
              reply_to_post_id bigint NULL
            );
            """
        )

        # якщо таблиця вже була — додамо колонку reply_to_post_id
        await conn.execute(
            """
            ALTER TABLE forum_posts
              ADD COLUMN IF NOT EXISTS reply_to_post_id bigint;
            """
        )

        # FK для reply_to_post_id (опційно, через DO-блок щоб не падало)
        await conn.execute(
            """
            DO $$
            BEGIN
              IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'fk_forum_posts_reply_to'
              ) THEN
                ALTER TABLE forum_posts
                  ADD CONSTRAINT fk_forum_posts_reply_to
                  FOREIGN KEY (reply_to_post_id)
                  REFERENCES forum_posts(id)
                  ON DELETE SET NULL;
              END IF;
            END $$;
            """
        )

        await conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_forum_posts_topic_created ON forum_posts (topic_id, created_at ASC, id ASC);"""
        )
        await conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_forum_posts_author_created ON forum_posts (author_tg, created_at DESC);"""
        )
        await conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_forum_posts_reply_to ON forum_posts (reply_to_post_id);"""
        )

        # ─────────────────────────────────────────────
        # 4) forum_likes (тільки "подяки")
        # ─────────────────────────────────────────────
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS forum_likes (
              post_id bigint NOT NULL REFERENCES forum_posts(id) ON DELETE CASCADE,
              voter_tg bigint NOT NULL,
              created_at timestamptz NOT NULL DEFAULT now(),
              PRIMARY KEY (post_id, voter_tg)
            );
            """
        )
        await conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_forum_likes_post ON forum_likes (post_id);"""
        )
        await conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_forum_likes_voter ON forum_likes (voter_tg, created_at DESC);"""
        )

        # ─────────────────────────────────────────────
        # 5) forum_topic_subscriptions (щоб "Стежити")
        # ─────────────────────────────────────────────
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS forum_topic_subscriptions (
              topic_id bigint NOT NULL REFERENCES forum_topics(id) ON DELETE CASCADE,
              tg_id bigint NOT NULL,
              created_at timestamptz NOT NULL DEFAULT now(),
              PRIMARY KEY (topic_id, tg_id)
            );
            """
        )
        await conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_forum_subs_tg ON forum_topic_subscriptions (tg_id, created_at DESC);"""
        )
        await conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_forum_subs_topic ON forum_topic_subscriptions (topic_id);"""
        )

        # ─────────────────────────────────────────────
        # 6) заявки на категорії (платно: 1000 червонців або 10 клейнодів)
        # ─────────────────────────────────────────────
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS forum_category_requests (
              id bigserial PRIMARY KEY,

              creator_tg bigint NOT NULL,
              title text NOT NULL,
              slug text NOT NULL,
              description text NOT NULL DEFAULT '',

              pay_currency text NOT NULL, -- 'chervontsi' | 'kleynody'
              pay_amount int NOT NULL,

              status text NOT NULL DEFAULT 'pending', -- pending | approved | rejected

              created_at timestamptz NOT NULL DEFAULT now(),
              decided_at timestamptz NULL,
              decided_by_tg bigint NULL,
              decision_note text NULL,

              CONSTRAINT chk_forum_cat_req_status
                CHECK (status IN ('pending','approved','rejected')),

              CONSTRAINT chk_forum_cat_req_currency
                CHECK (pay_currency IN ('chervontsi','kleynody')),

              CONSTRAINT chk_forum_cat_req_amount
                CHECK (
                  (pay_currency = 'chervontsi' AND pay_amount = 1000)
                  OR
                  (pay_currency = 'kleynody'   AND pay_amount = 10)
                )
            );
            """
        )

        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_forum_cat_req_status_created ON forum_category_requests(status, created_at DESC);"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_forum_cat_req_creator ON forum_category_requests(creator_tg, created_at DESC);"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_forum_cat_req_slug ON forum_category_requests(lower(slug));"
        )

        # заборона дублю pending на той самий slug
        await conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_forum_cat_req_pending_slug
            ON forum_category_requests (lower(slug))
            WHERE status = 'pending';
            """
        )

        # ─────────────────────────────────────────────
        # 7) доп. колонки в forum_categories (походження)
        # ─────────────────────────────────────────────
        await conn.execute(
            """
            ALTER TABLE forum_categories
              ADD COLUMN IF NOT EXISTS created_by_tg bigint,
              ADD COLUMN IF NOT EXISTS created_via_request_id bigint,
              ADD COLUMN IF NOT EXISTS approved_at timestamptz;
            """
        )

        # FK на request (опційно)
        await conn.execute(
            """
            DO $$
            BEGIN
              IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'fk_forum_categories_request'
              ) THEN
                ALTER TABLE forum_categories
                  ADD CONSTRAINT fk_forum_categories_request
                  FOREIGN KEY (created_via_request_id)
                  REFERENCES forum_category_requests(id)
                  ON DELETE SET NULL;
              END IF;
            END $$;
            """
        )


# ─────────────────────────────────────────────
# Зручний аліас, якщо ти звик викликати ensure_* по-одному
# ─────────────────────────────────────────────
async def ensure_forum_category_requests() -> None:
    await ensure_forum_schema()