DROP TABLE IF EXISTS referrals;

CREATE TABLE referrals (
    id           BIGSERIAL PRIMARY KEY,
    invitee_id   BIGINT UNIQUE NOT NULL,
    inviter_id   BIGINT NOT NULL,
    reward_paid  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ref_inviter ON referrals(inviter_id);
