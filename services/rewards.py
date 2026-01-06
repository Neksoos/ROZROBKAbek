from __future__ import annotations

from typing import List, Dict, Any
from loguru import logger

# Використовуємо публічну функцію з роутера інвентаря
from routers.inventory import give_item_to_player  # type: ignore


async def distribute_drops(tg_id: int, drops: List[Dict[str, Any]]) -> List[str]:
    """
    Видати список предметів гравцеві та сформувати повідомлення для UI.

    Підтримує кілька варіантів ключів: item_code/code/itemCode, amount/qty,
    description/descr, stats/stat тощо. Якщо code відсутній, генеруємо його з name.
    Для предметів зі слотом (екіпірування) використовується префікс «Екіп»,
    для інших — «Мусор».
    """
    messages: List[str] = []

    for d in drops or []:
        try:
            item_code = (
                d.get("item_code")
                or d.get("code")
                or d.get("itemCode")
            )
            if not item_code:
                name_for_code = d.get("name") or "gather_item"
                item_code = name_for_code.lower().replace(" ", "_")

            name = d.get("name", item_code)
            amount = int(d.get("amount", d.get("qty", 1)))
            category = d.get("category")
            emoji = d.get("emoji")
            rarity = d.get("rarity")
            description = d.get("description") or d.get("descr")
            stats = d.get("stats") or d.get("stat") or {}
            slot = d.get("slot")

            await give_item_to_player(
                tg_id=tg_id,
                item_code=item_code,
                name=name,
                category=category,
                emoji=emoji,
                rarity=rarity,
                description=description,
                stats=stats,
                amount=amount,
                slot=slot,
            )

            prefix = "Екіп" if slot is not None else "Мусор"
            if amount > 1:
                messages.append(f"{prefix}: {name} ×{amount}")
            else:
                messages.append(f"{prefix}: {name}")

        except Exception as e:
            # логування і безпечне продовження, щоб інші предмети не втрачалися
            logger.exception(
                f"rewards: failed to give item to tg_id={tg_id}, drop={d}: {e}"
            )
            messages.append(f"⚠ Не вдалося видати предмет: {d.get('name', '?')}")
            continue

    return messages
