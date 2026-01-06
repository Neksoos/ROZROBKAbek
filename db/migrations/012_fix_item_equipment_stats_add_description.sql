ALTER TABLE item_equipment_stats
    ADD COLUMN IF NOT EXISTS description TEXT DEFAULT '';