# services/forum/migrations.py
from __future__ import annotations

from db import get_pool


async def ensure_forum_paid_categories() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        # ─────────────────────────────────────────────
        # forum_categories: походження + базові поля (safe)
        # ─────────────────────────────────────────────
        await conn.execute(
            """
            ALTER TABLE forum_categories
              ADD COLUMN IF NOT EXISTS created_by_tg bigint,
              ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now(),
              ADD COLUMN IF NOT EXISTS is_hidden boolean NOT NULL DEFAULT FALSE;
            """
        )

        # slug унікальний case-insensitive
        await conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_forum_categories_slug_lower
            ON forum_categories (lower(slug));
            """
        )

        # ─────────────────────────────────────────────
        # ЛОГ платних створень категорій (антиспам + аудит)
        # ─────────────────────────────────────────────
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS forum_category_creations (
              id bigserial PRIMARY KEY,
              creator_tg bigint NOT NULL,
              category_id bigint NOT NULL REFERENCES forum_categories(id) ON DELETE CASCADE,

              pay_currency text NOT NULL, -- 'chervontsi' | 'kleynody'
              pay_amount int NOT NULL,

              created_at timestamptz NOT NULL DEFAULT now(),

              CONSTRAINT chk_forum_cat_create_currency
                CHECK (pay_currency IN ('chervontsi','kleynody')),

              CONSTRAINT chk_forum_cat_create_amount
                CHECK (
                  (pay_currency = 'chervontsi' AND pay_amount = 1000)
                  OR
                  (pay_currency = 'kleynody'   AND pay_amount = 10)
                )
            );
            """
        )

        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_forum_cat_create_creator_time
            ON forum_category_creations(creator_tg, created_at DESC);
            """
        )

        # ─────────────────────────────────────────────
        # forum_posts: reply_to_post_id (для “відповісти на пост”)
        # ─────────────────────────────────────────────
        await conn.execute(
            """
            ALTER TABLE forum_posts
              ADD COLUMN IF NOT EXISTS reply_to_post_id bigint NULL;
            """
        )

        # Індекс для швидкого рендера гілок/відповідей
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_forum_posts_reply_to
            ON forum_posts(reply_to_post_id);
            """
        )

        # FK reply_to -> forum_posts(id) (м’яко: якщо вже існує — no-op)
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