-- Зберігання ресурсів (трави, руда, каміння тощо)
-- НЕ екіп, НЕ сміття, НЕ інвентар

CREATE TABLE IF NOT EXISTS player_materials (
    tg_id        BIGINT NOT NULL,
    material_id  INT    NOT NULL,
    qty          INT    NOT NULL DEFAULT 0,

    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT player_materials_pk
        PRIMARY KEY (tg_id, material_id),

    CONSTRAINT player_materials_material_fk
        FOREIGN KEY (material_id)
        REFERENCES craft_materials(id)
        ON DELETE CASCADE
);

-- Індекс для швидкого отримання ресурсів гравця
CREATE INDEX IF NOT EXISTS idx_player_materials_tg
    ON player_materials (tg_id);