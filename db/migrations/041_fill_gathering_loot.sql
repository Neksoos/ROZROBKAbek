-- 041_fill_gathering_loot.sql
-- Початкове заповнення таблиці gathering_loot
-- Беремо всі матеріали з craft_materials і вішаємо їх на першу локацію "netrytsia".
-- Потім ти зможеш руками розкидати по інших локаціях, якщо треба.

INSERT INTO gathering_loot (
    area_key,
    material_id,
    source_type,
    drop_chance,
    min_qty,
    max_qty,
    level_min,
    level_max
)
SELECT
    'netrytsia'                         AS area_key,         -- 🔥 тепер НЕ NULL
    cm.id                               AS material_id,
    cm.source_type                      AS source_type,
    COALESCE(cm.drop_chance, 25)        AS drop_chance,
    1                                   AS min_qty,
    2                                   AS max_qty,
    1                                   AS level_min,
    99                                  AS level_max
FROM craft_materials cm
-- тільки ті, у кого є source_type (herb/ore/stone/…)
WHERE cm.source_type IS NOT NULL
ON CONFLICT (area_key, material_id) DO UPDATE
SET
    drop_chance = EXCLUDED.drop_chance,
    min_qty     = EXCLUDED.min_qty,
    max_qty     = EXCLUDED.max_qty,
    level_min   = EXCLUDED.level_min,
    level_max   = EXCLUDED.level_max;