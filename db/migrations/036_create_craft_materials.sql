CREATE TABLE IF NOT EXISTS craft_materials (
    id          SERIAL PRIMARY KEY,
    code        TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    descr       TEXT NOT NULL,
    profession  TEXT NOT NULL,  -- 'травник','зілляр','коваль','ювелір'
    source_type TEXT NOT NULL,  -- 'трава','руда','камінь','змішане'
    rarity      TEXT NOT NULL,  -- Звичайний, Добротний, Рідкісний, Вибраний, Обереговий, Божественний
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_craft_materials_profession
    ON craft_materials (profession);

CREATE INDEX IF NOT EXISTS idx_craft_materials_rarity
    ON craft_materials (rarity);