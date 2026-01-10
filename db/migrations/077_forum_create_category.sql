BEGIN;

-- =========================================================
-- Forum categories: required fields for paid creation
-- =========================================================

ALTER TABLE forum_categories
ADD COLUMN IF NOT EXISTS slug TEXT;

ALTER TABLE forum_categories
ADD COLUMN IF NOT EXISTS is_hidden BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE forum_categories
ADD COLUMN IF NOT EXISTS created_by_tg BIGINT;

-- slug must be unique (case-insensitive)
CREATE UNIQUE INDEX IF NOT EXISTS ux_forum_categories_slug
ON forum_categories (lower(slug));

-- =========================================================
-- Paid category creation log (cooldown / limits)
-- =========================================================

CREATE TABLE IF NOT EXISTS forum_category_creations (
  id BIGSERIAL PRIMARY KEY,
  creator_tg BIGINT NOT NULL,
  category_id INT NOT NULL REFERENCES forum_categories(id) ON DELETE CASCADE,
  pay_currency TEXT NOT NULL CHECK (pay_currency IN ('chervontsi','kleynody')),
  pay_amount INT NOT NULL CHECK (pay_amount > 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_forum_cat_creations_creator_time
  ON forum_category_creations(creator_tg, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_forum_cat_creations_category
  ON forum_category_creations(category_id);

COMMIT;