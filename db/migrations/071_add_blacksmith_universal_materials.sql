-- 2025_12_25_add_blacksmith_universal_materials.sql
BEGIN;

-- ─────────────────────────────────────────────
-- 0) Ensure blacksmith tables exist (safe)
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS blacksmith_recipes (
  code                   text PRIMARY KEY,
  name                   text NOT NULL,
  slot                   text NOT NULL,
  level_req              int  NOT NULL DEFAULT 1,

  forge_hits             int  NOT NULL DEFAULT 60,
  base_progress_per_hit  double precision NOT NULL DEFAULT 0.0166667,
  heat_sensitivity       double precision NOT NULL DEFAULT 0.65,
  rhythm_min_ms          int  NOT NULL DEFAULT 120,
  rhythm_max_ms          int  NOT NULL DEFAULT 220,

  output_item_code       text NOT NULL,
  output_amount          int  NOT NULL DEFAULT 1,

  created_at             timestamptz NOT NULL DEFAULT now(),
  updated_at             timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS blacksmith_recipe_ingredients (
  recipe_code   text NOT NULL REFERENCES blacksmith_recipes(code) ON DELETE CASCADE,
  material_code text NOT NULL,
  qty           int  NOT NULL DEFAULT 1,
  role          text NOT NULL DEFAULT 'metal',
  PRIMARY KEY (recipe_code, material_code, role)
);

CREATE INDEX IF NOT EXISTS idx_bsmith_recipes_slot ON blacksmith_recipes(slot);

-- ─────────────────────────────────────────────
-- 1) craft_materials: add universal smith items
--    (schema: code, name, descr, profession, source_type, rarity)
-- ─────────────────────────────────────────────
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
ON CONFLICT (code) DO NOTHING;

-- ─────────────────────────────────────────────
-- 2) Add these materials into EVERY blacksmith recipe
--    reagent qty  = GREATEST(1, CEIL(level_req/2))
--    quench qty   = GREATEST(1, level_req - 1)
-- ─────────────────────────────────────────────

-- reagent
INSERT INTO blacksmith_recipe_ingredients (recipe_code, material_code, qty, role)
SELECT
  r.code AS recipe_code,
  'smith_reagent' AS material_code,
  GREATEST(1, CEIL(r.level_req / 2.0))::int AS qty,
  'process' AS role
FROM blacksmith_recipes r
ON CONFLICT (recipe_code, material_code, role) DO NOTHING;

-- quench mix
INSERT INTO blacksmith_recipe_ingredients (recipe_code, material_code, qty, role)
SELECT
  r.code AS recipe_code,
  'smith_quench_mix' AS material_code,
  GREATEST(1, (r.level_req - 1))::int AS qty,
  'quench' AS role
FROM blacksmith_recipes r
ON CONFLICT (recipe_code, material_code, role) DO NOTHING;

COMMIT;