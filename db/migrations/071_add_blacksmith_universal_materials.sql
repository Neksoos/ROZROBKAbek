--- a/db/migrations/071_add_blacksmith_universal_materials.sql
+++ b/db/migrations/071_add_blacksmith_universal_materials.sql
@@ -1,42 +1,10 @@
--- 2025_12_25_add_blacksmith_universal_materials.sql
-BEGIN;
-
--- ─────────────────────────────────────────────
--- 0) Ensure blacksmith tables exist (safe)
--- ─────────────────────────────────────────────
-CREATE TABLE IF NOT EXISTS blacksmith_recipes (
-  code                   text PRIMARY KEY,
-  name                   text NOT NULL,
-  slot                   text NOT NULL,
-  level_req              int  NOT NULL DEFAULT 1,
-
-  forge_hits             int  NOT NULL DEFAULT 60,
-  base_progress_per_hit  double precision NOT NULL DEFAULT 0.0166667,
-  heat_sensitivity       double precision NOT NULL DEFAULT 0.65,
-  rhythm_min_ms          int  NOT NULL DEFAULT 120,
-  rhythm_max_ms          int  NOT NULL DEFAULT 220,
-
-  output_item_code       text NOT NULL,
-  output_amount          int  NOT NULL DEFAULT 1,
-
-  created_at             timestamptz NOT NULL DEFAULT now(),
-  updated_at             timestamptz NOT NULL DEFAULT now()
-);
+-- 071_add_blacksmith_universal_materials.sql
+-- Додає універсальні матеріали для ковальства у craft_materials.
+-- ВАЖЛИВО: цей файл має бути незалежний від наявності таблиць blacksmith_*.
 
-CREATE TABLE IF NOT EXISTS blacksmith_recipe_ingredients (
-  recipe_code   text NOT NULL REFERENCES blacksmith_recipes(code) ON DELETE CASCADE,
-  material_code text NOT NULL,
-  qty           int  NOT NULL DEFAULT 1,
-  role          text NOT NULL DEFAULT 'metal',
-  PRIMARY KEY (recipe_code, material_code, role)
-);
-
-CREATE INDEX IF NOT EXISTS idx_bsmith_recipes_slot ON blacksmith_recipes(slot);
+BEGIN;
 
--- ─────────────────────────────────────────────
--- 1) craft_materials: add universal smith items
---    (schema: code, name, descr, profession, source_type, rarity)
--- ─────────────────────────────────────────────
+-- schema: craft_materials(code, name, descr, profession, source_type, rarity, created_at, updated_at)
 INSERT INTO craft_materials (code, name, descr, profession, source_type, rarity)
 VALUES
 (
@@ -55,32 +23,13 @@
   'змішане',
   'Добротний'
 )
-ON CONFLICT (code) DO NOTHING;
-
--- ─────────────────────────────────────────────
--- 2) Add these materials into EVERY blacksmith recipe
---    reagent qty  = GREATEST(1, CEIL(level_req/2))
---    quench qty   = GREATEST(1, level_req - 1)
--- ─────────────────────────────────────────────
-
--- reagent
-INSERT INTO blacksmith_recipe_ingredients (recipe_code, material_code, qty, role)
-SELECT
-  r.code AS recipe_code,
-  'smith_reagent' AS material_code,
-  GREATEST(1, CEIL(r.level_req / 2.0))::int AS qty,
-  'process' AS role
-FROM blacksmith_recipes r
-ON CONFLICT (recipe_code, material_code, role) DO NOTHING;
-
--- quench mix
-INSERT INTO blacksmith_recipe_ingredients (recipe_code, material_code, qty, role)
-SELECT
-  r.code AS recipe_code,
-  'smith_quench_mix' AS material_code,
-  GREATEST(1, (r.level_req - 1))::int AS qty,
-  'quench' AS role
-FROM blacksmith_recipes r
-ON CONFLICT (recipe_code, material_code, role) DO NOTHING;
+ON CONFLICT (code) DO UPDATE
+SET
+  name        = EXCLUDED.name,
+  descr       = EXCLUDED.descr,
+  profession  = EXCLUDED.profession,
+  source_type = EXCLUDED.source_type,
+  rarity      = EXCLUDED.rarity,
+  updated_at  = NOW();
 
 COMMIT;
