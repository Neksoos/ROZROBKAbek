CREATE TABLE IF NOT EXISTS fort_applications (
    id SERIAL PRIMARY KEY,
    tg_id BIGINT NOT NULL,
    fort_id INT NOT NULL,
    created_at TIMESTAMP DEFAULT now(),

    -- tg_id + fort_id унікальна заявка
    CONSTRAINT uq_fort_app UNIQUE (tg_id, fort_id)
);