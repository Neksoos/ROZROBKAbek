-- 044_add_alchemist_profession.sql
-- Додає нову крафтову професію "Алхімік"

DO $$
BEGIN
    -- якщо професія з кодом 'alchemist' уже є – нічого не робимо
    IF NOT EXISTS (
        SELECT 1 FROM professions WHERE code = 'alchemist'
    ) THEN
        INSERT INTO professions (code, name, descr, kind, min_level, icon)
        VALUES (
            'alchemist',
            'Алхімік',
            'Варить відвари, настої та бойові еліксири з трав, грибів і моторошних інгредієнтів.',
            'craft',
            1,
            '⚗️'
        );
    END IF;
END;
$$;