-- db/migrations/057_fix_inventory_stacking.sql
-- FIX: мерджимо дублікати ТІЛЬКИ для стеків (slot IS NULL) і робимо правильний unique index

-- 1) merge duplicates only for stacks
WITH dups AS (
  SELECT
    tg_id,
    item_id,
    MIN(id) AS keep_id,
    ARRAY_REMOVE(ARRAY_AGG(id ORDER BY id), MIN(id)) AS drop_ids,
    SUM(COALESCE(qty,1)) AS total_qty
  FROM player_inventory
  WHERE COALESCE(is_equipped,FALSE) = FALSE
    AND slot IS NULL
  GROUP BY tg_id, item_id
  HAVING COUNT(*) > 1
)
UPDATE player_inventory pi
SET qty = d.total_qty,
    updated_at = NOW()
FROM dups d
WHERE pi.id = d.keep_id;

WITH dups AS (
  SELECT
    tg_id,
    item_id,
    MIN(id) AS keep_id,
    ARRAY_REMOVE(ARRAY_AGG(id ORDER BY id), MIN(id)) AS drop_ids
  FROM player_inventory
  WHERE COALESCE(is_equipped,FALSE) = FALSE
    AND slot IS NULL
  GROUP BY tg_id, item_id
  HAVING COUNT(*) > 1
)
DELETE FROM player_inventory
WHERE id = ANY(SELECT unnest(drop_ids) FROM dups);

-- 2) drop wrong index + create correct partial unique for stacks
DROP INDEX IF EXISTS ux_player_inventory_stack;

CREATE UNIQUE INDEX IF NOT EXISTS uq_player_inventory_stack
ON player_inventory (tg_id, item_id)
WHERE slot IS NULL AND is_equipped = FALSE;
