-- 039_professions_init.sql
-- Професії + професії гравця + сидер 5 базових професій

-- ─────────────────────────────────────────────
-- Довідник професій
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS professions (
    id         SERIAL PRIMARY KEY,
    code       TEXT NOT NULL UNIQUE,     -- 'herbalist', 'miner', 'stonemason', 'blacksmith', 'jeweler'
    name       TEXT NOT NULL,           -- Людська назва (укр)
    descr      TEXT NOT NULL,           -- Опис для UI
    kind       TEXT NOT NULL,           -- 'gathering' | 'craft'
    min_level  INTEGER NOT NULL DEFAULT 1,
    icon       TEXT NULL,               -- emoji або шлях до іконки
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- На випадок, якщо таблиця вже є, але немає якихось колонок
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'professions' AND column_name = 'icon'
    ) THEN
        ALTER TABLE professions ADD COLUMN icon TEXT NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'professions' AND column_name = 'min_level'
    ) THEN
        ALTER TABLE professions ADD COLUMN min_level INTEGER NOT NULL DEFAULT 1;
    END IF;
END $$;

-- ─────────────────────────────────────────────
-- Професії гравця
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS player_professions (
    id             BIGSERIAL PRIMARY KEY,
    player_id      BIGINT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    profession_id  INTEGER NOT NULL REFERENCES professions(id) ON DELETE CASCADE,
    level          INTEGER NOT NULL DEFAULT 1,
    xp             INTEGER NOT NULL DEFAULT 0,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (player_id, profession_id)
);

CREATE INDEX IF NOT EXISTS player_professions_player_idx
    ON player_professions(player_id);

-- ─────────────────────────────────────────────
-- Сидер базових професій
-- ─────────────────────────────────────────────
-- Якщо рядка з таким code ще нема — вставляємо; якщо є — оновлюємо name/descr/kind/min_level/icon

INSERT INTO professions (code, name, descr, kind, min_level, icon)
VALUES
    -- Збиральні
    (
        'herbalist',
        'Зілляр',
        'Збирає лікувальні трави, коріння та рідкісні рослини для майбутніх еліксирів.',
        'gathering',
        1,
        '🌿'
    ),
    (
        'miner',
        'Рудокоп',
        'Добуває руду, металеві жили та інші глибокі дари землі.',
        'gathering',
        3,
        '⛏'
    ),
    (
        'stonemason',
        'Каменяр',
        'Шукає коштовне каміння, мінерали та особливі породи скель.',
        'gathering',
        5,
        '🪨'
    ),

    -- Крафтові
    (
        'blacksmith',
        'Коваль',
        'Кує зброю та броню, посилює спорядження та відкриває нові рецепти екіпірування.',
        'craft',
        5,
        '⚒'
    ),
    (
        'jeweler',
        'Ювелір',
        'Створює кільця, амулети та інші прикраси з посилюючими властивостями.',
        'craft',
        7,
        '💍'
    )
ON CONFLICT (code) DO UPDATE
SET
    name      = EXCLUDED.name,
    descr     = EXCLUDED.descr,
    kind      = EXCLUDED.kind,
    min_level = EXCLUDED.min_level,
    icon      = EXCLUDED.icon,
    updated_at = NOW();