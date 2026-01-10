-- ─────────────────────────────────────────────
-- Платні створення категорій форуму
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS forum_category_creations (
    id            BIGSERIAL PRIMARY KEY,

    creator_tg    BIGINT NOT NULL,
    category_id   INT NOT NULL,

    pay_currency  TEXT NOT NULL CHECK (pay_currency IN ('chervontsi', 'kleynody')),
    pay_amount    INT  NOT NULL,

    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Індекс: швидкі перевірки кулдауну / лімітів
CREATE INDEX IF NOT EXISTS idx_forum_cat_creations_creator_time
ON forum_category_creations (creator_tg, created_at DESC);