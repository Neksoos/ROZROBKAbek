# services/seed.py
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from loguru import logger

try:
    from ..database import get_pool  # type: ignore
except Exception:
    get_pool = None  # type: ignore

# контент
try:
    from ..content.mobs import MOBS  # type: ignore
except Exception:
    MOBS = []

try:
    from ..content.races import RACES  # type: ignore
except Exception:
    RACES = []

try:
    from ..content.classes import CLASSES  # type: ignore
except Exception:
    CLASSES = []

# квестові/інші предмети
try:
    from ..content.quest_items import ALL_QUEST_ITEMS  # type: ignore
except Exception:
    ALL_QUEST_ITEMS = []

# етно-лут (наш новий сидер)
try:
    from .ethno_loot_seed import ensure_ethno_loot  # type: ignore
except Exception:
    async def ensure_ethno_loot() -> None:
        return

# імпорт синка рецептів (не критично)
try:
    from .recipes_sync import sync_recipes  # type: ignore
except Exception:
    async def sync_recipes() -> int:
        return 0


# ----------------------------- UPSERTS --------------------------------------

UPSERT_MOB = """
INSERT INTO mobs (id, name, level, base_hp, base_attack, base_xp, base_mp, is_training)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
ON CONFLICT (id) DO UPDATE SET
  name=EXCLUDED.name,
  level=EXCLUDED.level,
  base_hp=EXCLUDED.base_hp,
  base_attack=EXCLUDED.base_attack,
  base_xp=EXCLUDED.base_xp,
  base_mp=EXCLUDED.base_mp,
  is_training=EXCLUDED.is_training;
"""

UPSERT_RACE = """
INSERT INTO races (key, name, description, stat_mult, passives)
VALUES ($1,$2,$3,$4::jsonb,$5::jsonb)
ON CONFLICT (key) DO UPDATE SET
  name=EXCLUDED.name,
  description=EXCLUDED.description,
  stat_mult=EXCLUDED.stat_mult,
  passives=EXCLUDED.passives;
"""

UPSERT_CLASS = """
INSERT INTO classes (key, name, description, stat_mult, passives, starter_skills, cp_mod)
VALUES ($1,$2,$3,$4::jsonb,$5::jsonb,$6::jsonb,$7::jsonb)
ON CONFLICT (key) DO UPDATE SET
  name=EXCLUDED.name,
  description=EXCLUDED.description,
  stat_mult=EXCLUDED.stat_mult,
  passives=EXCLUDED.passives,
  starter_skills=EXCLUDED.starter_skills,
  cp_mod=EXCLUDED.cp_mod;
"""

# upsert для предметів (items) – квестові/сервісні
UPSERT_ITEM = """
INSERT INTO items (code, name, descr, stack_max, weight, tradable, bind_on_pickup, rarity, npc_key)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
ON CONFLICT (code) DO UPDATE SET
  name=EXCLUDED.name,
  descr=EXCLUDED.descr,
  stack_max=EXCLUDED.stack_max,
  weight=EXCLUDED.weight,
  tradable=EXCLUDED.tradable,
  bind_on_pickup=EXCLUDED.bind_on_pickup,
  rarity=EXCLUDED.rarity,
  npc_key=EXCLUDED.npc_key,
  updated_at=now();
"""


# ----------------------------- PATH UTILS -----------------------------------

def _project_root() -> Path:
    """
    Корінь проєкту: <repo_root>, працює і з/без папки 'src'.
    Файл зараз у services/seed.py → root = ../../
    """
    return Path(__file__).resolve().parents[2]


def _read_migration_sql(rel_path: str) -> str:
    """
    Зчитує SQL з відносного шляху від кореня репозиторію.
    rel_path приклад: 'db/migrations/022_create_area_resources.sql'
    """
    p = _project_root() / rel_path
    return p.read_text(encoding="utf-8")


# ------------------------------ SEEDERS -------------------------------------

async def seed_mobs() -> None:
    if not get_pool or not MOBS:
        logger.info("mobs: nothing to seed (no pool or empty MOBS).")
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        for m in MOBS:
            await conn.execute(
                UPSERT_MOB,
                getattr(m, "id", None),
                getattr(m, "name", ""),
                int(getattr(m, "level", 1)),
                int(getattr(m, "base_hp", 30)),
                int(getattr(m, "base_attack", 6)),
                int(getattr(m, "base_xp", 10)),
                int(getattr(m, "base_mp", 0)),
                bool(getattr(m, "is_training", False)),
            )
    logger.info(f"✅ Seeded/updated {len(MOBS)} mobs.")


async def seed_races() -> None:
    if not get_pool or not RACES:
        logger.info("races: nothing to seed (no pool or empty RACES).")
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        for r in RACES:
            d = asdict(r)
            await conn.execute(
                UPSERT_RACE,
                d.get("key"),
                d.get("name"),
                d.get("desc"),
                json.dumps(d.get("stat_mult", {}), ensure_ascii=False),
                json.dumps(d.get("passives", {}), ensure_ascii=False),
            )
    logger.info(f"✅ Seeded/updated {len(RACES)} races.")


async def seed_classes() -> None:
    if not get_pool or not CLASSES:
        logger.info("classes: nothing to seed (no pool or empty CLASSES).")
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        for c in CLASSES:
            d = asdict(c)
            await conn.execute(
                UPSERT_CLASS,
                d.get("key"),
                d.get("name"),
                d.get("desc"),
                json.dumps(d.get("stat_mult", {}),     ensure_ascii=False),
                json.dumps(d.get("passives", {}),       ensure_ascii=False),
                json.dumps(d.get("starter_skills", {}), ensure_ascii=False),
                json.dumps(d.get("cp_mod", {}),         ensure_ascii=False),
            )
    logger.info(f"✅ Seeded/updated {len(CLASSES)} classes.")


async def seed_items() -> None:
    """
    Заливає контент з content.quest_items.ALL_QUEST_ITEMS у таблицю items.
    Потрібна міграція 021_create_items.sql (таблиця items).
    """
    if not get_pool or not ALL_QUEST_ITEMS:
        logger.info("items: nothing to seed (no pool or empty ALL_QUEST_ITEMS).")
        return

    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval("SELECT to_regclass('public.items') IS NOT NULL;")
        if not exists:
            logger.warning("⚠️ Таблиці 'items' немає (021_create_items.sql не застосована) — пропускаю seed_items()")
            return

        for it in ALL_QUEST_ITEMS:
            await conn.execute(
                UPSERT_ITEM,
                getattr(it, "code", ""),
                getattr(it, "name", ""),
                getattr(it, "desc", ""),
                int(getattr(it, "stack", 1)),
                int(getattr(it, "weight", 0) or 0),
                bool(getattr(it, "tradable", False)),
                bool(getattr(it, "bind_on_pickup", False)),
                getattr(it, "rarity", "common"),
                getattr(it, "npc_key", "") or "",
            )
    logger.info(f"✅ Seeded/updated {len(ALL_QUEST_ITEMS)} items (quest/other).")


async def seed_area_resources_if_needed() -> None:
    """
    Якщо таблиці area_resources нема або вона порожня — підняти з міграції.
    Працює без жорсткого 'src/' у шляхах.
    """
    if not get_pool:
        return
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            exists = await conn.fetchval("SELECT to_regclass('public.area_resources') IS NOT NULL;")
            if not exists:
                logger.info("🪓 area_resources not found — creating and seeding...")
                sql = _read_migration_sql("db/migrations/022_create_area_resources.sql")
                await conn.execute(sql)
                logger.success("🌿 area_resources created and seeded.")
                return

            cnt = await conn.fetchval("SELECT COUNT(*) FROM area_resources;")
            if cnt and cnt > 0:
                logger.info(f"🌿 area_resources present ({cnt} rows). Skipping seed.")
            else:
                logger.info("🌿 area_resources empty — seeding initial data...")
                sql = _read_migration_sql("db/migrations/022_create_area_resources.sql")
                await conn.execute(sql)
                logger.success("🌿 area_resources seeded.")
    except FileNotFoundError:
        logger.warning("area_resources seed file missing: db/migrations/022_create_area_resources.sql")
    except Exception as e:
        logger.warning(f"area_resources seed skipped due to error: {e}")


# ------------------------------ ENTRYPOINT ----------------------------------

async def seed_all_content() -> None:
    await seed_races()
    await seed_classes()
    await seed_mobs()

    # квестові предмети
    try:
        await seed_items()
    except Exception as e:
        logger.exception(f"items seed failed: {e}")

    # етно-лут (мусор/інгридієнти/консум/трофеї/камінці/руда)
    try:
        await ensure_ethno_loot()
    except Exception as e:
        logger.exception(f"ethno loot seed failed: {e}")

    # рецепти
    try:
        synced = await sync_recipes()
        logger.info(f"📦 Seeded/updated {synced} recipes.")
    except Exception as e:
        logger.exception(f"recipes sync failed: {e}")

    await seed_area_resources_if_needed()

    logger.success("🌱 Content seeded: races, classes, mobs, quest items, ethno loot, recipes, area_resources.")