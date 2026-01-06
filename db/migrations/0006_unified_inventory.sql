-- 0006_unified_inventory.sql

-- 1. Створюємо новий єдиний інвентар
CREATE TABLE IF NOT EXISTS player_inventory (
  id          SERIAL PRIMARY KEY,
  tg_id       BIGINT NOT NULL REFERENCES players(tg_id) ON DELETE CASCADE,
  item_id     INTEGER NOT NULL REFERENCES items(id) ON DELETE RESTRICT,
  qty         INTEGER NOT NULL DEFAULT 1,
  is_equipped BOOLEAN NOT NULL DEFAULT FALSE,
  slot        TEXT NULL,              -- напр. 'weapon', 'armor', 'ring1' і т.д.
  created_at  TIMESTAMP NOT NULL DEFAULT now(),
  updated_at  TIMESTAMP NOT NULL DEFAULT now()
);

-- Індекс по гравцю
CREATE INDEX IF NOT EXISTS player_inventory_tg_id_idx
  ON player_inventory(tg_id);

-- Індекс по (tg_id, is_equipped) щоб швидко тягнути екіп
CREATE INDEX IF NOT EXISTS player_inventory_equipped_idx
  ON player_inventory(tg_id, is_equipped);

-- 2. Старі таблиці — геть (робиться ОДИН раз, без повернення)
DROP TABLE IF EXISTS player_items;
DROP TABLE IF EXISTS player_item_equipment;
DROP TABLE IF EXISTS player_equipment;
DROP TABLE IF EXISTS player_equipments;