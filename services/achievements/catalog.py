# services/achievements/catalog.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Reward:
    coins: int = 0
    kleynody: int = 0


@dataclass(frozen=True)
class AchievementDef:
    key: str
    name: str
    description: str

    # умова "метрика >= need"
    metric_key: str
    need: int

    # нагорода
    reward: Reward

    # чи одноразова (практично всі ачівки одноразові)
    once: bool = True


def all_achievements() -> List[AchievementDef]:
    """
    Каталог досягнень.
    Ключ ачівки -> event_key = f"achv:{key}"

    Принципи:
    - Українські, короткі, ремісничо-похмурі назви (без пафосу/магії словами).
    - Ранні — швидко відкриваються, далі — довший грінд.
    - Нагороди помірні: монети часто, клейноди рідше.
    """
    A = AchievementDef
    R = Reward

    return [
        # ─────────────────────────────
        # Бої / Перемоги (battles_won)
        # ─────────────────────────────
        A("first_win", "Перша перемога", "Виграй свій перший бій.", "battles_won", 1, R(coins=10)),
        A("wins_5", "П'ять сутичок", "Виграй 5 боїв.", "battles_won", 5, R(coins=30)),
        A("wins_10", "Десять сутичок", "Виграй 10 боїв.", "battles_won", 10, R(coins=60)),
        A("wins_25", "Твердий крок", "Виграй 25 боїв.", "battles_won", 25, R(coins=160)),
        A("wins_50", "Загартований", "Виграй 50 боїв.", "battles_won", 50, R(coins=300, kleynody=1)),
        A("wins_100", "Без зайвих слів", "Виграй 100 боїв.", "battles_won", 100, R(coins=700, kleynody=1)),
        A("wins_200", "Старий вояка", "Виграй 200 боїв.", "battles_won", 200, R(coins=1200, kleynody=2)),
        A("wins_500", "Стіна сталі", "Виграй 500 боїв.", "battles_won", 500, R(coins=3500, kleynody=4)),

        # ─────────────────────────────
        # Бої / Всього (battles_total) — корисно якщо додаси метрику за поразки
        # ─────────────────────────────
        A("battles_10", "Польова звичка", "Проведи 10 боїв.", "battles_total", 10, R(coins=40)),
        A("battles_50", "Не відступив", "Проведи 50 боїв.", "battles_total", 50, R(coins=220)),
        A("battles_200", "Гуркіт буднів", "Проведи 200 боїв.", "battles_total", 200, R(coins=1100, kleynody=1)),
        A("battles_600", "Довга дорога", "Проведи 600 боїв.", "battles_total", 600, R(coins=3200, kleynody=3)),

        # ─────────────────────────────
        # Вбивства (kills_total)
        # ─────────────────────────────
        A("kills_10", "Розігрів", "Здолай 10 ворогів.", "kills_total", 10, R(coins=30)),
        A("kills_25", "Перші трофеї", "Здолай 25 ворогів.", "kills_total", 25, R(coins=120)),
        A("kills_50", "Рука рівна", "Здолай 50 ворогів.", "kills_total", 50, R(coins=220)),
        A("kills_100", "Рука не тремтить", "Здолай 100 ворогів.", "kills_total", 100, R(coins=450, kleynody=1)),
        A("kills_250", "Чорна робота", "Здолай 250 ворогів.", "kills_total", 250, R(coins=1200, kleynody=1)),
        A("kills_500", "М’ясник курганів", "Здолай 500 ворогів.", "kills_total", 500, R(coins=2500, kleynody=3)),
        A("kills_1000", "Сухий рахунок", "Здолай 1000 ворогів.", "kills_total", 1000, R(coins=6000, kleynody=5)),

        # ─────────────────────────────
        # Вбивства по рівнях (kills_lvl_XX) — у тебе інкрементиться f"kills_lvl_{lvl:02d}"
        # ─────────────────────────────
        A("kills_lvl_05_25", "Нижня межа", "Здолай 25 ворогів 5-го рівня.", "kills_lvl_05", 25, R(coins=180)),
        A("kills_lvl_10_50", "Десятка", "Здолай 50 ворогів 10-го рівня.", "kills_lvl_10", 50, R(coins=600, kleynody=1)),
        A("kills_lvl_15_50", "П'ятнадцятка", "Здолай 50 ворогів 15-го рівня.", "kills_lvl_15", 50, R(coins=900, kleynody=1)),
        A("kills_lvl_20_75", "Двадцятка", "Здолай 75 ворогів 20-го рівня.", "kills_lvl_20", 75, R(coins=1600, kleynody=2)),

        # ─────────────────────────────
        # XP з боїв (xp_from_battles)
        # ─────────────────────────────
        A("xp_200", "Перший досвід", "Набери 200 XP з боїв.", "xp_from_battles", 200, R(coins=80)),
        A("xp_500", "Під набій", "Набери 500 XP з боїв.", "xp_from_battles", 500, R(coins=200)),
        A("xp_1500", "Звик до удару", "Набери 1500 XP з боїв.", "xp_from_battles", 1500, R(coins=520, kleynody=1)),
        A("xp_3000", "Вивчений кров’ю", "Набери 3000 XP з боїв.", "xp_from_battles", 3000, R(coins=900, kleynody=1)),
        A("xp_8000", "Досвід без легенд", "Набери 8000 XP з боїв.", "xp_from_battles", 8000, R(coins=2600, kleynody=2)),
        A("xp_20000", "Тягар дороги", "Набери 20000 XP з боїв.", "xp_from_battles", 20000, R(coins=8000, kleynody=5)),

        # ─────────────────────────────
        # Монети з боїв (coins_from_battles)
        # ─────────────────────────────
        A("coins_200", "На дрібне", "Збери 200 червонців з боїв.", "coins_from_battles", 200, R(coins=60)),
        A("coins_500", "Дрібні прибутки", "Збери 500 червонців з боїв.", "coins_from_battles", 500, R(coins=150)),
        A("coins_1200", "Поясний мішок", "Збери 1200 червонців з боїв.", "coins_from_battles", 1200, R(coins=350)),
        A("coins_2500", "Військова каса", "Збери 2500 червонців з боїв.", "coins_from_battles", 2500, R(coins=600, kleynody=1)),
        A("coins_6000", "Повний гаманець", "Збери 6000 червонців з боїв.", "coins_from_battles", 6000, R(coins=1600, kleynody=2)),
        A("coins_15000", "Золота звичка", "Збери 15000 червонців з боїв.", "coins_from_battles", 15000, R(coins=4200, kleynody=3)),

        # ─────────────────────────────
        # Лут/дроп (loot_drops_total)
        # ─────────────────────────────
        A("loot_10", "Перший мішок", "Отримай 10 трофеїв (дропів).", "loot_drops_total", 10, R(coins=40)),
        A("loot_50", "Трофейник", "Отримай 50 трофеїв (дропів).", "loot_drops_total", 50, R(coins=250)),
        A("loot_120", "Складач", "Отримай 120 трофеїв (дропів).", "loot_drops_total", 120, R(coins=600, kleynody=1)),
        A("loot_250", "Грабар", "Отримай 250 трофеїв (дропів).", "loot_drops_total", 250, R(coins=1200, kleynody=2)),
        A("loot_600", "Комірник", "Отримай 600 трофеїв (дропів).", "loot_drops_total", 600, R(coins=3200, kleynody=3)),

        # ─────────────────────────────
        # Застава (fort_xp_from_kills) — у battle/rewards.py ти інкрементиш цю метрику
        # ─────────────────────────────
        A("fortxp_200", "Підмога", "Здобудь 200 XP для застави з боїв.", "fort_xp_from_kills", 200, R(coins=120)),
        A("fortxp_800", "Опора", "Здобудь 800 XP для застави з боїв.", "fort_xp_from_kills", 800, R(coins=450, kleynody=1)),
        A("fortxp_2500", "Служба день у день", "Здобудь 2500 XP для застави з боїв.", "fort_xp_from_kills", 2500, R(coins=1400, kleynody=2)),
        A("fortxp_8000", "Кістяк застави", "Здобудь 8000 XP для застави з боїв.", "fort_xp_from_kills", 8000, R(coins=4500, kleynody=4)),

        # ─────────────────────────────
        # Нічна Варта (nightwatch_medals)
        # ─────────────────────────────
        A("nightwatch_1", "Знак Варти", "Знайди 1 Медаль Сторожа.", "nightwatch_medals", 1, R(coins=200)),
        A("nightwatch_3", "Око в пітьмі", "Знайди 3 Медалі Сторожа.", "nightwatch_medals", 3, R(coins=420)),
        A("nightwatch_5", "Пильний", "Знайди 5 Медалей Сторожа.", "nightwatch_medals", 5, R(coins=600, kleynody=1)),
        A("nightwatch_10", "Сторожовий рахунок", "Знайди 10 Медалей Сторожа.", "nightwatch_medals", 10, R(coins=1200, kleynody=1)),
        A("nightwatch_20", "Вартовий курганів", "Знайди 20 Медалей Сторожа.", "nightwatch_medals", 20, R(coins=2500, kleynody=3)),
        A("nightwatch_40", "Старший сторож", "Знайди 40 Медалей Сторожа.", "nightwatch_medals", 40, R(coins=5200, kleynody=5)),

        # ─────────────────────────────
        # Зони (wins_area_*) — у тебе інкрементиться wins_area_{area}
        # Приклади для “slums” та “unknown” (під свої area_key додаси ще)
        # ─────────────────────────────
        A("wins_area_slums_10", "Сліди на болоті", "Виграй 10 боїв у локації slums.", "wins_area_slums", 10, R(coins=160)),
        A("wins_area_slums_50", "Знаєш провулки", "Виграй 50 боїв у локації slums.", "wins_area_slums", 50, R(coins=900, kleynody=1)),
        A("wins_area_unknown_10", "Там, де не питають", "Виграй 10 боїв у невідомій зоні.", "wins_area_unknown", 10, R(coins=120)),

        # ─────────────────────────────
        # “Сума рівнів убитих мобів” (kills_lvl_sum) — у тебе інкрементиться сумою рівнів
        # ─────────────────────────────
        A("lvl_sum_200", "Сходинка за сходинкою", "Набери 200 сумарних рівнів з убитих ворогів.", "kills_lvl_sum", 200, R(coins=220)),
        A("lvl_sum_1000", "Підрахунок", "Набери 1000 сумарних рівнів з убитих ворогів.", "kills_lvl_sum", 1000, R(coins=900, kleynody=1)),
        A("lvl_sum_5000", "Тяжка робота", "Набери 5000 сумарних рівнів з убитих ворогів.", "kills_lvl_sum", 5000, R(coins=4200, kleynody=3)),
    ]


def achievements_by_metric() -> Dict[str, List[AchievementDef]]:
    """
    Допоміжна мапа: metric_key -> список ачівок, які від нього залежать.
    """
    out: Dict[str, List[AchievementDef]] = {}
    for a in all_achievements():
        out.setdefault(a.metric_key, []).append(a)

    # стабільно: менші need перевіряються раніше
    for k in list(out.keys()):
        out[k] = sorted(out[k], key=lambda x: int(x.need))
    return out


def get_achievement(key: str) -> Optional[AchievementDef]:
    for a in all_achievements():
        if a.key == key:
            return a
    return None