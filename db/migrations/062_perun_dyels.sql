DROP TABLE perun_duels;

CREATE TABLE perun_duels (
    id BIGSERIAL PRIMARY KEY,
    p1 BIGINT NOT NULL,
    p2 BIGINT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMP DEFAULT now()
);