-- 2025_12_21_tavern_food_items.sql
-- Tavern Food Shop: HP/MP restore items (borshch/varenyky/salo + kvas/kompot/birch_juice)

BEGIN;

-- -----------------------------
-- Ensure columns exist (safe)
-- -----------------------------
ALTER TABLE items ADD COLUMN IF NOT EXISTS code TEXT;
ALTER TABLE items ADD COLUMN IF NOT EXISTS name TEXT;
ALTER TABLE items ADD COLUMN IF NOT EXISTS emoji TEXT;
ALTER TABLE items ADD COLUMN IF NOT EXISTS category TEXT;
ALTER TABLE items ADD COLUMN IF NOT EXISTS rarity TEXT;
ALTER TABLE items ADD COLUMN IF NOT EXISTS stats JSONB;
ALTER TABLE items ADD COLUMN IF NOT EXISTS base_value INT;
ALTER TABLE items ADD COLUMN IF NOT EXISTS sell_price INT;
ALTER TABLE items ADD COLUMN IF NOT EXISTS is_active BOOLEAN;

-- Default for is_active if column exists but nulls are present
UPDATE items
SET is_active = TRUE
WHERE is_active IS NULL;

-- -----------------------------
-- Ensure unique code
-- (works even if duplicates don't exist; if duplicates exist -> fix them first)
-- -----------------------------
CREATE UNIQUE INDEX IF NOT EXISTS ux_items_code ON items(code);

-- -----------------------------
-- Upsert 6 tavern food/drinks
-- -----------------------------
INSERT INTO items (code, name, emoji, category, rarity, stats, base_value, sell_price, is_active)
VALUES
  -- HP restore
  ('food_borshch',        'Борщ зі сметаною',     '🥣', 'food', 'common',   '{"hp": 20}'::jsonb,  8,  2, TRUE),
  ('food_varenyky',       'Вареники зі шкварками','🥟', 'food', 'uncommon', '{"hp": 45}'::jsonb, 18,  5, TRUE),
  ('food_salo',           'Сало з часником',      '🥓', 'food', 'rare',     '{"hp": 80}'::jsonb, 35, 10, TRUE),

  -- MP restore
  ('drink_kvas',          'Квас',                 '🍞', 'food', 'common',   '{"mp": 15}'::jsonb,  6,  2, TRUE),
  ('drink_kompot',        'Компот',               '🍎', 'food', 'uncommon', '{"mp": 35}'::jsonb, 14,  4, TRUE),
  ('drink_birch_juice',   'Березовий сік',        '🌿', 'food', 'rare',     '{"mp": 65}'::jsonb, 30,  9, TRUE)
ON CONFLICT (code) DO UPDATE
SET
  name       = EXCLUDED.name,
  emoji      = EXCLUDED.emoji,
  category   = EXCLUDED.category,
  rarity     = EXCLUDED.rarity,
  stats      = EXCLUDED.stats,
  base_value = EXCLUDED.base_value,
  sell_price = EXCLUDED.sell_price,
  is_active  = EXCLUDED.is_active;

COMMIT;