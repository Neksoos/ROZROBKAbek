

ALTER TABLE players
    ADD COLUMN IF NOT EXISTS regen_at timestamptz DEFAULT now();