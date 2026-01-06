ALTER TABLE items
ADD COLUMN IF NOT EXISTS category TEXT;

UPDATE items
SET category = 'trash'
WHERE category IS NULL;