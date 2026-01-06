CREATE UNIQUE INDEX IF NOT EXISTS ux_player_inventory_stack
ON player_inventory (tg_id, item_id)
WHERE slot IS NULL AND is_equipped = FALSE;