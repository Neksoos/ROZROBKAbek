-- 053_fill_sell_price_from_base.sql

-- гарантія, що колонка є
ALTER TABLE items
ADD COLUMN IF NOT EXISTS sell_price INT;

-- перерахувати ціну продажу з base_value
UPDATE items
SET sell_price = GREATEST(
  1,
  FLOOR(base_value * 0.4)  -- 40% від базової ціни
)
WHERE base_value IS NOT NULL
  AND base_value > 0;