# routers/gathering_story.py
from __future__ import annotations

import json
import time
import secrets
import random
from typing import Any, Dict, List, Literal, Optional, Tuple
from urllib.parse import parse_qs

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from routers.redis_manager import get_redis
from services.gathering_loot import roll_gathering_loot

# ✅ кладемо здобич як ITEMS у інвентар (player_inventory)
from routers.inventory import give_item_to_player

from data.world_data import MOBS

router = APIRouter(prefix="/api/gathering/story", tags=["gathering"])

RiskMode = Literal["careful", "normal", "risky"]

# ✅ ТВОЯ РЕАЛЬНІСТЬ: камінь = ks (items.category = "ks")
# приймаємо як "правильні" типи, так і аліаси
SourceType = Literal["herb", "ore", "ks", "herbalist", "miner", "stonemason", "stone"]
StoryOptionKind = Literal["continue", "fight", "escape", "finish"]


# ───────────────────────────────────────
# headers -> tg_id (як у professions)
# ───────────────────────────────────────
def _tg_id_from_headers(x_init_data: str | None, x_tg_id: str | None) -> int:
    if x_tg_id and x_tg_id.strip():
        try:
            return int(x_tg_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid X-Tg-Id")

    if not x_init_data or not x_init_data.strip():
        raise HTTPException(status_code=401, detail="Missing X-Init-Data")

    try:
        qs = parse_qs(x_init_data, keep_blank_values=True)
        user_raw = (qs.get("user") or [None])[0]
        if not user_raw:
            raise ValueError("user missing")

        user = json.loads(user_raw)
        return int(user.get("id"))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid X-Init-Data")


def _normalize_source_type(v: str) -> Literal["herb", "ore", "ks"]:
    """
    Нормалізуємо до: herb | ore | ks
    Бо в items.category для каменяра = ks
    """
    v = (v or "").strip().lower()

    if v in ("herb", "herbalist"):
        return "herb"
    if v in ("ore", "miner"):
        return "ore"

    # ✅ ВСЕ КАМІННЕ В ks
    if v in ("ks", "stone", "stonemason", "камінь", "камені", "каменяр"):
        return "ks"

    raise HTTPException(status_code=400, detail="INVALID_SOURCE_TYPE")


class StoryStartBody(BaseModel):
    tg_id: Optional[int] = None
    area_key: str
    risk: RiskMode
    source_type: SourceType


class StoryChoiceBody(BaseModel):
    tg_id: Optional[int] = None
    choice_id: str


class StoryOptionDTO(BaseModel):
    id: str
    kind: StoryOptionKind
    label: str


class DropDTO(BaseModel):
    material_id: int
    code: str
    name: str
    qty: int
    rarity: Optional[str] = None


class StoryStepDTO(BaseModel):
    ok: bool = True
    area_key: str
    risk: RiskMode
    step: int
    text: str
    options: List[StoryOptionDTO] = Field(default_factory=list)
    mob_name: Optional[str] = None
    combat_result: Optional[str] = None
    finished: bool = False
    drops: Optional[List[DropDTO]] = None


def _redis_key(tg_id: int) -> str:
    return f"gather_story:{tg_id}"


def _now_ts() -> int:
    return int(time.time())


def _rand_id(prefix: str = "c") -> str:
    return f"{prefix}_{secrets.token_urlsafe(8)}"


def _risk_params(risk: RiskMode) -> Dict[str, Any]:
    if risk == "careful":
        return {"ambush_chance": 0.15, "win_chance": 0.75, "loot_mult": 0.85, "loot_risk": "low"}
    if risk == "risky":
        return {"ambush_chance": 0.45, "win_chance": 0.55, "loot_mult": 1.25, "loot_risk": "high"}
    return {"ambush_chance": 0.28, "win_chance": 0.65, "loot_mult": 1.00, "loot_risk": "medium"}


def _make_options(items: List[Tuple[StoryOptionKind, str, str]]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for kind, label, _action in items:
        out.append({"id": _rand_id("opt"), "kind": kind, "label": label})
    return out


async def _load_state(tg_id: int) -> Optional[Dict[str, Any]]:
    r = await get_redis()
    raw = await r.get(_redis_key(tg_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        await r.delete(_redis_key(tg_id))
        return None


async def _save_state(tg_id: int, state: Dict[str, Any], ttl_seconds: int = 60 * 60) -> None:
    r = await get_redis()
    await r.set(_redis_key(tg_id), json.dumps(state, ensure_ascii=False), ex=ttl_seconds)


async def _clear_state(tg_id: int) -> None:
    r = await get_redis()
    await r.delete(_redis_key(tg_id))


def _roll(p: float) -> bool:
    return secrets.randbelow(10_000) < int(p * 10_000)


# ✅ моб тільки з локації
def _pick_mob_for_area(area_key: str) -> str:
    for key, mob_list in MOBS:
        if key == area_key and mob_list:
            mob = random.choice(mob_list)
            return str(mob[1])  # name
    return "Невідомий ворог"


def _scale_drops(drops: List[Dict[str, Any]], mult: float) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for d in drops or []:
        qty = int(d.get("qty") or d.get("amount") or 1)
        qty2 = max(1, int(round(qty * mult)))

        code = str(d.get("code") or d.get("item_code") or "").strip()
        if not code:
            continue

        out.append(
            {
                "material_id": int(d.get("material_id") or d.get("id") or 0),
                "code": code,
                "name": str(d.get("name") or code),
                "rarity": d.get("rarity"),
                "qty": qty2,
            }
        )
    return out


def _to_drop_dtos(raw: Any) -> List[DropDTO]:
    if not raw:
        return []
    if isinstance(raw, list):
        out: List[DropDTO] = []
        for d in raw:
            if isinstance(d, DropDTO):
                out.append(d)
                continue
            if isinstance(d, dict):
                try:
                    out.append(DropDTO(**d))
                except Exception:
                    continue
        return out
    return []


def _step_text(area_key: str, source_type: str, step: int) -> str:
    if step == 1:
        return (
            f"Ти вирушаєш у місцевість «{area_key}» на пошук ресурсів.\n"
            f"Дорога здається спокійною, але ці землі не люблять чужинців…"
        )
    if step == 2:
        return (
            "Ти знаходиш перспективну ділянку і починаєш пошук.\n"
            "Раптом чуєш шарудіння неподалік — щось стежить за тобою."
        )
    return (
        "Ти майже завершуєш похід. Залишилось зробити останній ривок і забрати здобич.\n"
        "Чи ризикнеш затриматись ще на мить?"
    )


def _options_for_step(step: int, ambush: bool) -> List[Tuple[StoryOptionKind, str, str]]:
    if step == 1:
        return [
            ("continue", "Йти далі стежкою", "go_next"),
            ("escape", "Повернутись назад, не ризикувати", "finish_early"),
        ]
    if step == 2:
        if ambush:
            return [
                ("fight", "Вийти з тіні й дати бій", "fight"),
                ("escape", "Спробувати втекти в хащі", "escape"),
            ]
        return [
            ("continue", "Спокійно продовжити пошук", "go_next"),
            ("finish", "Забрати знайдене й завершити похід", "finish"),
        ]
    if ambush:
        return [
            ("fight", "Останній бій за здобич", "fight_finish"),
            ("escape", "Відступити й зберегти сили", "finish_early"),
        ]
    return [("finish", "Завершити похід і повернутись", "finish")]


_RARITY_TIER_MAP = {
    "звичайний": "common",
    "добротний": "uncommon",
    "рідкісний": "rare",
    "вибраний": "epic",
    "обереговий": "legendary",
    "божественний": "mythic",
    "common": "common",
    "uncommon": "uncommon",
    "rare": "rare",
    "epic": "epic",
    "legendary": "legendary",
    "mythic": "mythic",
}


def _category_for_drop(source_type: str, rarity: Optional[str]) -> str:
    """
    ✅ ВАЖЛИВО ПІД ТВОЮ БД:
    - items.category для каменя = "ks" (БЕЗ tier)
    - player_inventory теж має отримувати category="ks"
    """
    base = (source_type or "").strip().lower()

    # ✅ камінь завжди "ks" (ніяких ks_common / ks_rare)
    if base in ("ks", "stone", "stonemason"):
        return "ks"

    # herb/ore як було (tier-логіка)
    r = (rarity or "").strip().lower()
    tier = _RARITY_TIER_MAP.get(r, "common")
    if base not in ("herb", "ore"):
        base = "herb"
    return f"{base}_{tier}"


async def _grant_drops_as_items(tg_id: int, source_type: str, drops_scaled: List[Dict[str, Any]]) -> None:
    if tg_id <= 0 or not drops_scaled:
        return

    for d in drops_scaled:
        code = str(d.get("code") or "").strip()
        qty = int(d.get("qty") or 0)
        if not code or qty <= 0:
            continue

        name = str(d.get("name") or code)
        rarity = d.get("rarity")
        category = _category_for_drop(source_type, rarity)

        await give_item_to_player(
            tg_id,
            item_code=code,
            name=name,
            category=category,  # ✅ "ks" для каменю
            emoji=None,
            rarity=rarity,
            description=None,
            stats=None,
            amount=qty,
            slot=None,
        )


@router.post("/start", response_model=StoryStepDTO)
async def start_story(
    payload: StoryStartBody,
    x_init_data: str | None = Header(default=None, alias="X-Init-Data"),
    x_tg_id: str | None = Header(default=None, alias="X-Tg-Id"),
) -> StoryStepDTO:
    tg_id = payload.tg_id or _tg_id_from_headers(x_init_data, x_tg_id)
    source_type_norm = _normalize_source_type(payload.source_type)  # ✅ herb/ore/ks

    await _clear_state(tg_id)

    params = _risk_params(payload.risk)
    step = 1
    ambush = _roll(params["ambush_chance"])

    opts = _options_for_step(step, ambush)
    option_dtos = _make_options([(k, lbl, act) for (k, lbl, act) in opts])
    mapping = {option_dtos[i]["id"]: opts[i][2] for i in range(len(opts))}

    state = {
        "tg_id": tg_id,
        "area_key": payload.area_key,
        "risk": payload.risk,
        "source_type": source_type_norm,  # ✅ herb/ore/ks
        "step": step,
        "ambush": ambush,
        "mob_name": _pick_mob_for_area(payload.area_key) if ambush else None,
        "choice_map": mapping,
        "finished": False,
        "created_at": _now_ts(),
    }
    await _save_state(tg_id, state)

    return StoryStepDTO(
        ok=True,
        area_key=payload.area_key,
        risk=payload.risk,
        step=step,
        text=_step_text(payload.area_key, source_type_norm, step),
        options=[StoryOptionDTO(**o) for o in option_dtos],
        mob_name=state["mob_name"],
        combat_result=None,
        finished=False,
        drops=None,
    )


@router.post("/choice", response_model=StoryStepDTO)
async def choose(
    payload: StoryChoiceBody,
    x_init_data: str | None = Header(default=None, alias="X-Init-Data"),
    x_tg_id: str | None = Header(default=None, alias="X-Tg-Id"),
) -> StoryStepDTO:
    tg_id = payload.tg_id or _tg_id_from_headers(x_init_data, x_tg_id)

    state = await _load_state(tg_id)
    if not state:
        raise HTTPException(status_code=404, detail="NO_ACTIVE_STORY")

    if state.get("finished"):
        drops_dto = _to_drop_dtos(state.get("drops"))
        await _clear_state(tg_id)
        return StoryStepDTO(
            ok=True,
            area_key=state["area_key"],
            risk=state["risk"],
            step=int(state.get("step", 1)),
            text="Поход уже завершено.",
            options=[],
            mob_name=None,
            combat_result=None,
            finished=True,
            drops=drops_dto,
        )

    choice_map: Dict[str, str] = state.get("choice_map") or {}
    action = choice_map.get(payload.choice_id)
    if not action:
        raise HTTPException(status_code=400, detail="INVALID_CHOICE")

    risk: RiskMode = state["risk"]
    params = _risk_params(risk)

    area_key: str = state["area_key"]
    source_type: str = state["source_type"]  # ✅ herb/ore/ks
    step: int = int(state.get("step", 1))
    combat_result: Optional[str] = None

    if action in ("finish_early", "escape"):
        drops_raw = await roll_gathering_loot(
            tg_id=tg_id,
            area_key=area_key,
            source_type=source_type,
            risk=params["loot_risk"],
        )
        drops_scaled = _scale_drops([d.as_dict() for d in drops_raw], mult=params["loot_mult"] * 0.65)

        await _grant_drops_as_items(tg_id, source_type, drops_scaled)
        await _clear_state(tg_id)

        return StoryStepDTO(
            ok=True,
            area_key=area_key,
            risk=risk,
            step=step,
            text="Ти вирішуєш не випробовувати долю й повертаєшся з тим, що встиг здобути.",
            options=[],
            mob_name=None,
            combat_result=None,
            finished=True,
            drops=[DropDTO(**d) for d in drops_scaled],
        )

    if action in ("fight", "fight_finish"):
        mob = state.get("mob_name") or _pick_mob_for_area(area_key)
        win = _roll(params["win_chance"])
        combat_result = "win" if win else "lose"

        drops_raw = await roll_gathering_loot(
            tg_id=tg_id,
            area_key=area_key,
            source_type=source_type,
            risk=params["loot_risk"],
        )

        if not win:
            drops_scaled = _scale_drops([d.as_dict() for d in drops_raw], mult=params["loot_mult"] * 0.50)
            await _grant_drops_as_items(tg_id, source_type, drops_scaled)
            await _clear_state(tg_id)

            return StoryStepDTO(
                ok=True,
                area_key=area_key,
                risk=risk,
                step=step,
                text=f"Сутичка з ворогом «{mob}» виснажує тебе. Ти відступаєш, але щось таки вдається винести.",
                options=[],
                mob_name=mob,
                combat_result=combat_result,
                finished=True,
                drops=[DropDTO(**d) for d in drops_scaled],
            )

        if action == "fight_finish":
            drops_scaled = _scale_drops([d.as_dict() for d in drops_raw], mult=params["loot_mult"] * 1.10)
            await _grant_drops_as_items(tg_id, source_type, drops_scaled)
            await _clear_state(tg_id)

            return StoryStepDTO(
                ok=True,
                area_key=area_key,
                risk=risk,
                step=step,
                text=f"Ти перемагаєш «{mob}» і забираєш свою здобич. Поход завершено!",
                options=[],
                mob_name=mob,
                combat_result="win",
                finished=True,
                drops=[DropDTO(**d) for d in drops_scaled],
            )

        step = min(step + 1, 3)

    if action == "go_next":
        step = min(step + 1, 3)

    if action == "finish":
        drops_raw = await roll_gathering_loot(
            tg_id=tg_id,
            area_key=area_key,
            source_type=source_type,
            risk=params["loot_risk"],
        )
        drops_scaled = _scale_drops([d.as_dict() for d in drops_raw], mult=params["loot_mult"])

        await _grant_drops_as_items(tg_id, source_type, drops_scaled)
        await _clear_state(tg_id)

        return StoryStepDTO(
            ok=True,
            area_key=area_key,
            risk=risk,
            step=step,
            text="Ти завершуєш похід і повертаєшся з трофеями.",
            options=[],
            mob_name=None,
            combat_result=combat_result,
            finished=True,
            drops=[DropDTO(**d) for d in drops_scaled],
        )

    ambush = _roll(params["ambush_chance"]) if step >= 2 else bool(state.get("ambush", False))
    mob_name = _pick_mob_for_area(area_key) if ambush else None

    opts = _options_for_step(step, ambush)
    option_dtos = _make_options([(k, lbl, act) for (k, lbl, act) in opts])
    mapping = {option_dtos[i]["id"]: opts[i][2] for i in range(len(opts))}

    state["step"] = step
    state["ambush"] = ambush
    state["mob_name"] = mob_name
    state["choice_map"] = mapping
    state["finished"] = False
    await _save_state(tg_id, state)

    return StoryStepDTO(
        ok=True,
        area_key=area_key,
        risk=risk,
        step=step,
        text=_step_text(area_key, source_type, step),
        options=[StoryOptionDTO(**o) for o in option_dtos],
        mob_name=mob_name,
        combat_result=combat_result,
        finished=False,
        drops=None,
    )