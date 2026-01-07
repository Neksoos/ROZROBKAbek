-- 072_blacksmith_tables.sql
-- Таблиці та DB-функції для ковальства (smelt/forge) з таймером та чергою.
--
-- Важливо: міграції виконуються при кожному старті, тому файл має бути ідемпотентним.

BEGIN;

-- =========================================================
-- 0) Legacy-guard: якщо в БД є стара схема blacksmith_recipes
--    (із slot/forge_hits/output_item_code), переносимо в *_old один раз.
-- =========================================================

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'blacksmith_recipes'
  )
  AND NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'blacksmith_recipes'
      AND column_name = 'output_kind'
  )
  AND NOT EXISTS (
    SELECT 1
    FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'blacksmith_recipes_old'
  )
  THEN
    ALTER TABLE blacksmith_recipes RENAME TO blacksmith_recipes_old;
  END IF;

  IF EXISTS (
    SELECT 1
    FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'blacksmith_recipe_ingredients'
  )
  AND NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'blacksmith_recipe_ingredients'
      AND column_name = 'input_kind'
  )
  AND NOT EXISTS (
    SELECT 1
    FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'blacksmith_recipe_ingredients_old'
  )
  THEN
    ALTER TABLE blacksmith_recipe_ingredients RENAME TO blacksmith_recipe_ingredients_old;
  END IF;
END $$;


-- =========================================================
-- 1) Blacksmith: recipes + ingredients (новий формат)
-- =========================================================

CREATE TABLE IF NOT EXISTS blacksmith_recipes (
  code            text PRIMARY KEY,
  name            text NOT NULL,
  prof_key        text NOT NULL DEFAULT 'blacksmith',
  level_req       int  NOT NULL DEFAULT 1,
  craft_time_sec  int  NOT NULL DEFAULT 60 CHECK (craft_time_sec > 0),

  output_kind     text NOT NULL CHECK (output_kind IN ('material','item')),
  output_code     text NOT NULL,
  output_amount   int  NOT NULL DEFAULT 1 CHECK (output_amount > 0),

  type            text NOT NULL CHECK (type IN ('smelt','forge')),

  notes           text,
  json_data       jsonb NOT NULL DEFAULT '{}'::jsonb,

  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_blacksmith_recipes_prof_type_level
  ON blacksmith_recipes(prof_key, type, level_req);


CREATE TABLE IF NOT EXISTS blacksmith_recipe_ingredients (
  id          bigserial PRIMARY KEY,
  recipe_code text NOT NULL REFERENCES blacksmith_recipes(code) ON DELETE CASCADE,
  input_kind  text NOT NULL CHECK (input_kind IN ('item','material')),
  input_code  text NOT NULL,
  qty         int  NOT NULL CHECK (qty > 0),
  role        text NOT NULL DEFAULT 'main',
  UNIQUE(recipe_code, input_kind, input_code, role)
);

CREATE INDEX IF NOT EXISTS idx_blacksmith_ing_recipe
  ON blacksmith_recipe_ingredients(recipe_code);


-- =========================================================
-- 1.1) Optional: перенос legacy-даних, якщо існують *_old
-- =========================================================

DO $$
BEGIN
  IF to_regclass('public.blacksmith_recipes_old') IS NOT NULL THEN
    EXECUTE $$
      INSERT INTO blacksmith_recipes (
        code, name, prof_key, level_req, craft_time_sec,
        output_kind, output_code, output_amount,
        type, notes, json_data
      )
      SELECT
        r.code,
        r.name,
        'blacksmith' AS prof_key,
        COALESCE(r.level_req, 1) AS level_req,
        GREATEST(60, COALESCE(r.forge_hits, 60) * 3) AS craft_time_sec,
        'item' AS output_kind,
        COALESCE(r.output_item_code, r.code) AS output_code,
        COALESCE(r.output_amount, 1) AS output_amount,
        'forge' AS type,
        NULL AS notes,
        '{}'::jsonb AS json_data
      FROM blacksmith_recipes_old r
      ON CONFLICT (code) DO NOTHING;
    $$;
  END IF;

  IF to_regclass('public.blacksmith_recipe_ingredients_old') IS NOT NULL THEN
    EXECUTE $$
      INSERT INTO blacksmith_recipe_ingredients (recipe_code, input_kind, input_code, qty, role)
      SELECT
        i.recipe_code,
        'material' AS input_kind,
        i.material_code AS input_code,
        COALESCE(i.qty,1) AS qty,
        COALESCE(i.role,'main') AS role
      FROM blacksmith_recipe_ingredients_old i
      ON CONFLICT DO NOTHING;
    $$;
  END IF;
END $$;


-- =========================================================
-- 2) Черга крафту гравця
-- =========================================================

CREATE TABLE IF NOT EXISTS player_blacksmith_queue (
  id            bigserial PRIMARY KEY,
  tg_id         bigint NOT NULL,
  recipe_code   text NOT NULL REFERENCES blacksmith_recipes(code) ON DELETE RESTRICT,

  status        text NOT NULL DEFAULT 'crafting',
  started_at    timestamptz NOT NULL DEFAULT now(),
  finish_at     timestamptz NOT NULL,

  output_kind   text NOT NULL CHECK (output_kind IN ('material','item')),
  output_code   text NOT NULL,
  output_amount int  NOT NULL DEFAULT 1 CHECK (output_amount > 0),

  meta          jsonb NOT NULL DEFAULT '{}'::jsonb,

  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_player_blacksmith_queue_tg
  ON player_blacksmith_queue(tg_id);

CREATE INDEX IF NOT EXISTS idx_player_blacksmith_queue_tg_status
  ON player_blacksmith_queue(tg_id, status);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.table_constraints
    WHERE table_schema='public'
      AND table_name = 'player_blacksmith_queue'
      AND constraint_name = 'player_blacksmith_queue_status_chk'
  ) THEN
    BEGIN
      ALTER TABLE player_blacksmith_queue
        ADD CONSTRAINT player_blacksmith_queue_status_chk
        CHECK (status IN ('crafting','done','collected','cancelled'));
    EXCEPTION WHEN OTHERS THEN
      NULL;
    END;
  END IF;
END $$;


-- =========================================================
-- 3) Функція refresh
-- =========================================================

CREATE OR REPLACE FUNCTION blacksmith_refresh_queue(p_tg_id bigint)
RETURNS int
LANGUAGE plpgsql
AS $$
DECLARE
  v_cnt int;
BEGIN
  UPDATE player_blacksmith_queue
  SET status = 'done',
      updated_at = now()
  WHERE tg_id = p_tg_id
    AND status = 'crafting'
    AND finish_at <= now();

  GET DIAGNOSTICS v_cnt = ROW_COUNT;
  RETURN v_cnt;
END;
$$;


-- =========================================================
-- 4) View для UI
-- =========================================================

CREATE OR REPLACE VIEW v_player_blacksmith_active AS
SELECT
  q.id,
  q.tg_id,
  q.recipe_code,
  r.name AS recipe_name,
  r.type AS craft_type,
  q.status,
  q.started_at,
  q.finish_at,
  GREATEST(0, EXTRACT(EPOCH FROM (q.finish_at - now()))::int) AS seconds_left,
  q.output_kind,
  q.output_code,
  q.output_amount,
  q.meta
FROM player_blacksmith_queue q
JOIN blacksmith_recipes r ON r.code = q.recipe_code
WHERE q.status IN ('crafting','done');

COMMIT;
