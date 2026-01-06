BEGIN;

-- 1) items: stackable column exists + sane defaults
ALTER TABLE items
ADD COLUMN IF NOT EXISTS stackable BOOLEAN DEFAULT FALSE;

UPDATE items SET stackable = FALSE WHERE stackable IS NULL;

-- 2) Mark resource-like categories as stackable
UPDATE items
SET stackable = TRUE
WHERE (stackable = FALSE OR stackable IS NULL)
  AND (
    category ILIKE 'herb%'   OR
    category ILIKE 'ore%'    OR
    category ILIKE 'stone%'  OR
    category ILIKE 'mat%'    OR
    category ILIKE 'food%'   OR
    category ILIKE 'potion%' OR
    category ILIKE 'consum%' OR
    category ILIKE 'trash%'
  );

-- 3) player_inventory: ensure amount column exists
ALTER TABLE player_inventory
ADD COLUMN IF NOT EXISTS amount INTEGER;

-- backfill amount if NULL
UPDATE player_inventory
SET amount = 1
WHERE amount IS NULL;

-- if you still have legacy qty column and want to migrate it into amount:
DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_name='player_inventory' AND column_name='qty'
  ) THEN
    -- if amount looks like default 1 but qty has meaningful values, copy qty -> amount
    UPDATE player_inventory
    SET amount = GREATEST(1, qty)
    WHERE (amount IS NULL OR amount = 1)
      AND qty IS NOT NULL
      AND qty > 1;
  END IF;
END $$;

-- 4) Merge duplicates (stacking rows) for non-equipped items:
--    Keep the smallest id row, sum amounts into it, delete the rest.
WITH grouped AS (
  SELECT
    tg_id,
    item_id,
    MIN(id) AS keep_id,
    SUM(GREATEST(1, COALESCE(amount, 1))) AS total_amount
  FROM player_inventory
  WHERE is_equipped = FALSE
  GROUP BY tg_id, item_id
  HAVING COUNT(*) > 1
),
upd AS (
  UPDATE player_inventory pi
  SET amount = g.total_amount,
      updated_at = NOW()
  FROM grouped g
  WHERE pi.id = g.keep_id
  RETURNING pi.id
)
DELETE FROM player_inventory pi
USING grouped g
WHERE pi.is_equipped = FALSE
  AND pi.tg_id = g.tg_id
  AND pi.item_id = g.item_id
  AND pi.id <> g.keep_id;

-- 5) Ensure unique index for stack (non-equipped)
-- Drop old index if exists (name might be different - we handle common one)
DROP INDEX IF EXISTS ux_player_inventory_stack;

-- Create the correct partial unique index
CREATE UNIQUE INDEX IF NOT EXISTS ux_player_inventory_stack
ON player_inventory (tg_id, item_id)
WHERE is_equipped = FALSE;

COMMIT;