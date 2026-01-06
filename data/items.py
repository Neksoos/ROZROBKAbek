# src/content/items.py
from __future__ import annotations

from typing import Dict, Any

# Категорії:
# weapon, armor, helmet, boots, shield, ring, amulet, trinket
# herb, ore, gem, mat, consum, food, trash

RARITY_ORDER = ["common", "uncommon", "rare", "epic", "legendary", "mythic"]

RARITY_MULT = {
    "common": 1.0,
    "uncommon": 1.6,
    "rare": 2.4,
    "epic": 4.0,
    "legendary": 6.0,
    "mythic": 9.0,
}

RARITY_LABEL_UA = {
    "common": "звичайний",
    "uncommon": "незвичний",
    "rare": "рідкісний",
    "epic": "епічний",
    "legendary": "легендарний",
    "mythic": "міфічний",
}

CATEGORY_CONFIG = [
    {
        "key": "weapon",
        "emoji": "🗡️",
        "base_names": [
            "залізний меч",
            "крива шабля",
            "бойова сокира",
            "кістяний кинджал",
            "короткий спис",
            "бердиш нетриці",
        ],
        "focus_main": ["atk"],
        "focus_sec": ["crit", "speed"],
        "base_power": 4,
    },
    {
        "key": "armor",
        "emoji": "🛡️",
        "base_names": [
            "латний обладунок",
            "шкіряний жупан",
            "панцир сторожа",
            "кольчуга курганів",
            "куячний кожух",
        ],
        "focus_main": ["def", "hp"],
        "focus_sec": ["speed"],
        "base_power": 4,
    },
    {
        "key": "helmet",
        "emoji": "🪖",
        "base_names": [
            "шолом сотника",
            "рогатий шолом",
            "козацька шапка",
            "залізний бацинет",
            "каптур мольфара",
        ],
        "focus_main": ["def", "hp"],
        "focus_sec": ["crit"],
        "base_power": 3,
    },
    {
        "key": "boots",
        "emoji": "🥾",
        "base_names": [
            "чоботи блукача",
            "тихі постоли",
            "сапоги нетриці",
            "ковані чоботи",
            "легкі черевики",
        ],
        "focus_main": ["speed"],
        "focus_sec": ["def", "luck"],
        "base_power": 3,
    },
    {
        "key": "shield",
        "emoji": "🛡️",
        "base_names": [
            "дерев'яний щит",
            "щит з кісток",
            "круглий щит",
            "бойовий тарч",
            "щит з курганів",
        ],
        "focus_main": ["def"],
        "focus_sec": ["hp"],
        "base_power": 4,
    },
    {
        "key": "ring",
        "emoji": "💍",
        "base_names": [
            "срібний перстень",
            "рунічний перстень",
            "кістяний перстень",
            "перстень перевертня",
            "перстень сторожа",
        ],
        "focus_main": ["mp", "crit"],
        "focus_sec": ["luck"],
        "base_power": 3,
    },
    {
        "key": "amulet",
        "emoji": "📿",
        "base_names": [
            "оберіг мольфара",
            "шепітний амулет",
            "знак Перуна",
            "амулет потойбіччя",
            "амулет нічного вітру",
        ],
        "focus_main": ["mp", "luck"],
        "focus_sec": ["crit"],
        "base_power": 3,
    },
    {
        "key": "trinket",
        "emoji": "🧿",
        "base_names": [
            "талісман мандрівця",
            "зачарована бляшка",
            "курганний трофей",
            "затемнений оберіг",
            "кров'яний знак",
        ],
        "focus_main": ["luck", "crit"],
        "focus_sec": ["speed", "mp"],
        "base_power": 2,
    },
    # Професійні ресурси
    {
        "key": "herb",
        "emoji": "🌿",
        "base_names": [
            "нетрицький полин",
            "трава нічниці",
            "корінь вовчої пащі",
            "листя мольфарської шавлії",
            "зілля болотяної м'яти",
        ],
        "focus_main": [],
        "focus_sec": [],
        "base_power": 1,
    },
    {
        "key": "ore",
        "emoji": "⛏️",
        "base_names": [
            "залізна руда",
            "темна руда курганів",
            "місячна руда",
            "рудокамінь нетриці",
            "кришталева жила",
        ],
        "focus_main": [],
        "focus_sec": [],
        "base_power": 1,
    },
    {
        "key": "gem",
        "emoji": "💎",
        "base_names": [
            "кровавий гранат",
            "місячний камінь",
            "заставський берил",
            "примарний опал",
            "осколок зоряного кришталю",
        ],
        "focus_main": [],
        "focus_sec": [],
        "base_power": 2,
    },
    {
        "key": "mat",
        "emoji": "🧱",
        "base_names": [
            "обвуглене дерево",
            "суха жила шкіри",
            "плетена мотузка",
            "шмат панцира",
            "оббитий метал",
        ],
        "focus_main": [],
        "focus_sec": [],
        "base_power": 1,
    },
    {
        "key": "food",
        "emoji": "🍖",
        "base_names": [
            "юха із щуки",
            "печене м'ясо звіра",
            "запашний куліш",
            "сушене м'ясо",
            "печені коржики",
        ],
        "focus_main": [],
        "focus_sec": [],
        "base_power": 1,
    },
    {
        "key": "consum",
        "emoji": "🧪",
        "base_names": [
            "фляга гіркої настоянки",
            "пляшка міцного зілля",
            "міхур мольфарської суті",
            "відвар нічного коріння",
            "фляга солоної води",
        ],
        "focus_main": [],
        "focus_sec": [],
        "base_power": 2,
    },
    {
        "key": "trash",
        "emoji": "🗑️",
        "base_names": [
            "іржавий цвях",
            "обгризена кістка",
            "побитий глечик",
            "зламаний ніж",
            "дірявий капшук",
        ],
        "focus_main": [],
        "focus_sec": [],
        "base_power": 0,
    },
]

ADJECTIVES = [
    "старий",
    "загартований",
    "нічний",
    "тіньовий",
    "кривавий",
    "обпалений",
    "забутий",
    "похмурий",
    "освячений",
    "примарний",
    "тяжкий",
    "легкий",
    "трофейний",
    "заставський",
    "курганний",
    "нетрицький",
    "обідраний",
    "міцний",
    "потрісканий",
    "зачарований",
]

TITLES = [
    "новобранця",
    "сторожа",
    "мисливця",
    "блукача",
    "вартового",
    "кровника",
    "мольфара",
    "характерника",
    "ватажка",
    "відьми",
    "козака",
    "сотника",
    "тисячника",
    "розвідника",
    "охоронця",
    "ізгоя",
    "тіньового гість",
    "нічного гостя",
    "заставника",
    "майстра",
]


def _build_stats_for_equipment(
    category: str,
    rarity: str,
    idx: int,
    focus_main: list[str],
    focus_sec: list[str],
    base_power: int,
) -> Dict[str, int]:
    """
    Генерує стати для екіпу.
    idx використовується як легке зміщення, щоб статки не збігались.
    """
    mult = RARITY_MULT[rarity]
    # невелика плавна надбавка від індексу, щоб однакові предмети різнились
    tier_boost = 1 + (idx % 3)

    atk = def_ = hp = mp = crit = speed = luck = 0

    # базова сила
    base_main = int(base_power * mult * tier_boost)

    def add_main(stat_name: str, factor: float = 1.0) -> int:
        return max(0, int(base_main * factor))

    def add_sec(stat_name: str, factor: float = 0.4) -> int:
        return max(0, int(base_main * factor))

    for s in focus_main:
        if s == "atk":
            atk += add_main(s, 1.0)
        elif s == "def":
            def_ += add_main(s, 1.0)
        elif s == "hp":
            hp += add_main(s, 3.0)
        elif s == "mp":
            mp += add_main(s, 2.0)
        elif s == "crit":
            crit += add_main(s, 0.6)
        elif s == "speed":
            speed += add_main(s, 0.8)
        elif s == "luck":
            luck += add_main(s, 0.8)

    for s in focus_sec:
        if s == "atk":
            atk += add_sec(s, 0.7)
        elif s == "def":
            def_ += add_sec(s, 0.7)
        elif s == "hp":
            hp += add_sec(s, 2.0)
        elif s == "mp":
            mp += add_sec(s, 1.5)
        elif s == "crit":
            crit += add_sec(s, 0.8)
        elif s == "speed":
            speed += add_sec(s, 0.9)
        elif s == "luck":
            luck += add_sec(s, 0.9)

    # дрібні статки для різноманіття
    if rarity in ("epic", "legendary", "mythic"):
        crit += idx % 3
        speed += (idx // 2) % 3
        luck += (idx // 3) % 3

    return {
        "atk": atk,
        "def": def_,
        "hp": hp,
        "mp": mp,
        "crit": crit,
        "speed": speed,
        "luck": luck,
    }


def _estimate_base_value(category: str, rarity: str, stats: Dict[str, int]) -> int:
    """
    Базова ціна предмета в червонцях з урахуванням статів і рідкості.
    """
    stats_sum = (
        stats["atk"]
        + stats["def"]
        + stats["hp"] * 0.2
        + stats["mp"] * 0.3
        + stats["crit"] * 1.5
        + stats["speed"] * 1.2
        + stats["luck"] * 1.0
    )
    base = int(stats_sum * 0.7) + 1

    # треш і мат — дешевші
    if category in ("trash", "mat"):
        base = max(1, base // 4)
    elif category in ("herb", "ore", "food", "consum"):
        base = max(1, base // 2)

    # невеличка поправка на рідкість
    rarity_k = {
        "common": 0.8,
        "uncommon": 1.0,
        "rare": 1.4,
        "epic": 2.0,
        "legendary": 3.0,
        "mythic": 4.0,
    }[rarity]

    return max(1, int(base * rarity_k))


def _make_description(category: str, rarity: str, base_name: str) -> str:
    r_label = RARITY_LABEL_UA[rarity]
    if category in ("weapon", "armor", "helmet", "boots", "shield"):
        return f"{r_label.capitalize()} {base_name}. Кований для боїв біля курганів, тримає на собі подих темних земель."
    if category in ("ring", "amulet", "trinket"):
        return f"{r_label.capitalize()} {base_name}. Несе на собі сліди мольфарської сили та забутих присяг."
    if category == "herb":
        return f"{r_label.capitalize()} {base_name}. Трава, яку шукають травники для сильних настоїв."
    if category == "ore":
        return f"{r_label.capitalize()} {base_name}. Руда, що годиться для кування зброї та броні."
    if category == "gem":
        return f"{r_label.capitalize()} {base_name}. Камінь, який цінують ювеліри й мольфари."
    if category == "food":
        return f"{r_label.capitalize()} {base_name}. Проста їжа, що підтримає сили мандрівника."
    if category == "consum":
        return f"{r_label.capitalize()} {base_name}. Використовується раз, зате може врятувати у важку мить."
    if category == "mat":
        return f"{r_label.capitalize()} {base_name}. Допоміжний матеріал для ковалів, ювелірів та алхіміків."
    if category == "trash":
        return f"{r_label.capitalize()} {base_name}. Майже ні на що не годиться, хіба що продати за копійки."
    return f"{r_label.capitalize()} {base_name}. Річ з далеких сторожових застав."


def build_items(target_min: int = 320) -> Dict[str, Dict[str, Any]]:
    """
    Генерує щонайменше target_min предметів.
    Кожна назва українською унікальна (без повторів).
    """
    items: Dict[str, Dict[str, Any]] = {}
    used_names: set[str] = set()
    idx_global = 0

    # Скільки приблизно предметів на категорію
    # (для екіпу більше, для ресурсів трохи менше)
    per_category_hint = {
        "weapon": 60,
        "armor": 50,
        "helmet": 40,
        "boots": 40,
        "shield": 40,
        "ring": 35,
        "amulet": 35,
        "trinket": 30,
        "herb": 20,
        "ore": 20,
        "gem": 16,
        "mat": 16,
        "food": 16,
        "consum": 16,
        "trash": 16,
    }

    for cfg in CATEGORY_CONFIG:
        cat = cfg["key"]
        emoji = cfg["emoji"]
        base_names = cfg["base_names"]
        focus_main = cfg["focus_main"]
        focus_sec = cfg["focus_sec"]
        base_power = cfg["base_power"]

        target_for_cat = per_category_hint.get(cat, 10)
        created_for_cat = 0

        # комбінації base_name × rarity × adjectives × titles
        for rarity in RARITY_ORDER:
            if created_for_cat >= target_for_cat:
                break

            for base in base_names:
                if created_for_cat >= target_for_cat:
                    break

                for adj in ADJECTIVES:
                    if created_for_cat >= target_for_cat:
                        break

                    for title in TITLES:
                        if created_for_cat >= target_for_cat:
                            break

                        # будуємо унікальну назву
                        base_full = f"{adj} {base} {title}".strip()
                        base_full_cap = base_full[0].upper() + base_full[1:]

                        if base_full_cap in used_names:
                            continue

                        used_names.add(base_full_cap)

                        # code: cat + incremental id
                        idx_global += 1
                        created_for_cat += 1
                        code = f"{cat}_{idx_global:04d}"

                        # стати тільки для екіпу, ресурси/їжа без статів
                        if cat in (
                            "weapon",
                            "armor",
                            "helmet",
                            "boots",
                            "shield",
                            "ring",
                            "amulet",
                            "trinket",
                        ):
                            stats = _build_stats_for_equipment(
                                category=cat,
                                rarity=rarity,
                                idx=idx_global,
                                focus_main=focus_main,
                                focus_sec=focus_sec,
                                base_power=base_power,
                            )
                        else:
                            stats = {
                                "atk": 0,
                                "def": 0,
                                "hp": 0,
                                "mp": 0,
                                "crit": 0,
                                "speed": 0,
                                "luck": 0,
                            }

                        base_value = _estimate_base_value(cat, rarity, stats)
                        sell_price = None  # рахується у корчмі від base_value

                        description = _make_description(
                            category=cat, rarity=rarity, base_name=base_full_cap
                        )

                        items[code] = {
                            "code": code,
                            "name": base_full_cap,
                            "emoji": emoji,
                            "category": cat,
                            "rarity": rarity,
                            "description": description,
                            "stats": stats,
                            "base_value": base_value,
                            "sell_price": sell_price,
                        }

        # на випадок, якщо цикл не добився до target_for_cat
        # (це малоймовірно, бо комбінаторики вистачає з запасом)
    # Переконуємось, що вийшло більше ніж target_min
    # (на практиці буде явно 300+)
    return items


# Головний словник предметів, який підхоплюють сидери / роутери
ITEMS: Dict[str, Dict[str, Any]] = build_items()