"""测试共用的快照与替身构造器（不连接游戏、不调用模型）。"""

from __future__ import annotations

from itertools import count

from stardew_agent.observation import from_snapshot

_SEQUENCE = count(1)


def tile(x: int, y: int) -> dict:
    return {"x": int(x), "y": int(y)}


def entity(reference: str, kind: str, name: str, x: int, y: int, **extra) -> dict:
    row = {"ref": reference, "kind": kind, "name": name, "tile": tile(x, y), "passable": True}
    row.update(extra)
    return row


def inventory(slot: int, name: str, count: int = 1, **extra) -> dict:
    row = {"slot": slot, "itemId": "(O)1", "name": name, "count": count, "category": -1}
    row.update(extra)
    return row


def snapshot(
    *,
    location: str = "Farm",
    x: int = 10,
    y: int = 10,
    entities: list | None = None,
    inventory_rows: list | None = None,
    menu: dict | None = None,
    save_id: str = "save-1",
    instance: str = "inst-1",
    generation: int = 1,
    day: int = 1,
    season: str = "spring",
    year: int = 1,
    game_time: int = 600,
    radius: int = 8,
    width: int = 40,
    height: int = 30,
    truncated: bool = False,
    mode: str = "game",
    energy: int = 200,
    selected_slot: int = 0,
    can_move: bool = True,
    money: int = 500,
) -> dict:
    entities = list(entities or [])
    scope = {
        "radius": radius,
        "center": tile(x, y),
        "truncated": truncated,
        "width": width,
        "height": height,
        "entities": entities,
    }
    return {
        "mode": mode,
        "kind": "snapshot",
        "snapshotId": "s%d" % next(_SEQUENCE),
        "instanceId": instance,
        "saveGeneration": generation,
        "saveId": save_id,
        "playerId": "player-1",
        "location": location,
        "date": {"season": season, "day": day, "year": year},
        "time": game_time,
        "player": {
            "position": tile(x, y),
            "energy": energy,
            "maxEnergy": 270,
            "selectedSlot": selected_slot,
            "canMove": can_move,
            "money": money,
            "maxItems": 12,
        },
        "inventory": list(inventory_rows or []),
        "trajectory": [],
        "observation": scope,
        "entities": entities,
        "menu": menu,
    }


def observe(**kwargs):
    return from_snapshot(snapshot(**kwargs))


def menu_row(option_refs: list[str], *, text: str = "确认", identity: str = "menu-1") -> dict:
    return {
        "type": "DialogueBox",
        "identity": identity,
        "text": text,
        "options": [{"ref": ref, "label": "选项"} for ref in option_refs],
    }
