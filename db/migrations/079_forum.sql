-- forum_category_creations: лог створення платних категорій (для кулдауна/лімітів)

CREATE TABLE IF NOT EXISTS forum_category_creations (
  id BIGSERIAL PRIMARY KEY,
  creator_tg BIGINT NOT NULL,
  category_id INT NOT NULL,
  pay_currency TEXT NOT NULL CHECK (pay_currency IN ('chervontsi','kleynody')),
  pay_amount INT NOT NULL CHECK (pay_amount > 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_forum_cat_creations_creator_time
ON forum_category_creations(creator_tg, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_forum_cat_creations_category
ON forum_category_creations(category_id);