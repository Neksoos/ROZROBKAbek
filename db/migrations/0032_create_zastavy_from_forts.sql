BEGIN;

-- 1. Створюємо таблицю застав
CREATE TABLE zastavy (
    id               INTEGER PRIMARY KEY,
    name             TEXT NOT NULL,
    -- лідер у вигляді player.id (НЕ tg_id)
    leader_player_id BIGINT REFERENCES players(id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2. Копіюємо дані з forts
--    forts.created_by = players.tg_id  → шукаємо відповідного player.id
INSERT INTO zastavy (id, name, leader_player_id, created_at)
SELECT
    f.id,
    f.name,
    p.id AS leader_player_id,
    f.created_at
FROM forts AS f
LEFT JOIN players AS p
    ON p.tg_id = f.created_by;

-- 3. Послідовність для нових записів
CREATE SEQUENCE IF NOT EXISTS zastavy_id_seq;

-- ставимо seq на поточний MAX(id), щоб наступний INSERT отримав наступне число
SELECT setval(
    'zastavy_id_seq',
    COALESCE((SELECT MAX(id) FROM zastavy), 0),
    TRUE
);

ALTER TABLE zastavy
    ALTER COLUMN id SET DEFAULT nextval('zastavy_id_seq');

COMMIT;