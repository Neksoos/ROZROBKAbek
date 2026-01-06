# services/ensure_schema.py
from __future__ import annotations

from loguru import logger

# мініап: пул беремо з кореневого модуля db
try:
    from db import get_pool  # type: ignore
except Exception as e:  # fallback
    logger.warning(f"ensure_schema: db import failed: {e}")
    get_pool = None  # type: ignore


async def _ensure_items_base_value_column(pool) -> None:
    """
    Гарантуємо, що в таблиці items є колонка base_value INTEGER з дефолтом 1.
    Працює акуратно й ідемпотентно.
    """
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'items'
                  AND column_name = 'base_value'
            );
            """
        )
        if exists:
            logger.info("Schema OK: items.base_value вже існує.")
            return

        logger.warning("Fix: items.base_value відсутня — додаю колонку...")

        async with conn.transaction():
            await conn.execute(
                """
                ALTER TABLE items
                ADD COLUMN IF NOT EXISTS base_value INTEGER;
                """
            )
            await conn.execute(
                """
                ALTER TABLE items
                ALTER COLUMN base_value SET DEFAULT 1;
                """
            )
            await conn.execute(
                """
                UPDATE items
                SET base_value = 1
                WHERE base_value IS NULL;
                """
            )

        logger.success("Fix applied: items.base_value INTEGER DEFAULT 1 ✔")


async def _ensure_items_npc_key_nullable(pool) -> None:
    """
    Гарантуємо, що items.npc_key допускає NULL (щоб автолут міг писати npc_key = NULL).
    Якщо вже NULLABLE — нічого не робимо.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT is_nullable
            FROM information_schema.columns
            WHERE table_name = 'items'
              AND column_name = 'npc_key'
            """
        )
        is_nullable = (row["is_nullable"] if row else "").upper()
        if is_nullable == "YES":
            logger.info("Schema OK: items.npc_key вже допускає NULL.")
            return

        logger.warning("Fix: items.npc_key має NOT NULL — роблю nullable...")

        async with conn.transaction():
            await conn.execute(
                """
                ALTER TABLE items
                ALTER COLUMN npc_key DROP NOT NULL;
                """
            )

        logger.success("Fix applied: items.npc_key DROP NOT NULL ✔")


async def _ensure_player_items_item_fk_fix(pool) -> None:
    """
    ІСТОРИЧНИЙ ФІКС для player_items.item_id.

    ТЕПЕР:
    - якщо таблиці player_items НЕМає (ти перейшов на єдиний інвентарь) — просто скіпаємо.
    - якщо є — поводимось як раніше: робимо item_id INTEGER + FK.
    """
    async with pool.acquire() as conn:
        # 0) Перевіряємо, чи таблиця ще існує
        exists = await conn.fetchval(
            """
            SELECT EXISTS (
              SELECT 1
              FROM information_schema.tables
              WHERE table_schema = 'public'
                AND table_name = 'player_items'
            );
            """
        )

        if not exists:
            logger.info(
                "Schema info: table player_items не знайдена — "
                "ймовірно, використовується новий єдиний інвентарь. "
                "Скіпаю _ensure_player_items_item_fk_fix."
            )
            return

        # 1) Перевіряємо поточний тип колонки
        row = await conn.fetchrow(
            """
            SELECT data_type
            FROM information_schema.columns
            WHERE table_name='player_items' AND column_name='item_id'
            """
        )
        dtype = (row["data_type"] if row else None) or ""
        if dtype.lower() in {"integer", "int", "int4"}:
            logger.info("Schema OK: player_items.item_id already INTEGER.")
            return

        logger.warning(
            f"Fix: player_items.item_id is not INTEGER (found: {dtype}) -> converting..."
        )

        async with conn.transaction():
            # 2) Зняти FK, якщо існує
            await conn.execute(
                """
                ALTER TABLE IF EXISTS player_items
                DROP CONSTRAINT IF EXISTS player_items_item_id_fkey;
                """
            )

            # 3) Прибрати нечислові item_id
            await conn.execute(
                """
                DELETE FROM player_items
                WHERE NOT (item_id ~ '^[0-9]+$');
                """
            )

            # 4) Прибрати «висячі» посилання (яких немає в items)
            await conn.execute(
                """
                DELETE FROM player_items pi
                WHERE (item_id)::integer NOT IN (SELECT id FROM items);
                """
            )

            # 5) Змінити тип на INTEGER
            await conn.execute(
                """
                ALTER TABLE player_items
                ALTER COLUMN item_id TYPE INTEGER
                USING item_id::integer;
                """
            )

            # 6) Повернути FK
            await conn.execute(
                """
                ALTER TABLE player_items
                ADD CONSTRAINT player_items_item_id_fkey
                FOREIGN KEY (item_id) REFERENCES items(id)
                ON DELETE CASCADE;
                """
            )

            # 7) Створити індекс, якщо його немає
            await conn.execute(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_indexes
                        WHERE schemaname = 'public'
                          AND indexname = 'player_items_item_id_idx'
                    ) THEN
                        CREATE INDEX player_items_item_id_idx
                        ON player_items(item_id);
                    END IF;
                END$$;
                """
            )

        logger.success("Fix applied: player_items.item_id -> INTEGER with FK ✔")


async def ensure_schema_pool() -> None:
    """
    Єдина точка для автоперевірок/автофіксів при старті бекенду.
    """
    if not get_pool:
        logger.warning("ensure_schema_pool: no DB pool is available")
        return

    try:
        pool = await get_pool()
    except Exception as e:
        logger.warning(f"ensure_schema_pool: get_pool failed: {e}")
        return

    # 1) Колонка base_value в items
    try:
        await _ensure_items_base_value_column(pool)
    except Exception as e:
        logger.warning(f"_ensure_items_base_value_column skipped/failed: {e}")
    else:
        logger.success("DB schema ensured: items.base_value ✔")

    # 2) npc_key має бути NULLABLE
    try:
        await _ensure_items_npc_key_nullable(pool)
    except Exception as e:
        logger.warning(f"_ensure_items_npc_key_nullable skipped/failed: {e}")
    else:
        logger.success("DB schema ensured: items.npc_key nullable ✔")

    # 3) Виправлення типу та FK для player_items.item_id
    #    (якщо цієї таблиці вже нема — функція сама скіпає)
    try:
        await _ensure_player_items_item_fk_fix(pool)
    except Exception as e:
        logger.warning(f"_ensure_player_items_item_fk_fix skipped/failed: {e}")
    else:
        logger.success("DB schema ensured: player_items.item_id int + FK ✔")


__all__ = [
    "ensure_schema_pool",
    "_ensure_items_base_value_column",
    "_ensure_items_npc_key_nullable",
    "_ensure_player_items_item_fk_fix",
]