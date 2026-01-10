# services/forum/migrations.py
from __future__ import annotations

from db import get_pool


async def ensure_forum_category_requests() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        # 1) лог створення категорій (оплата -> створення одразу)
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS forum_category_requests (
              id bigserial PRIMARY KEY,

              creator_tg bigint NOT NULL,

              -- те, що просили створити
              title text NOT NULL,
              slug text NOT NULL,
              description text NOT NULL DEFAULT '',

              -- оплата: або 1000 chervontsi або 10 kleynody
              pay_currency text NOT NULL, -- 'chervontsi' | 'kleynody'
              pay_amount int NOT NULL,

              -- ✅ факт створення
              category_id bigint NULL, -- стане після INSERT у forum_categories

              created_at timestamptz NOT NULL DEFAULT now(),

              -- базові перевірки
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

        # 2) індекси (кулдаун/історія/пошук)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_forum_cat_req_creator_time ON forum_category_requests(creator_tg, created_at DESC);"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_forum_cat_req_created ON forum_category_requests(created_at DESC);"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_forum_cat_req_slug ON forum_category_requests(lower(slug));"
        )

        # 3) унікальність slug серед заявок (щоб не спамили тим самим)
        await conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_forum_cat_req_slug_unique
            ON forum_category_requests (lower(slug));
            """
        )

        # 4) колонки в forum_categories (походження)
        await conn.execute(
            """
            ALTER TABLE forum_categories
              ADD COLUMN IF NOT EXISTS created_by_tg bigint,
              ADD COLUMN IF NOT EXISTS is_user_created boolean NOT NULL DEFAULT false,
              ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();
            """
        )

        # 5) FK: request.category_id -> forum_categories.id
        await conn.execute(
            """
            DO $$
            BEGIN
              IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'fk_forum_cat_req_category'
              ) THEN
                ALTER TABLE forum_category_requests
                  ADD CONSTRAINT fk_forum_cat_req_category
                  FOREIGN KEY (category_id)
                  REFERENCES forum_categories(id)
                  ON DELETE SET NULL;
              END IF;
            END $$;
            """
        )