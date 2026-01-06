-- 00016_add_is_banned_to_players.sql
-- Додаємо прапорець бану гравця

ALTER TABLE players
    ADD COLUMN IF NOT EXISTS is_banned BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_players_is_banned
    ON players (is_banned);