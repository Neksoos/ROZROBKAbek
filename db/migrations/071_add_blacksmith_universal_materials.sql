-- 071_add_blacksmith_universal_materials.sql
-- Додає універсальні матеріали для ковальства у craft_materials.
-- Незалежний від таблиць blacksmith_*.

BEGIN;

INSERT INTO craft_materials (code, name, descr, profession, source_type, rarity)
VALUES
(
  'smith_reagent',
  'Ковальський реагент',
  'Універсальна суміш мінералів і солей, що стабілізує метал під час кування. Використовується на всіх етапах ковальського процесу.',
  'коваль',
  'змішане',
  'Добротний'
),
(
  'smith_quench_mix',
  'Гартівний склад',
  'Суміш масел, солей і мінералів для фінальної гартовки виробів. Потрібна для завершення будь-якого ковальського кування.',
  'коваль',
  'змішане',
  'Добротний'
)
ON CONFLICT (code) DO UPDATE
SET
  name        = EXCLUDED.name,
  descr       = EXCLUDED.descr,
  profession  = EXCLUDED.profession,
  source_type = EXCLUDED.source_type,
  rarity      = EXCLUDED.rarity,
  updated_at  = NOW();

COMMIT;
