"""L3 世界知识：把确定的事实（坐标、出口、作物、规则）压成几行喂给模型。

数据文件：bot/knowledge.json，可用 $FARM_DATA/knowledge.json 覆盖。
只放确定的事实；不确定的写 null + note，让模型知道"不知道"。
"""
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_CACHE = {"path": None, "mtime": 0, "data": {}}


def knowledge_path():
    own = os.path.join(os.environ.get("FARM_DATA") or "", "knowledge.json")
    if os.environ.get("FARM_DATA") and os.path.exists(own):
        return own
    return os.path.join(_HERE, "knowledge.json")


def load():
    path = knowledge_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    if _CACHE["path"] == path and _CACHE["mtime"] == mtime:
        return _CACHE["data"]
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f) or {}
    except Exception:
        data = {}
    _CACHE.update({"path": path, "mtime": mtime, "data": data})
    return data


def digest(max_lines=28):
    """压成简短文本行，直接塞进 prompt。"""
    data = load()
    if not data:
        return []
    out = []

    farm = data.get("farm") or {}
    if farm.get("farmhouse_door"):
        out.append("农舍门在 %s；田块 %s" % (
            tuple(farm["farmhouse_door"]),
            farm.get("plot_rect") and "x%s-%s,y%s-%s" % (
                farm["plot_rect"].get("x1"), farm["plot_rect"].get("x2"),
                farm["plot_rect"].get("y1"), farm["plot_rect"].get("y2")) or "未知"))

    for area_key, area_name in (("farm", "Farm"), ("town", "Town"), ("busstop", "BusStop")):
        for ex in (data.get(area_key) or {}).get("known_exits") or []:
            out.append("%s → %s 走 %s%s" % (
                area_name, ex.get("to"), tuple(ex.get("tile") or []),
                ("（%s）" % ex["note"]) if ex.get("note") else ""))

    shop = (data.get("town") or {}).get("pierre_shop") or {}
    if shop:
        out.append("皮埃尔种子店：%s" % (
            "坐标 %s" % (tuple(shop["tile"]),) if shop.get("tile") else
            "%s；%s" % (shop.get("location"), shop.get("note") or "坐标未知")))

    for crop in data.get("crops") or []:
        out.append("%s：%s %s 天成熟，种子 %s g，卖 %s g（%s）" % (
            crop.get("name"), crop.get("season"), crop.get("days"),
            crop.get("seed_price"), crop.get("sell"),
            crop.get("water_growth") or "每天浇水才长"))

    gift = data.get("starting_gift") or {}
    if gift:
        out.append("开局礼物：%s 里有 %s 个 %s（%s）" % (
            gift.get("location"), gift.get("count"), gift.get("item"),
            "只此一次" if gift.get("once") else "可重复"))

    craft = data.get("crafting") or {}
    if craft.get("examples"):
        out.append("可以制造：%s（%s）" % ("、".join(craft["examples"]), craft.get("note") or ""))

    for rule in data.get("rules") or []:
        out.append("规则：%s" % rule)

    return out[:max_lines]


def text(max_lines=28):
    return "\n".join(digest(max_lines))
