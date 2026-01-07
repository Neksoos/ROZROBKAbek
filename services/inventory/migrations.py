# services/inventory/migrations.py
from __future__ import annotations

from db import get_pool


async def ensure_items_columns() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""ALTER TABLE items ADD COLUMN IF NOT EXISTS emoji TEXT;""")
        await conn.execute("""ALTER TABLE items ADD COLUMN IF NOT EXISTS slot TEXT;""")
        await conn.execute("""ALTER TABLE items ADD COLUMN IF NOT EXISTS stats JSONB DEFAULT '{}'::jsonb;""")
        await conn.execute("""ALTER TABLE items ADD COLUMN IF NOT EXISTS description TEXT;""")
        await conn.execute("""ALTER TABLE items ADD COLUMN IF NOT EXISTS sell_price INTEGER;""")
        await conn.execute("""ALTER TABLE items ADD COLUMN IF NOT EXISTS stackable BOOLEAN DEFAULT FALSE;""")
        await conn.execute("""ALTER TABLE items ADD COLUMN IF NOT EXISTS category TEXT DEFAULT 'trash';""")

        # бойові колонки
        await conn.execute("""ALTER TABLE items ADD COLUMN IF NOT EXISTS atk INTEGER DEFAULT 0;""")
        await conn.execute("""ALTER TABLE items ADD COLUMN IF NOT EXISTS defense INTEGER DEFAULT 0;""")
        await conn.execute("""ALTER TABLE items ADD COLUMN IF NOT EXISTS hp INTEGER DEFAULT 0;""")
        await conn.execute("""ALTER TABLE items ADD COLUMN IF NOT EXISTS mp INTEGER DEFAULT 0;""")
        await conn.execute("""ALTER TABLE items ADD COLUMN IF NOT EXISTS weight INTEGER DEFAULT 0;""")

        # best-effort міграції/чистка
        await conn.execute("""UPDATE items SET sell_price = 1 WHERE sell_price IS NULL;""")
        await conn.execute(
            """
            UPDATE items
            SET description = descr
            WHERE (description IS NULL OR description = '')
              AND descr IS NOT NULL;
            """
        )
        await conn.execute("""UPDATE items SET stackable = FALSE WHERE stackable IS NULL;""")


async def ensure_player_inventory_columns() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""ALTER TABLE player_inventory ADD COLUMN IF NOT EXISTS qty INTEGER;""")
        await conn.execute("""ALTER TABLE player_inventory ADD COLUMN IF NOT EXISTS is_equipped BOOLEAN DEFAULT FALSE;""")
        await conn.execute("""ALTER TABLE player_inventory ADD COLUMN IF NOT EXISTS slot TEXT;""")
        await conn.execute("""ALTER TABLE player_inventory ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW();""")
        await conn.execute("""ALTER TABLE player_inventory ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW();""")

        # якщо колись була amount — мігруємо в qty
        await conn.execute(
            """
            DO $$
            BEGIN
              IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name='player_inventory' AND column_name='amount'
              ) THEN
                EXECUTE '
                  UPDATE player_inventory
                  SET qty = amount
                  WHERE amount IS NOT NULL
                    AND (qty IS NULL OR qty=0 OR qty=1)
                    AND (qty IS NULL OR qty <> amount)
                ';
              END IF;
            END $$;
            """
        )
        await conn.execute("""UPDATE player_inventory SET qty = 1 WHERE qty IS NULL OR qty = 0;""")

        # partial unique index для стекабельних (tg_id,item_id) коли slot NULL і не екіп
        await conn.execute(
            """
            DO $$
            BEGIN
              IF NOT EXISTS (
                SELECT 1
                FROM pg_indexes
                WHERE indexname = 'uq_player_inventory_stack'
              ) THEN
                EXECUTE '
                  CREATE UNIQUE INDEX uq_player_inventory_stack
                  ON player_inventory (tg_id, item_id)
                  WHERE slot IS NULL AND is_equipped = FALSE
                ';
              END IF;
            END $$;
            """
        )


async def ensure_players_columns() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        # поточні значення
        await conn.execute("""ALTER TABLE players ADD COLUMN IF NOT EXISTS hp INTEGER;""")
        await conn.execute("""ALTER TABLE players ADD COLUMN IF NOT EXISTS mp INTEGER;""")
        await conn.execute("""ALTER TABLE players ADD COLUMN IF NOT EXISTS energy INTEGER;""")

        # максимуми
        await conn.execute("""ALTER TABLE players ADD COLUMN IF NOT EXISTS hp_max INTEGER DEFAULT 100;""")
        await conn.execute("""ALTER TABLE players ADD COLUMN IF NOT EXISTS mp_max INTEGER DEFAULT 50;""")
        await conn.execute("""ALTER TABLE players ADD COLUMN IF NOT EXISTS energy_max INTEGER DEFAULT 240;""")

        await conn.execute(
            """
            UPDATE players
            SET energy = COALESCE(energy, energy_max)
            WHERE energy IS NULL;
            """
        )