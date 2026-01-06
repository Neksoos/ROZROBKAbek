from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Dict, Any

import asyncpg

# ──────────────────────────────────────────────
# GLOBALS
# ──────────────────────────────────────────────

POOL: Optional[asyncpg.Pool] = None
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# ──────────────────────────────────────────────
# GET POOL
# ──────────────────────────────────────────────

async def get_pool() -> asyncpg.Pool:
    global POOL
    if POOL is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL is not set")
        POOL = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=1,
            max_size=5,
        )
    return POOL


# ──────────────────────────────────────────────
# MINIMAL BASE SCHEMA (players table)
# ──────────────────────────────────────────────

async def ensure_min_schema():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS players (
              tg_id       BIGINT PRIMARY KEY,
              name        TEXT UNIQUE NOT NULL,
              gender      TEXT,
              race_key    TEXT,
              class_key   TEXT,
              level       INT DEFAULT 1,
              xp          INT DEFAULT 0,
              chervontsi  INT DEFAULT 0,
              kleynody    INT DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_players_name_lower
              ON players ((lower(name)));
            """
        )


# ──────────────────────────────────────────────
# AUTO-MIGRATIONS (db/migrations)
# ──────────────────────────────────────────────

async def run_migrations() -> None:
    """
    Виконує всі SQL-файли з папки db/migrations у лексичному порядку.
    Викликається при старті бекенду.
    """
    if not DATABASE_URL:
        print("run_migrations: DATABASE_URL not set")
        return

    # db.py лежить у корені (/app/db.py), а SQL у /app/db/migrations
    base_dir = Path(__file__).resolve().parent    # /app
    migrations_path = base_dir / "db" / "migrations"

    if not migrations_path.is_dir():
        print("run_migrations: folder not found:", migrations_path)
        return

    files = sorted(p for p in migrations_path.glob("*.sql"))
    if not files:
        print(f"run_migrations: no *.sql files in {migrations_path}")
        return

    pool = await get_pool()
    async with pool.acquire() as conn:
        for path in files:
            sql = path.read_text(encoding="utf-8").strip()
            if not sql:
                print(f"[MIGRATION] {path.name} — empty, skipped")
                continue

            print(f"[MIGRATION] Applying {path.name} ...")
            try:
                await conn.execute(sql)
            except Exception as e:
                print(f"[MIGRATION] ERROR in {path.name}: {e}")
                # не ховаємо помилку, щоб контейнер упав, а ти це побачив
                raise

    print("[MIGRATION] All migrations applied successfully.")


# ──────────────────────────────────────────────
# FETCH PLAYER
# ──────────────────────────────────────────────

async def fetch_player_by_tg(tg_id: int) -> Dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM players WHERE tg_id=$1", tg_id)
        return dict(row) if row else None