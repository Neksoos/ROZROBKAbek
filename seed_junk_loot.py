from __future__ import annotations

from typing import List, Dict, Any
from db import get_pool


UPSERT_JUNK_SQL = """
INSERT INTO items (
    code,
    name,
    descr,
    stack_max,
    weight,
    tradable,
    bind_on_pickup,
    rarity,
    npc_key,
    category
)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
ON CONFLICT (code) DO UPDATE SET
  name           = EXCLUDED.name,
  descr          = EXCLUDED.descr,
  stack_max      = EXCLUDED.stack_max,
  weight         = EXCLUDED.weight,
  tradable       = EXCLUDED.tradable,
  bind_on_pickup = EXCLUDED.bind_on_pickup,
  rarity         = EXCLUDED.rarity,
  npc_key        = EXCLUDED.npc_key,
  category       = EXCLUDED.category,
  updated_at     = now();
"""


JUNK_ITEMS: List[Dict[str, Any]] = [
    {"code": "junk_potertyi_lystok", "name": "Потертий листок", "descr": "Клаптик паперу з майже стертою писаниною."},
    {"code": "junk_zitlia_tkanyna", "name": "Зітля шмати́на тканини", "descr": "Стара тканина, що розсипається в руках."},
    {"code": "junk_trisnutyi_horshchyk", "name": "Тріснутий горщик", "descr": "Глиняний посуд із тріщиною, непридатний до вжитку."},
    {"code": "junk_stara_motuzka", "name": "Стара мотузка", "descr": "Висохла й потріскана мотузка."},
    {"code": "junk_zlamanyi_kilok", "name": "Зламаний кілок", "descr": "Деревʼяний кілок, розколотий навпіл."},
    {"code": "junk_irzhava_tsvyakhyna", "name": "Іржава цвяхина", "descr": "Погнутий іржавий цвях."},
    {"code": "junk_obrizky_shkiry", "name": "Обрізки шкіри", "descr": "Клапті шкіри від старих ременів."},
    {"code": "junk_hlynianyi_cherepok", "name": "Битий глиняний черепок", "descr": "Уламок глиняного посуду."},
    {"code": "junk_kupka_popelu", "name": "Купка попелу", "descr": "Холодний попіл від давнього багаття."},
    {"code": "junk_oharok_svichky", "name": "Огарок свічки", "descr": "Зношений обгорілий кінець свічки."},
    {"code": "junk_stara_lozhka", "name": "Стара ложка", "descr": "Потемніла металева ложка."},
    {"code": "junk_staryi_kukhlyk", "name": "Старий кухлик", "descr": "Глиняний кухлик зі стертою глазурʼю."},
    {"code": "junk_zlamanyi_hrebin", "name": "Зламаний гребінь", "descr": "Деревʼяний гребінь з обламаними зубцями."},
    {"code": "junk_dereviana_triska", "name": "Деревʼяна тріска", "descr": "Тонка тріска від старої дошки."},
    {"code": "junk_pokruchena_drotuna", "name": "Покручена дротина", "descr": "Шматок покрученого дроту."},
    {"code": "junk_hlyniana_tiulka", "name": "Порожня глиняна тюлька", "descr": "Маленька посудина без кришки."},
    {"code": "junk_diriavyi_mishek", "name": "Дірявий мішечок", "descr": "Старий полотняний мішечок із діркою."},
    {"code": "junk_trukhliavyi_klyn", "name": "Трухлявий клин", "descr": "Шматок трухлявого дерева."},
    {"code": "junk_klapot_rohozhi", "name": "Клапоть рогожі", "descr": "Подертий вовняний шматок."},
    {"code": "junk_rozmazana_zapyska", "name": "Розмазана записка", "descr": "Аркуш із розмазаними чорнилами."},
    {"code": "junk_oblizla_fihurka", "name": "Облізла деревʼяна фігурка", "descr": "Стара дитяча фігурка з облупленою фарбою."},
    {"code": "junk_voshchyna", "name": "Непотрібний шматок вощини", "descr": "Обгарок воску без форми."},
    {"code": "junk_dribnyi_kaminchyk", "name": "Дрібний камінчик", "descr": "Маленький звичайний камінчик."},
    {"code": "junk_ulamok_tsehly", "name": "Уламок цегли", "descr": "Частина старої цеглини."},
    {"code": "junk_hanchirka_dim", "name": "Ганчірка з димовим запахом", "descr": "Закіптюжена стара ганчірка."},
    {"code": "junk_stara_pidkova", "name": "Стара підкова", "descr": "Зношена і нікому не потрібна підкова."},
    {"code": "junk_polamana_igrashka", "name": "Поламана іграшка", "descr": "Уламки колишньої деревʼяної іграшки."},
    {"code": "junk_tablichka_hlyniana", "name": "Розтріскана глиняна табличка", "descr": "Стара табличка зі вицвілими подряпинами."},
    {"code": "junk_neparnyi_remynets", "name": "Непарний ремінець", "descr": "Одинокий короткий шкіряний ремінець."},
    {"code": "junk_vytsvila_strichka", "name": "Вицвіла стрічка", "descr": "Стрічка, з якої майже зник колір."},
]


async def seed_junk_loot() -> None:
    pool = await get_pool()

    async with pool.acquire() as conn:
        exists = await conn.fetchval("SELECT to_regclass('public.items') IS NOT NULL;")
        if not exists:
            print("[seed_junk_loot] items table not found, skipping.")
            return

        for item in JUNK_ITEMS:
            await conn.execute(
                UPSERT_JUNK_SQL,
                item["code"],
                item["name"],
                item["descr"],
                99,      # stack_max
                1,       # weight
                True,    # tradable
                False,   # bind_on_pickup
                "Звичайний",
                "",
                "junk",
            )

        print(f"[seed_junk_loot] seeded/updated {len(JUNK_ITEMS)} junk items.")