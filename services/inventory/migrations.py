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

        # best-effort чистка
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
        await conn.execute("""UPDATE player_inventory SET is_equipped = FALSE WHERE is_equipped IS NULL;""")

        # 1) прибрати legacy UNIQUE (tg_id,item_id) якщо існує (constraint або non-partial unique index)
        await conn.execute(
            """
            DO $$
            DECLARE
              c_name text;
              i_name text;
            BEGIN
              SELECT conname INTO c_name
              FROM pg_constraint
              WHERE conrelid = 'player_inventory'::regclass
                AND contype = 'u'
                AND pg_get_constraintdef(oid) ILIKE '%(tg_id, item_id)%'
              LIMIT 1;

              IF c_name IS NOT NULL THEN
                EXECUTE format('ALTER TABLE player_inventory DROP CONSTRAINT %I', c_name);
              END IF;

              SELECT indexname INTO i_name
              FROM pg_indexes
              WHERE schemaname = current_schema()
                AND tablename = 'player_inventory'
                AND indexdef ILIKE '%UNIQUE%'
                AND indexdef ILIKE '%(tg_id, item_id)%'
                AND indexdef NOT ILIKE '%WHERE slot IS NULL AND is_equipped = false%'
              LIMIT 1;

              IF i_name IS NOT NULL THEN
                EXECUTE format('DROP INDEX IF EXISTS %I', i_name);
              END IF;
            END $$;
            """
        )

        # 2) Підчистити "биті" екземпляри екіпу, які колись стали slot=NULL
        #    (щоб створення partial-unique для стеків не падало)
        await conn.execute(
            """
            UPDATE player_inventory pi
            SET slot = i.slot,
                updated_at = NOW()
            FROM items i
            WHERE i.id = pi.item_id
              AND pi.slot IS NULL
              AND COALESCE(i.slot,'') <> ''
              AND COALESCE(pi.is_equipped, FALSE) = FALSE;
            """
        )

        # 3) partial unique index для стеків:
        #    рівно 1 рядок на (tg_id,item_id) тільки коли slot NULL і не екіп
        await conn.execute(
            """
            DO $$
            BEGIN
              IF NOT EXISTS (
                SELECT 1
                FROM pg_indexes
                WHERE schemaname = current_schema()
                  AND indexname = 'uq_player_inventory_stack'
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

        # 4) гарантує, що в одному слоті може бути лише 1 екіп (на гравця)
        await conn.execute(
            """
            DO $$
            BEGIN
              IF NOT EXISTS (
                SELECT 1
                FROM pg_indexes
                WHERE schemaname = current_schema()
                  AND indexname = 'uq_player_equipped_slot'
              ) THEN
                EXECUTE '
                  CREATE UNIQUE INDEX uq_player_equipped_slot
                  ON player_inventory (tg_id, slot)
                  WHERE is_equipped = TRUE
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