DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fort_treasury_fort_fk'
    ) THEN
        ALTER TABLE fort_treasury
        ADD CONSTRAINT fort_treasury_fort_fk
        FOREIGN KEY (fort_id)
        REFERENCES forts(id);
    END IF;
END $$;