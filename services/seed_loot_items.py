from __future__ import annotations

"""
Одноразовий сидер для етно-луту.

1) Створює таблицю loot_items, якщо її ще нема.
2) Генерує етно-український лут (статичний + автогенерація).
3) Вставляє все в loot_items з ON CONFLICT (code) DO UPDATE.

Поля в loot_items:
- id
- code          (UNIQUE)
- name
- category      (trash / herb / ore / gem / mat / trophy / consum)
- rarity        (common / uncommon / rare / epic)
- descr
- stack_max
- weight
- tradable
- bind_on_pickup
- npc_key
- is_archived
- base_value
- created_at
- updated_at
"""

import asyncio
import os
from typing import Dict

import asyncpg

from services.loot_generator import get_all_ethno_items_for_db


# Скільки автолута накатувати по кожній категорії
TARGET_COUNTS: Dict[str, int] = {
    "trash": 40,
    "herb": 50,
    "ore": 30,
    "gem": 15,
    "mat": 25,
    "trophy": 15,
    "consum": 25,
}


def _get_db_dsn() -> str:
    """
    Беремо DSN з оточення.

    Підійде будь-що з цього:
        DATABASE_URL=postgres://...
        PG_DSN=postgres://...
    """
    dsn = os.getenv("DATABASE_URL") or os.getenv("PG_DSN")
    if not dsn:
        raise RuntimeError(
            "Не знайдено змінну оточення DATABASE_URL або PG_DSN для підключення до Postgres"
        )
    return dsn


async def seed_loot_items() -> None:
    dsn = _get_db_dsn()
    conn: asyncpg.Connection = await asyncpg.connect(dsn)

    try:
        print("Підключився до бази, перевіряю loot_items...")

        # 1) Створюємо таблицю, якщо її ще нема
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS loot_items (
                id              SERIAL PRIMARY KEY,
                code            TEXT NOT NULL,
                name            TEXT NOT NULL,
                category        TEXT NOT NULL,
                rarity          TEXT NOT NULL,
                descr           TEXT NOT NULL,
                stack_max       INTEGER NOT NULL DEFAULT 1,
                weight          NUMERIC(10, 2) NOT NULL DEFAULT 0,
                tradable        BOOLEAN NOT NULL DEFAULT TRUE,
                bind_on_pickup  BOOLEAN NOT NULL DEFAULT FALSE,
                npc_key         TEXT,
                is_archived     BOOLEAN NOT NULL DEFAULT FALSE,
                base_value      INTEGER NOT NULL DEFAULT 1,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )

        # Унікальний код предмета
        await conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS loot_items_code_uq
            ON loot_items(code);
            """
        )

        print("Таблиця loot_items готова, генерую лут...")

        # 2) Беремо статичні + автозгенеровані предмети
        items = get_all_ethno_items_for_db(
            TARGET_COUNTS,
            min_tier=1,
            max_tier=5,
            seed=42,          # стабільний результат; можна змінити, якщо хочеш інший набір
            include_static=True,
        )

        print(f"Предметів до заливки: {len(items)}")

        sql = """
        INSERT INTO loot_items (
            code,
            name,
            category,
            rarity,
            descr,
            stack_max,
            weight,
            tradable,
            bind_on_pickup,
            npc_key,
            is_archived,
            base_value
        )
        VALUES (
            $1, $2, $3, $4, $5,
            $6, $7, $8, $9, $10,
            $11, $12
        )
        ON CONFLICT (code) DO UPDATE
        SET
            name            = EXCLUDED.name,
            category        = EXCLUDED.category,
            rarity          = EXCLUDED.rarity,
            descr           = EXCLUDED.descr,
            stack_max       = EXCLUDED.stack_max,
            weight          = EXCLUDED.weight,
            tradable        = EXCLUDED.tradable,
            bind_on_pickup  = EXCLUDED.bind_on_pickup,
            npc_key         = EXCLUDED.npc_key,
            is_archived     = EXCLUDED.is_archived,
            base_value      = EXCLUDED.base_value,
            updated_at      = now();
        """

        # 3) Все в одній транзакції
        async with conn.transaction():
            for it in items:
                await conn.execute(
                    sql,
                    it["code"],
                    it["name"],
                    it["category"],
                    it["rarity"],
                    it["descr"],
                    int(it.get("stack_max", 1)),
                    float(it.get("weight", 0)),
                    bool(it.get("tradable", True)),
                    bool(it.get("bind_on_pickup", False)),
                    it.get("npc_key"),
                    bool(it.get("is_archived", False)),
                    int(it.get("base_value", 1)),
                )

        print("Готово: етно-лут залитий/оновлений у таблиці loot_items.")

    finally:
        await conn.close()
        print("Зʼєднання з базою закрито.")


if __name__ == "__main__":
    asyncio.run(seed_loot_items())