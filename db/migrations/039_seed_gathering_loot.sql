-- 032_seed_gathering_loot.sql
-- Сидимо дроп для збору ресурсів із craft_materials у таблицю gathering_loot

-- ░░ БАЗА: Нетриця (netrytsia), зілляр, трави (herb)
INSERT INTO gathering_loot (
    area_key,
    material_id,
    source_type,
    drop_chance,
    min_qty,
    max_qty,
    level_min
)
SELECT
    'netrytsia'              AS area_key,
    cm.id                    AS material_id,
    cm.source_type           AS source_type,
    CASE cm.rarity
        WHEN 'Звичайний'   THEN 65
        WHEN 'Добротний'   THEN 45
        WHEN 'Рідкісний'   THEN 25
        WHEN 'Вибраний'    THEN 12
        WHEN 'Обереговий'  THEN 6
        WHEN 'Божественний' THEN 2
        ELSE 30
    END                      AS drop_chance,
    1                        AS min_qty,
    2                        AS max_qty,
    1                        AS level_min
FROM craft_materials cm
WHERE cm.profession = 'зілляр'
  AND cm.source_type = 'herb'
  AND NOT EXISTS (
      SELECT 1
      FROM gathering_loot gl
      WHERE gl.area_key    = 'netrytsia'
        AND gl.material_id = cm.id
        AND gl.source_type = cm.source_type
  );

-- ░░ Та самі трави у Передмісті, але шанси трохи нижчі, дроп більший, рівень з 3
INSERT INTO gathering_loot (
    area_key,
    material_id,
    source_type,
    drop_chance,
    min_qty,
    max_qty,
    level_min
)
SELECT
    'peredmistia'            AS area_key,
    cm.id                    AS material_id,
    cm.source_type           AS source_type,
    CASE cm.rarity
        WHEN 'Звичайний'   THEN 55
        WHEN 'Добротний'   THEN 38
        WHEN 'Рідкісний'   THEN 20
        WHEN 'Вибраний'    THEN 10
        WHEN 'Обереговий'  THEN 5
        WHEN 'Божественний' THEN 2
        ELSE 25
    END                      AS drop_chance,
    1                        AS min_qty,
    3                        AS max_qty,
    3                        AS level_min
FROM craft_materials cm
WHERE cm.profession = 'зілляр'
  AND cm.source_type = 'herb'
  AND NOT EXISTS (
      SELECT 1
      FROM gathering_loot gl
      WHERE gl.area_key    = 'peredmistia'
        AND gl.material_id = cm.id
        AND gl.source_type = cm.source_type
  );