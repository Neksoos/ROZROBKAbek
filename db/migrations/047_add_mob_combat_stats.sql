-- 040_add_mob_combat_stats.sql
-- Бойові стати мобів (фіз/магія + захист)
-- Автозаповнення на основі level / base_*

BEGIN;

-- ─────────────────────────────────────────────
-- 1. ДОДАЄМО КОЛОНКИ (якщо їх ще нема)
-- ─────────────────────────────────────────────

ALTER TABLE mobs
  ADD COLUMN IF NOT EXISTS phys_attack INT,
  ADD COLUMN IF NOT EXISTS magic_attack INT,
  ADD COLUMN IF NOT EXISTS phys_defense INT,
  ADD COLUMN IF NOT EXISTS magic_defense INT;

-- ─────────────────────────────────────────────
-- 2. АВТОЗАПОВНЕННЯ СТАТІВ
-- логіка:
--  - phys_attack: atk → base_attack → level * 3
--  - magic_attack: тільки якщо є mana (base_mp)
--  - defense масштабується від level
-- ─────────────────────────────────────────────

UPDATE mobs
SET
  -- ФІЗИЧНА АТАКА
  phys_attack = COALESCE(
      phys_attack,
      atk,
      base_attack,
      GREATEST(1, level * 3)
  ),

  -- МАГІЧНА АТАКА
  magic_attack = COALESCE(
      magic_attack,
      CASE
        WHEN COALESCE(base_mp, 0) > 0
          THEN GREATEST(1, (base_mp / 2) + level)
        ELSE 0
      END
  ),

  -- ФІЗИЧНИЙ ЗАХИСТ
  phys_defense = COALESCE(
      phys_defense,
      GREATEST(0, level * 2)
  ),

  -- МАГІЧНИЙ ЗАХИСТ
  magic_defense = COALESCE(
      magic_defense,
      GREATEST(0, level)
  );

COMMIT;