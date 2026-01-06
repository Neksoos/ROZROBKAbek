-- 033_gathering_tasks.sql
-- Таблиця для "походів" на збір ресурсів

CREATE TABLE IF NOT EXISTS gathering_tasks (
    id           BIGSERIAL PRIMARY KEY,
    tg_id        BIGINT      NOT NULL REFERENCES players(tg_id) ON DELETE CASCADE,
    area_key     TEXT        NOT NULL,
    source_type  TEXT        NOT NULL, -- 'herb' / 'ore' / 'stone' і т.п.
    started_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finishes_at  TIMESTAMPTZ NOT NULL,
    resolved     BOOLEAN     NOT NULL DEFAULT FALSE,
    result_json  JSONB,                 -- тут можна зберігати лут / лог бою
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Щоб не було купи незакритих походів в одного гравця:
CREATE INDEX IF NOT EXISTS idx_gathering_tasks_active
    ON gathering_tasks (tg_id, resolved, finishes_at);

-- Тригер на оновлення updated_at (опціонально, якщо любиш автоматом)
CREATE OR REPLACE FUNCTION touch_gathering_tasks_updated_at()
RETURNS trigger AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_touch_gathering_tasks_updated_at ON gathering_tasks;

CREATE TRIGGER trg_touch_gathering_tasks_updated_at
BEFORE UPDATE ON gathering_tasks
FOR EACH ROW
EXECUTE FUNCTION touch_gathering_tasks_updated_at();