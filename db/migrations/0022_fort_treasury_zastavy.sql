DO $$
BEGIN
    -- Якщо колонка fort_id існує — перейменовуємо
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='fort_treasury' AND column_name='fort_id'
    ) THEN
        ALTER TABLE fort_treasury RENAME COLUMN fort_id TO zastava_id;
    END IF;

    -- Якщо колонка gold існує — перейменовуємо
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='fort_treasury' AND column_name='gold'
    ) THEN
        ALTER TABLE fort_treasury RENAME COLUMN gold TO chervontsi;
    END IF;

    -- Якщо нема клейнодів — додаємо
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='fort_treasury' AND column_name='kleynody'
    ) THEN
        ALTER TABLE fort_treasury ADD COLUMN kleynody BIGINT NOT NULL DEFAULT 0;
    END IF;

EXCEPTION WHEN others THEN
    RAISE NOTICE 'safe skip';
END $$;