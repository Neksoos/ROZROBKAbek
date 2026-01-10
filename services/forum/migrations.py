# services/forum/migrations.py (або будь-де, де ти тримаєш міграції)
from __future__ import annotations

from db import get_pool


async def ensure_forum_category_requests() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        # 1) заявки на категорії
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS forum_category_requests (
              id bigserial PRIMARY KEY,

              creator_tg bigint NOT NULL,
              title text NOT NULL,
              slug text NOT NULL,
              description text NOT NULL DEFAULT '',

              -- оплата: або 1000 chervontsi або 10 kleynody
              pay_currency text NOT NULL, -- 'chervontsi' | 'kleynody'
              pay_amount int NOT NULL,

              status text NOT NULL DEFAULT 'pending', -- pending | approved | rejected

              created_at timestamptz NOT NULL DEFAULT now(),
              decided_at timestamptz NULL,
              decided_by_tg bigint NULL,
              decision_note text NULL,

              -- базові перевірки
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

        # 2) індекси (щоб швидко дивитись чергу/мої заявки/пошук)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_forum_cat_req_status_created ON forum_category_requests(status, created_at DESC);"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_forum_cat_req_creator ON forum_category_requests(creator_tg, created_at DESC);"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_forum_cat_req_slug ON forum_category_requests(slug);"
        )

        # 3) заборона дублю “pending” заявок на той самий slug
        await conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_forum_cat_req_pending_slug
            ON forum_category_requests (lower(slug))
            WHERE status = 'pending';
            """
        )

        # 4) додаткові колонки в forum_categories (щоб знати походження)
        await conn.execute(
            """
            ALTER TABLE forum_categories
              ADD COLUMN IF NOT EXISTS created_by_tg bigint,
              ADD COLUMN IF NOT EXISTS created_via_request_id bigint,
              ADD COLUMN IF NOT EXISTS approved_at timestamptz;
            """
        )

        # (опційно) FK на request (не завжди ставлять, але корисно)
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
