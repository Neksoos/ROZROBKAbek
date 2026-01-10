-- migrations/xxx_achievements.sql
-- Achievements / Metrics core tables

CREATE TABLE IF NOT EXISTS player_metrics (
  tg_id       bigint NOT NULL,
  key         text   NOT NULL,
  val         bigint NOT NULL DEFAULT 0,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tg_id, key)
);

CREATE TABLE IF NOT EXISTS player_events (
  tg_id       bigint NOT NULL,
  event_key   text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tg_id, event_key)
);

-- Індекси для статистики, адмінки та швидких перевірок ачівок
CREATE INDEX IF NOT EXISTS idx_player_metrics_key
  ON player_metrics(key);

CREATE INDEX IF NOT EXISTS idx_player_events_event_key
  ON player_events(event_key);