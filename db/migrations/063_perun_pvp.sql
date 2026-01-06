-- 061_perun_pvp.sql
-- Perun PvP: queue + duels + elo (idempotent), with legacy perun_duels migration.

BEGIN;

-- =========================
-- 1) perun_queue
-- =========================
CREATE TABLE IF NOT EXISTS perun_queue (
  tg_id     BIGINT PRIMARY KEY,
  joined_at TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS perun_queue_joined_idx ON perun_queue(joined_at);


-- =========================
-- 2) perun_duels (new schema)
-- =========================
CREATE TABLE IF NOT EXISTS perun_duels (
  id         BIGSERIAL PRIMARY KEY,
  p1         BIGINT NOT NULL,
  p2         BIGINT NOT NULL,
  status     TEXT   NOT NULL DEFAULT 'active',
  created_at TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS perun_duels_status_idx  ON perun_duels(status);
CREATE INDEX IF NOT EXISTS perun_duels_created_idx ON perun_duels(created_at);
CREATE INDEX IF NOT EXISTS perun_duels_p1_idx      ON perun_duels(p1);
CREATE INDEX IF NOT EXISTS perun_duels_p2_idx      ON perun_duels(p2);


-- =========================
-- 2.1) Legacy fix: challenger/target -> p1/p2 (if old schema exists)
-- =========================
DO $$
BEGIN
  -- If table exists and has old columns challenger/target, but missing p1/p2 -> add and backfill.
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_name = 'perun_duels'
  ) THEN

    -- add p1 if missing
    IF NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_name='perun_duels' AND column_name='p1'
    ) THEN
      ALTER TABLE perun_duels ADD COLUMN p1 BIGINT;
    END IF;

    -- add p2 if missing
    IF NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_name='perun_duels' AND column_name='p2'
    ) THEN
      ALTER TABLE perun_duels ADD COLUMN p2 BIGINT;
    END IF;

    -- backfill from challenger -> p1 (if challenger exists)
    IF EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_name='perun_duels' AND column_name='challenger'
    ) THEN
      EXECUTE 'UPDATE perun_duels SET p1 = COALESCE(p1, challenger) WHERE p1 IS NULL';
    END IF;

    -- backfill from target -> p2 (if target exists)
    IF EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_name='perun_duels' AND column_name='target'
    ) THEN
      EXECUTE 'UPDATE perun_duels SET p2 = COALESCE(p2, target) WHERE p2 IS NULL';
    END IF;

    -- Ensure NOT NULL after backfill (only if no NULLs remain)
    IF NOT EXISTS (SELECT 1 FROM perun_duels WHERE p1 IS NULL) THEN
      ALTER TABLE perun_duels ALTER COLUMN p1 SET NOT NULL;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM perun_duels WHERE p2 IS NULL) THEN
      ALTER TABLE perun_duels ALTER COLUMN p2 SET NOT NULL;
    END IF;

  END IF;
END $$;


-- =========================
-- 3) perun_elo (rating table)
-- =========================
CREATE TABLE IF NOT EXISTS perun_elo (
  tg_id      BIGINT PRIMARY KEY,
  elo_day    INT NOT NULL DEFAULT 1000,
  elo_week   INT NOT NULL DEFAULT 1000,
  elo_month  INT NOT NULL DEFAULT 1000,
  elo_all    INT NOT NULL DEFAULT 1000,
  wins       INT NOT NULL DEFAULT 0,
  losses     INT NOT NULL DEFAULT 0,
  updated_at TIMESTAMP DEFAULT now()
);

CREATE INDEX IF NOT EXISTS perun_elo_updated_idx   ON perun_elo(updated_at);
CREATE INDEX IF NOT EXISTS perun_elo_elo_day_idx   ON perun_elo(elo_day DESC);
CREATE INDEX IF NOT EXISTS perun_elo_elo_week_idx  ON perun_elo(elo_week DESC);
CREATE INDEX IF NOT EXISTS perun_elo_elo_month_idx ON perun_elo(elo_month DESC);
CREATE INDEX IF NOT EXISTS perun_elo_elo_all_idx   ON perun_elo(elo_all DESC);

COMMIT;