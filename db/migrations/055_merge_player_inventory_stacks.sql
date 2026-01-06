WITH grouped AS (
  SELECT
    MIN(id) AS keep_id,
    tg_id,
    item_id,
    SUM(amount) AS total_amount
  FROM player_inventory
  WHERE slot IS NULL AND is_equipped = FALSE
  GROUP BY tg_id, item_id
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
WHERE pi.slot IS NULL
  AND pi.is_equipped = FALSE
  AND pi.tg_id = g.tg_id
  AND pi.item_id = g.item_id
  AND pi.id <> g.keep_id;