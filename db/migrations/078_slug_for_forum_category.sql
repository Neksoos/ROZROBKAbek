-- 1️⃣ slug (КРИТИЧНО)
ALTER TABLE forum_categories
ADD COLUMN IF NOT EXISTS slug text;

-- 2️⃣ унікальність slug (як у коді)
CREATE UNIQUE INDEX IF NOT EXISTS ux_forum_categories_slug
ON forum_categories (lower(slug));

-- 3️⃣ хто створив (для платних категорій)
ALTER TABLE forum_categories
ADD COLUMN IF NOT EXISTS created_by_tg bigint;

-- 4️⃣ походження із заявки (на майбутнє)
ALTER TABLE forum_categories
ADD COLUMN IF NOT EXISTS created_via_request_id bigint;

-- 5️⃣ коли затверджено
ALTER TABLE forum_categories
ADD COLUMN IF NOT EXISTS approved_at timestamptz;