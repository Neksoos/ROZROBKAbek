from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple, List, Set, Any

from .npc_defs import NpcDef, SpawnRules, all_npcs, get_npc as get_npc_def
from .npc_quests import (
    QuestDef,
    QuestStage,
    ALL_QUESTS,
    get_quests_for_npc,
    get_quest,
)

# ───────────────────────────────────────────────────────────────
# In-memory стан появ NPC (можна замінити на Redis/DB)
# ───────────────────────────────────────────────────────────────

_last_npc_seen: Dict[Tuple[int, str], float] = {}     # (uid, npc_key) -> last_seen_ts
_last_screen_for_uid: Dict[int, str] = {}             # uid -> останній екран


def _cooldown_ok(uid: int, npc: NpcDef, now_ts: float) -> bool:
    ts = _last_npc_seen.get((uid, npc.key), 0.0)
    return (now_ts - ts) >= npc.spawn.cooldown_sec


def _mark_seen(uid: int, npc: NpcDef, now_ts: float) -> None:
    _last_npc_seen[(uid, npc.key)] = now_ts


# ───────────────────────────────────────────────────────────────
# Говор NPC — без універсальних фраз
# ───────────────────────────────────────────────────────────────

class NpcSpeechError(RuntimeError):
    pass


def npc_say(npc: NpcDef, slot: str, *, strict: bool = True) -> str:
    seq: Optional[Sequence[str]] = getattr(npc.speech, slot, None)
    if not seq:
        if strict:
            raise NpcSpeechError(f"[{npc.key}] немає фраз у слоті '{slot}'")
        return ""
    return random.choice(list(seq))


# ───────────────────────────────────────────────────────────────
# JSON-функції для фронтенду
# ───────────────────────────────────────────────────────────────

def make_encounter_data(npc: NpcDef) -> Dict[str, Any]:
    """Повертає JSON-дані для фронту: ключ, ім’я, короткий опис."""
    return {
        "key": npc.key,
        "name": npc.name,
        "region": npc.region,
        "tags": sorted(npc.tags),
        "accent_notes": npc.accent_notes,
    }


# ───────────────────────────────────────────────────────────────
# Логіка появ NPC (бродячі NPC по екранах)
# ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PlayerContext:
    uid: int
    level: int
    screen_key: str   # екран (city, areas, zastava...)
    hour: int         # година доби (0..23)


def _screen_allowed(rules: SpawnRules, screen_key: str) -> bool:
    if rules.areas_deny and screen_key in rules.areas_deny:
        return False
    if rules.areas_allow and screen_key not in rules.areas_allow:
        return False
    return True


def _hour_allowed(rules: SpawnRules, hour: int) -> bool:
    if not rules.time_windows:
        return True
    for lo, hi in rules.time_windows:
        if lo <= hi and lo <= hour < hi:
            return True
        if lo > hi and (hour >= lo or hour < hi):
            return True
    return False


def _lvl_allowed(rules: SpawnRules, lvl: int) -> bool:
    return rules.lvl_min <= lvl <= rules.lvl_max


def _weighted_pick(candidates: Sequence[NpcDef]) -> Optional[NpcDef]:
    if not candidates:
        return None
    weights = [max(1, n.weight) for n in candidates]
    return random.choices(list(candidates), weights=weights, k=1)[0]


def maybe_pick_npc(ctx: PlayerContext, *, force: bool = False) -> Optional[Dict[str, Any]]:
    """
    Викликаєш при оновленні екрана.
    Повертає NPC як dict або None.
    """
    now = time.time()
    _last_screen_for_uid[ctx.uid] = ctx.screen_key

    pool: List[NpcDef] = []
    for npc in all_npcs():
        r = npc.spawn
        if not _screen_allowed(r, ctx.screen_key):
            continue
        if not _hour_allowed(r, ctx.hour):
            continue
        if not _lvl_allowed(r, ctx.level):
            continue
        if not _cooldown_ok(ctx.uid, npc, now):
            continue
        pool.append(npc)

    if not pool:
        return None

    if not force:
        base = max(n.spawn.base_chance for n in pool)
        if random.random() >= base:
            return None

    pick = _weighted_pick(pool)
    if pick:
        _mark_seen(ctx.uid, pick, now)
        return make_encounter_data(pick)
    return None


# ───────────────────────────────────────────────────────────────
# Хелпери для квестів (слоти говору)
# ───────────────────────────────────────────────────────────────

def quest_intro(npc: NpcDef) -> str:
    return npc_say(npc, "greet", strict=True)


def quest_offer(npc: NpcDef) -> str:
    return npc_say(npc, "offer", strict=True)


def quest_accept(npc: NpcDef) -> str:
    return npc_say(npc, "accept", strict=True)


def quest_reject(npc: NpcDef) -> str:
    return npc_say(npc, "reject", strict=True)


def quest_complete(npc: NpcDef) -> str:
    return npc_say(npc, "complete", strict=True)


def quest_smalltalk(npc: NpcDef) -> str:
    return npc_say(npc, "smalltalk", strict=True)


def _join_lines(lines: Sequence[str]) -> str:
    return "\n".join(l for l in lines if l)


# ───────────────────────────────────────────────────────────────
# In-memory стан КВЕСТІВ (без БД, для мініапу)
# ───────────────────────────────────────────────────────────────

# який квест цього NPC зараз «активний» у гравця
_active_quest_for_npc: Dict[Tuple[int, str], str] = {}      # (uid, npc_key) -> quest_key
# поточний етап квесту
_quest_stage_for_player: Dict[Tuple[int, str], str] = {}    # (uid, quest_key) -> stage_id
# завершені квести
_completed_quests: Set[Tuple[int, str]] = set()             # (uid, quest_key)


def _pick_quest_for_npc(npc_key: str, uid: int) -> Optional[QuestDef]:
    """
    Обираємо перший квест NPC, який ще не завершений гравцем.
    Поки що — просто перший з списку.
    """
    quests = list(get_quests_for_npc(npc_key))
    if not quests:
        return None
    for q in quests:
        if (uid, q.quest_key) not in _completed_quests:
            return q
    return None


def _first_choice_stage(q: QuestDef, stage_id: str, predicate) -> Optional[QuestStage]:
    """
    Утиліта: взяти наступний етап по першій кнопці, що задовольняє predicate(label, next_id).
    """
    stage = q.stages.get(stage_id)
    if not stage:
        return None
    for label, next_id in stage.choices.items():
        if predicate(label, next_id):
            return q.stages.get(next_id)
    return None


# ───────────────────────────────────────────────────────────────
# ПУБЛІЧНИЙ API ДЛЯ FastAPI-роутера /api/npc/*
# ───────────────────────────────────────────────────────────────

async def get_npc(npc_key: str) -> Optional[Dict[str, Any]]:
    """
    Дає опис NPC + основні фрази для encounter.
    Використовується в routers/npc_router.py.
    """
    npc = get_npc_def(npc_key)
    if not npc:
        return None

    # беремо перший доступний квест (якщо є)
    primary_quest = _pick_quest_for_npc(npc_key, uid=0) or (
        list(get_quests_for_npc(npc_key))[0] if list(get_quests_for_npc(npc_key)) else None
    )

    # діалогові фрази — з npc_defs (діалект)
    greet = ""
    offer = ""
    accept_ok = ""
    decline_ok = ""
    extra = ""

    try:
        greet = quest_intro(npc)
    except Exception:
        greet = npc.speech.greet[0] if npc.speech.greet else ""

    try:
        offer = quest_offer(npc)
    except Exception:
        if primary_quest:
            offer = primary_quest.description
        else:
            offer = "Маю для тебе діло. Візьмешся?"

    try:
        accept_ok = quest_accept(npc)
    except Exception:
        accept_ok = "Домовились. Не підведи."

    try:
        decline_ok = quest_reject(npc)
    except Exception:
        decline_ok = "Як знаєш. Дорога завжди відкрита."

    try:
        extra = quest_smalltalk(npc)
    except Exception:
        extra = ""

    return {
        "key": npc.key,
        "name": npc.name,
        "region": npc.region,
        "accent_notes": npc.accent_notes,
        "tags": sorted(npc.tags),
        "lines": {
            "greet": greet,
            "offer": offer,
            "accept_ok": accept_ok,
            "decline_ok": decline_ok,
            "extra": extra,
        },
    }


async def can_interact(uid: int, npc_key: str) -> bool:
    """
    Чи можна зараз взаємодіяти з NPC:
    - є хоч один квест;
    - не всі квести завершені.
    Поки без перевірки рівня, предметів і т.п.
    """
    quests = list(get_quests_for_npc(npc_key))
    if not quests:
        return False
    for q in quests:
        if (uid, q.quest_key) not in _completed_quests:
            return True
    return False


async def start_encounter(uid: int, npc_key: str) -> None:
    """
    Старт encounter: фіксуємо, який квест цього NPC зараз «активний» для гравця
    й ставимо його на стартовий етап.
    """
    q = _pick_quest_for_npc(npc_key, uid)
    if not q:
        return
    _active_quest_for_npc[(uid, npc_key)] = q.quest_key
    _quest_stage_for_player[(uid, q.quest_key)] = q.start_id


async def accept_quest(uid: int, npc_key: str) -> str:
    """
    Гравець натиснув «Прийняти» у фронті.
    Тут ми:
      - гарантуємо, що є активний квест;
      - перескакуємо зі стартового етапу на основний (наприклад, collect);
      - повертаємо текст етапу (діалект + умови).
    Предмети й нагороди тут ще НЕ рухаємо — це рівень вище (інвентар/БД).
    """
    q = _pick_quest_for_npc(npc_key, uid)
    if not q:
        return "Поки що для тебе немає справ."

    _active_quest_for_npc[(uid, npc_key)] = q.quest_key
    cur_stage_id = _quest_stage_for_player.get((uid, q.quest_key), q.start_id)

    # шукаємо «позитивний» перехід зі старту (по кнопці з ✅ або просто перший)
    next_stage: Optional[QuestStage] = None
    stage = q.stages.get(cur_stage_id)

    if stage and stage.choices:
        # спочатку шукаємо кнопку з ✅ / «йду в ділі» / «беру»
        next_stage = _first_choice_stage(
            q,
            cur_stage_id,
            lambda label, _nid: (
                "✅" in label
                or "прий" in label.lower()
                or "йду" in label.lower()
                or "беру" in label.lower()
            ),
        )
        # якщо не знайшли — просто перший варіант
        if not next_stage:
            first_next_id = next(iter(stage.choices.values()))
            next_stage = q.stages.get(first_next_id)

    # якщо щось криво в структурі — лишаємось на старті
    if not next_stage:
        next_stage = stage or q.stages.get(q.start_id)

    _quest_stage_for_player[(uid, q.quest_key)] = next_stage.id

    lines = list(next_stage.text_lines)
    npc = get_npc_def(npc_key)
    if npc:
        try:
            lines.append(quest_accept(npc))
        except Exception:
            pass

    return _join_lines(lines)


async def decline_quest(uid: int, npc_key: str) -> str:
    """
    Гравець відмовився від квесту.
    Переходимо на «reject»-етап (якщо є) і вважаємо квест завершеним у стані «відмова».
    """
    q = _pick_quest_for_npc(npc_key, uid)
    if not q:
        return "Та й нема від чого відмовлятися."

    cur_stage_id = _quest_stage_for_player.get((uid, q.quest_key), q.start_id)
    stage = q.stages.get(cur_stage_id)

    # шукаємо переходи на відмову
    reject_stage: Optional[QuestStage] = None
    if stage and stage.choices:
        reject_stage = _first_choice_stage(
            q,
            cur_stage_id,
            lambda label, _nid: (
                "❌" in label
                or "пас" in label.lower()
                or "не зараз" in label.lower()
                or "не маю часу" in label.lower()
            ),
        )

    # якщо не знайшли — шукаємо явний етап "reject"
    if not reject_stage:
        reject_stage = q.stages.get("reject")

    # якщо й тут глухо — просто ставимо квест у completed без тексту
    if not reject_stage:
        _completed_quests.add((uid, q.quest_key))
        return "Ти відмовився від справи. NPC знизав плечима й розчинився в натовпі."

    _quest_stage_for_player[(uid, q.quest_key)] = reject_stage.id
    _completed_quests.add((uid, q.quest_key))

    lines = list(reject_stage.text_lines)
    npc = get_npc_def(npc_key)
    if npc:
        try:
            lines.append(quest_reject(npc))
        except Exception:
            pass

    return _join_lines(lines)


async def extra_line(uid: int, npc_key: str) -> Optional[str]:
    """
    Додаткова фраза/підказка від NPC. Просто повертаємо smalltalk/extra.
    """
    npc = get_npc_def(npc_key)
    if not npc:
        return None

    try:
        return quest_smalltalk(npc)
    except Exception:
        if npc.speech.smalltalk:
            return npc.speech.smalltalk[0]
        return "Тримайся стежки та не загуби голову."