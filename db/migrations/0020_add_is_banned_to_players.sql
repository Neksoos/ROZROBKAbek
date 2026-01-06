-- 00016_add_is_banned_to_players.sql
ALTER TABLE players
    ADD COLUMN IF NOT EXISTS is_banned boolean NOT NULL DEFAULT FALSE;