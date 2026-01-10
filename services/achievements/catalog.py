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
    """
    A = AchievementDef
    R = Reward

    # Принцип: ранні — прості, далі — довші грінди.
    return [
        # ─────────────────────────────
        # Бої / Перемоги
        # ─────────────────────────────
        A(
            key="first_win",
            name="Перша перемога",
            description="Виграй свій перший бій.",
            metric_key="battles_won",
            need=1,
            reward=R(coins=10),
        ),
        A(
            key="wins_10",
            name="Десять сутичок",
            description="Виграй 10 боїв.",
            metric_key="battles_won",
            need=10,
            reward=R(coins=60),
        ),
        A(
            key="wins_50",
            name="Загартований",
            description="Виграй 50 боїв.",
            metric_key="battles_won",
            need=50,
            reward=R(coins=300, kleynody=1),
        ),
        A(
            key="wins_200",
            name="Старий вояка",
            description="Виграй 200 боїв.",
            metric_key="battles_won",
            need=200,
            reward=R(coins=1200, kleynody=2),
        ),

        # ─────────────────────────────
        # Вбивства
        # ─────────────────────────────
        A(
            key="kills_25",
            name="Перші трофеї",
            description="Здолай 25 ворогів.",
            metric_key="kills_total",
            need=25,
            reward=R(coins=120),
        ),
        A(
            key="kills_100",
            name="Рука не тремтить",
            description="Здолай 100 ворогів.",
            metric_key="kills_total",
            need=100,
            reward=R(coins=450, kleynody=1),
        ),
        A(
            key="kills_500",
            name="М’ясник курганів",
            description="Здолай 500 ворогів.",
            metric_key="kills_total",
            need=500,
            reward=R(coins=2500, kleynody=3),
        ),

        # ─────────────────────────────
        # Монети з боїв / лут
        # ─────────────────────────────
        A(
            key="coins_500",
            name="Дрібні прибутки",
            description="Збери 500 червонців з боїв.",
            metric_key="coins_from_battles",
            need=500,
            reward=R(coins=150),
        ),
        A(
            key="coins_2500",
            name="Військова каса",
            description="Збери 2500 червонців з боїв.",
            metric_key="coins_from_battles",
            need=2500,
            reward=R(coins=600, kleynody=1),
        ),
        A(
            key="loot_50",
            name="Трофейник",
            description="Отримай 50 трофеїв (дропів).",
            metric_key="loot_drops_total",
            need=50,
            reward=R(coins=250),
        ),
        A(
            key="loot_250",
            name="Грабар",
            description="Отримай 250 трофеїв (дропів).",
            metric_key="loot_drops_total",
            need=250,
            reward=R(coins=1200, kleynody=2),
        ),

        # ─────────────────────────────
        # XP з боїв
        # ─────────────────────────────
        A(
            key="xp_500",
            name="Під набій",
            description="Набери 500 XP з боїв.",
            metric_key="xp_from_battles",
            need=500,
            reward=R(coins=200),
        ),
        A(
            key="xp_3000",
            name="Вивчений кров’ю",
            description="Набери 3000 XP з боїв.",
            metric_key="xp_from_battles",
            need=3000,
            reward=R(coins=900, kleynody=1),
        ),

        # ─────────────────────────────
        # Нічна Варта (медалі)
        # ─────────────────────────────
        A(
            key="nightwatch_1",
            name="Знак Варти",
            description="Знайди 1 Медаль Сторожа.",
            metric_key="nightwatch_medals",
            need=1,
            reward=R(coins=200),
        ),
        A(
            key="nightwatch_5",
            name="Пильний",
            description="Знайди 5 Медалей Сторожа.",
            metric_key="nightwatch_medals",
            need=5,
            reward=R(coins=600, kleynody=1),
        ),
        A(
            key="nightwatch_20",
            name="Вартовий курганів",
            description="Знайди 20 Медалей Сторожа.",
            metric_key="nightwatch_medals",
            need=20,
            reward=R(coins=2500, kleynody=3),
        ),
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