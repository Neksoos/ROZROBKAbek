-- 1. Перенести реальні значення
UPDATE player_inventory
SET qty = amount
WHERE amount IS NOT NULL;

-- 2. Контроль
SELECT id, qty, amount FROM player_inventory;

-- 3. (опційно, але правильно)
ALTER TABLE player_inventory DROP COLUMN amount;