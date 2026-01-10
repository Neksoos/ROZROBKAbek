-- =========================================================
-- Forum: replies + paid category requests
-- =========================================================

BEGIN;

-- ---------------------------------------------------------
-- 1) Replies to specific posts
-- ---------------------------------------------------------
ALTER TABLE forum_posts
ADD COLUMN IF NOT EXISTS reply_to_post_id INT;

-- FK (reply_to -> forum_posts.id)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'fk_forum_posts_reply_to_post'
  ) THEN
    ALTER TABLE forum_posts
    ADD CONSTRAINT fk_forum_posts_reply_to_post
      FOREIGN KEY (reply_to_post_id)
      REFERENCES forum_posts(id)
      ON DELETE SET NULL;
  END IF;
END$$;

CREATE INDEX IF NOT EXISTS idx_forum_posts_reply_to
  ON forum_posts(reply_to_post_id);

-- ---------------------------------------------------------
-- 2) Paid category creation: requests table
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS forum_category_requests (
  id BIGSERIAL PRIMARY KEY,

  requester_tg BIGINT NOT NULL,
  requester_name TEXT NOT NULL DEFAULT '',

  -- what they want
  slug TEXT NOT NULL,
  title TEXT NOT NULL,
  sort_order INT NOT NULL DEFAULT 100,

  -- payment & pricing snapshot
  price_chervontsi INT NOT NULL DEFAULT 0,
  payment_status TEXT NOT NULL DEFAULT 'unpaid',  -- unpaid | paid | refunded

  -- moderation lifecycle
  status TEXT NOT NULL DEFAULT 'pending',         -- pending | approved | rejected | canceled
  moderator_tg BIGINT,
  moderator_note TEXT NOT NULL DEFAULT '',

  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at TIMESTAMPTZ
);

-- Uniqueness: active requests should not spam same slug
-- (Allows same slug in history if canceled/rejected? We'll still guard by status in code)
CREATE INDEX IF NOT EXISTS idx_forum_catreq_status
  ON forum_category_requests(status);

CREATE INDEX IF NOT EXISTS idx_forum_catreq_requester
  ON forum_category_requests(requester_tg);

-- hard uniqueness for slug among requests (optional, can be too strict)
-- safer: keep it non-unique and enforce in code.
-- If you DO want strict, uncomment:
-- CREATE UNIQUE INDEX IF NOT EXISTS uq_forum_catreq_slug ON forum_category_requests(slug);

-- Also ensure categories slug unique (recommended)
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_name='forum_categories'
  ) THEN
    -- Create unique index only if it doesn't exist and if slug column exists
    IF EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_name='forum_categories' AND column_name='slug'
    ) THEN
      IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE indexname = 'uq_forum_categories_slug'
      ) THEN
        CREATE UNIQUE INDEX uq_forum_categories_slug
          ON forum_categories(slug);
      END IF;
    END IF;
  END IF;
END$$;

COMMIT;