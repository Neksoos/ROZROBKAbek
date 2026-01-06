# src/services/npc_roamer.py
from __future__ import annotations

"""
Бродячі NPC, що «ходять за гравцем» і інколи підмішують кнопку зустрічі
у будь-який екран без правок у кожному роутері.

Як увімкнути:
1) Поклади цей файл у src/services/npc_roamer.py
2) У main.py ПІСЛЯ імпорту ui підтяни модуль, щоб спрацював патч:
      from .services import npc_roamer  # noqa: F401
3) Підключи router у Dispatcher:
      from .services.npc_roamer import router as npc_router
      dp.include_router(npc_router)

Що робить:
- Обгортає ui.render_screen так, що перед відмальовкою екрану з певним шансом
  додає кнопку "✨ Зустріти {NPC}" (callback "npc:meet:<key>").
- Перевіряє правила SpawnRules (cooldown, area allow/deny, time windows).
- Тримає легку пам'ять по користувачу (cooldown).
- Має мінімальний router для обробки "npc:meet:*" і показу короткого оффера.

Налаштування:
- Порог випадковості та правила — у npc_defs.SpawnRules конкретних NPC.
- Мапінг "screen_key" -> "area" див. _area_of(screen_key): відсікаємо префікс.
"""

import random
import time
from typing import Optional, Tuple, List, Dict, Any, Callable

from aiogram import Router, F
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    Message,
)
from aiogram.enums import ParseMode

from . import ui as ui_mod
from .npc_defs import all_npcs, NpcDef, SpawnRules

# ────────────────────────────────────────────────────────────────────
# Стан: прості in-memory мапи (на процес)
# ────────────────────────────────────────────────────────────────────

# остання поява будь-якого NPC для користувача: {uid: ts}
_COOLDOWNS: Dict[int, float] = {}
# останній NPC, з яким гравець ініціював зустріч: {uid: npc_key}
_LAST_OFFER: Dict[int, str] = {}

# опційний провайдер рівня гравця: Callable[[tg_id], int]
_LEVEL_PROVIDER: Optional[Callable[[int], int]] = None

# які екрани НЕ прикрашаємо (системні чи небажані)
_DENY_SCREENS_PREFIX = {
    "npc:",            # власні екрани npc
    "battle",          # у бою не мигаємо
    "mail_view",       # приклад — не чіпати довгі читальні
}

# ────────────────────────────────────────────────────────────────────
# Патч рендера
# ────────────────────────────────────────────────────────────────────

_original_render_screen = ui_mod.render_screen  # тримаємо оригінал
router = Router(name="npc_roamer")


def _get_uid_and_screen(args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> Tuple[Optional[int], str]:
    """
    Витягає user_id/chat_id і screen_key з аргументів того, хто викликає render_screen().
    Логіка максимально терпима до різних варіантів виклику.
    """
    screen_key = kwargs.get("screen_key") or ""
    user_id = kwargs.get("user_id") or kwargs.get("chat_id")

    if user_id is not None:
        try:
            return int(user_id), str(screen_key)
        except Exception:
            return None, str(screen_key)

    # якщо перший арг — Message/CallbackQuery/Bot
    if args:
        obj = args[0]
        # aiogram.types.Message
        if isinstance(obj, Message) and getattr(obj, "chat", None):
            return int(obj.chat.id), str(screen_key)
        # aiogram.types.CallbackQuery
        if isinstance(obj, CallbackQuery) and obj.message and getattr(obj.message, "chat", None):
            return int(obj.message.chat.id), str(screen_key)

    return None, str(screen_key)


def _area_of(screen_key: str) -> str:
    """
    Нормалізуємо screen_key до «зони», щоб порівнювати з SpawnRules.areas_allow/deny.
    Правило просте: беремо префікс до першого двокрап'я/підкреслення.

    "city", "city_main" -> "city"
    "zastava_v2"        -> "zastava"
    "areas:list"        -> "areas"
    """
    if not screen_key:
        return ""
    for sep in (":", "_"):
        if sep in screen_key:
            return screen_key.split(sep, 1)[0]
    return screen_key


def _in_time_windows(now_h: int, windows: Optional[List[Tuple[int, int]]]) -> bool:
    if not windows:
        return True
    for start, end in windows:
        if start <= end:
            if start <= now_h < end:
                return True
        else:
            # перехід через північ: напр. (22, 3)
            if now_h >= start or now_h < end:
                return True
    return False


def _player_level(uid: int) -> int:
    if callable(_LEVEL_PROVIDER):
        try:
            return int(_LEVEL_PROVIDER(uid))
        except Exception:
            return 1
    return 1


def _can_spawn(npc: NpcDef, uid: int, area: str) -> bool:
    sr: SpawnRules = npc.spawn

    # обмеження по зоні
    if sr.areas_allow and area not in sr.areas_allow:
        return False
    if sr.areas_deny and area in sr.areas_deny:
        return False

    # рівень
    lvl = _player_level(uid)
    if not (sr.lvl_min <= lvl <= sr.lvl_max):
        return False

    # час
    now_h = time.localtime().tm_hour
    if not _in_time_windows(now_h, sr.time_windows):
        return False

    return True


def _pick_npc(uid: int, area: str) -> Optional[NpcDef]:
    """
    Обирає NPC згідно з area та шансом.
    1) фільтр по can_spawn
    2) один кидок випадку по max(base_chance)
    3) random.choices з вагами
    """
    pool = [n for n in all_npcs() if _can_spawn(n, uid, area)]
    if not pool:
        return None

    base_p = max(n.spawn.base_chance for n in pool)
    if random.random() > base_p:
        return None

    weights = [max(1, int(n.weight)) for n in pool]
    return random.choices(pool, weights=weights, k=1)[0]


def _append_button(kb: Optional[InlineKeyboardMarkup], text: str, data: str) -> InlineKeyboardMarkup:
    kb = kb or InlineKeyboardMarkup(inline_keyboard=[])
    rows = list(kb.inline_keyboard or [])
    # не дублюємо кнопку, якщо вже є
    for row in rows:
        for btn in row:
            if getattr(btn, "callback_data", "") == data:
                return kb
    rows.append([InlineKeyboardButton(text=text, callback_data=data)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_screen_patched(*args, **kwargs):
    """
    Обгортка render_screen: перед викликом оригіналу може додати кнопку зустрічі.
    """
    uid, screen_key = _get_uid_and_screen(args, kwargs)

    if uid is not None and screen_key:
        area = _area_of(screen_key)
        deny = any(screen_key.startswith(p) for p in _DENY_SCREENS_PREFIX)

        if not deny:
            chosen: Optional[NpcDef] = _pick_npc(uid, area)
            if chosen:
                now = time.time()
                last = _COOLDOWNS.get(uid, 0.0)
                cd = max(60, int(chosen.spawn.cooldown_sec))

                # якщо ще не відсиділи cooldown — не показуємо
                if now - last >= cd:
                    _COOLDOWNS[uid] = now
                    btn_text = f"✨ Зустріти {chosen.name}"
                    cb_data = f"npc:meet:{chosen.key}"
                    kwargs["reply_markup"] = _append_button(
                        kwargs.get("reply_markup") or kwargs.get("keyboard"),
                        btn_text,
                        cb_data,
                    )

    # викликаємо оригінальний рендер
    return await _original_render_screen(*args, **kwargs)


# ────────────────────────────────────────────────────────────────────
# Публічні утиліти
# ────────────────────────────────────────────────────────────────────

def set_level_provider(fn: Callable[[int], int]) -> None:
    """Опційно: підкинь функцію, що повертає рівень гравця по tg_id."""
    global _LEVEL_PROVIDER
    _LEVEL_PROVIDER = fn


# ────────────────────────────────────────────────────────────────────
# Router: обробка зустрічі
# ────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("npc:meet:"))
async def npc_meet(c: CallbackQuery):
    await c.answer()
    try:
        npc_key = c.data.split(":", 2)[2]
    except Exception:
        return

    npc = next((n for n in all_npcs() if n.key == npc_key), None)
    if not npc:
        return

    _LAST_OFFER[c.from_user.id] = npc.key

    greet = npc.speech.greet[0] if npc.speech.greet else f"{npc.name} киває тобі."
    offer = npc.speech.offer[0] if npc.speech.offer else "Маю для тебе діло, якшо не страшно."

    text = (
        f"🧭 <b>{npc.name}</b> · {npc.region}\n"
        f"<i>{npc.accent_notes}</i>\n\n"
        f"— {greet}\n\n"
        f"— {offer}"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗣 Поговорити", callback_data=f"npc:talk:{npc.key}")],
            [InlineKeyboardButton(text="✖ Облишити", callback_data="ui:back")],
        ]
    )

    await ui_mod.render_screen(
        bot=c,
        screen_key=f"npc:encounter:{npc.key}",
        text=text,
        reply_markup=kb,
        disable_web_page_preview=True,
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data.startswith("npc:talk:"))
async def npc_talk(c: CallbackQuery):
    await c.answer()
    try:
        npc_key = c.data.split(":", 2)[2]
    except Exception:
        return

    npc = next((n for n in all_npcs() if n.key == npc_key), None)
    if not npc:
        return

    # Поки що — проста «болталка». Далі тут підв’яжемо QuestStage.
    small = npc.speech.smalltalk[0] if npc.speech.smalltalk else "Ну... говорімо."
    accept = npc.speech.accept[0] if npc.speech.accept else "Домовились."

    text = (
        f"🧭 <b>{npc.name}</b>\n\n"
        f"— {small}\n"
        f"— {accept}"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◀ Назад", callback_data="ui:back")],
        ]
    )

    await ui_mod.render_screen(
        bot=c,
        screen_key=f"npc:talk:{npc.key}",
        text=text,
        reply_markup=kb,
        disable_web_page_preview=True,
        parse_mode=ParseMode.HTML,
    )


# ────────────────────────────────────────────────────────────────────
# Ініціалізація (викликається при імпорті модуля)
# ────────────────────────────────────────────────────────────────────

def _init_patch_once() -> None:
    # Якщо вже патчений — не патчимо двічі
    if getattr(ui_mod, "_npc_roamer_patched", False):
        return
    ui_mod._npc_roamer_patched = True
    ui_mod.render_screen = _render_screen_patched  # type: ignore[assignment]


_init_patch_once()