CREATE TABLE IF NOT EXISTS item_equipment_stats (
    id              SERIAL PRIMARY KEY,
    item_code       TEXT NOT NULL REFERENCES items(code) ON DELETE CASCADE,
    slot            TEXT NOT NULL,          -- weapon / armor / shield / accessory
    level_req       INT  DEFAULT 1,
    atk_bonus       INT  DEFAULT 0,
    def_bonus       INT  DEFAULT 0,
    hp_bonus        INT  DEFAULT 0,
    mp_bonus        INT  DEFAULT 0,
    crit_chance_pct REAL DEFAULT 0,
    dodge_chance_pct REAL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS item_equipment_stats_code_uq
    ON item_equipment_stats(item_code);