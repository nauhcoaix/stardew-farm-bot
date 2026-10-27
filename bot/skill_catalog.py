"""模型可选择的能力和参数。可用性描述前提，不替模型排工作优先级。"""
from memory_store import target_id


DEFINITIONS = {
    "observe": ("观察当前范围，不移动或改变游戏", {"radius": "1..16"}),
    "memory": ("查询统一记忆或记录带证据的假设；不会把模型笔记标为游戏事实", {"op": "recall|remember", "query": "检索文字", "kind": "事件类型，可选", "location": "地图，可选", "text": "笔记", "evidence": "证据ID列表", "limit": "1..30"}),
    "explore": ("选择探索目的地、交互对象或试验；执行一次有界行动并记录发现", {"mode": "path|interaction|capability", "candidate": "探索候选ID", "target": "现场目标ID", "x": "已知坐标，可选", "y": "已知坐标，可选", "skill": "use|interact，功能试验时", "slot": "工具槽，可选", "question": "要验证的假设"}),
    "move": ("走向模型选的坐标/目标；按已观察地图和记忆规划，每段最多3格", {"x": "已知坐标", "y": "已知坐标", "target": "目标ID，和坐标二选一", "location": "目标所属地图，可选"}),
    "travel": ("去记忆中已发现的区域，使用已观察到的连接；无预设世界路由", {"to": "已知地图名称"}),
    "interact": ("靠近后交互对象/NPC/门/出口；不猜菜单选项", {"target": "现场目标ID"}),
    "select": ("选择背包槽位", {"slot": "现场存在的槽位"}),
    "use": ("向目标使用已选工具/物品；游戏决定是否支持和实际效果", {"target": "现场目标ID", "slot": "可选工具槽"}),
    "menu": ("选择当前菜单中实际提供的选项", {"target": "当前菜单选项ID"}),
    "menu_click": ("点击observe实际发现的菜单控件，不接受猜测屏幕坐标", {"target": "当前控件ID"}),
    "menu_close": ("明确关闭当前菜单，是否关闭由模型选择", {}),
    "refill": ("向已观察水源使用水壶补水；不自动去固定地点找水", {"target": "水源ID，可选"}),
    "till": ("向已观察的空置可耕地使用锄头", {"target": "可耕地ID"}),
    "plant": ("在已翻好的空地播种；由模型选择是否种植", {"target": "空耕地ID", "slot": "种子槽，可选"}),
    "harvest": ("收获成熟作物；不会自动补种或卖掉", {"limit": "1..24，默认3"}),
    "water": ("浇已观察的缺水地块；水不够时反馈，不自动寻找固定水源", {"limit": "1..24，默认3"}),
    "till_plant": ("明确委托一段开垦播种；每段最多3格，不固定农田坐标", {"limit": "1..24，默认3"}),
    "clear": ("清理模型委托的障碍，会改变地形并消耗资源", {"limit": "1..24，默认3", "min_energy": "15..270", "target": "障碍ID，可选"}),
    "chop": ("委托最多3次砍树动作，保留游戏血量验证", {"limit": "1..3"}),
    "buy_seeds": ("在已经打开的商店中购买指定商品，不自动去固定商店", {"index": "商店实际商品index", "item": "期望商品名称，用于复核", "qty": "1..20"}),
    "sell": ("在农场把选定物品交给发货箱；不会自动卖全部背包", {"slot": "明确选择的物品槽"}),
    "store": ("把指定物品存进已观察的箱子", {"target": "箱子ID", "slot": "物品槽"}),
    "sleep": ("与已观察的床交互，后续睡眠确认由menu选择", {"target": "床ID，可选"}),
    "fish": ("当前位置钓一竿，需独立钓鱼Mod、鱼竿和岸边；不会替模型选择路线", {"max_seconds": "10..120，默认60"}),
    "wait": ("短暂等待动画或现场变化，不执行其他工作", {"seconds": "0.1..3"}),
}
ACTIONS = set(DEFINITIONS)


def integer(value, low, high):
    if isinstance(value, bool):
        raise ValueError("布尔值不是坐标或数量")
    number = int(value)
    if number != float(value) or not low <= number <= high:
        raise ValueError("参数超出范围")
    return number


def clean_steps(steps):
    if not isinstance(steps, list):
        return []
    out = []
    text_fields = {"target", "candidate", "location", "to", "query", "kind", "text", "question", "item"}
    ranges = {"x": (0, 1000), "y": (0, 1000), "slot": (0, 35), "index": (0, 1000),
              "limit": (1, 30), "qty": (1, 20), "min_energy": (15, 270), "radius": (1, 16), "max_seconds": (10, 120)}
    for step in steps[:6]:
        if not isinstance(step, dict) or not isinstance(step.get("action"), str) or step["action"] not in ACTIONS:
            continue
        name = step["action"]
        raw = step.get("arg") or {}
        if not isinstance(raw, dict) or set(raw) - set(DEFINITIONS[name][1]):
            continue
        arg = {}
        try:
            for key, value in raw.items():
                if key in text_fields:
                    if not isinstance(value, str):
                        raise ValueError("需要文字参数")
                    arg[key] = value[:2000 if key == "text" else 300]
                elif key in ranges:
                    bounds = ranges[key]
                    if key == "limit":
                        bounds = (1, 30 if name == "memory" else 3 if name == "chop" else 24)
                    arg[key] = integer(value, *bounds)
                elif key in ("mode", "op", "skill"):
                    allowed = {"mode": {"path", "interaction", "capability"}, "op": {"recall", "remember"}, "skill": {"use", "interact"}}
                    if not isinstance(value, str) or value not in allowed[key]:
                        raise ValueError("未知模式")
                    arg[key] = value
                elif key == "evidence":
                    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
                        raise ValueError("证据必须是ID列表")
                    arg[key] = value[:12]
                elif key == "seconds":
                    arg[key] = max(0.1, min(3.0, float(value)))
            if name == "memory" and arg.get("op", "recall") == "remember" and not arg.get("text"):
                continue
            out.append({"action": name, "arg": arg, "reason": str(step.get("reason") or "模型选择")[:300], "source": "model"})
        except (ValueError, TypeError, OverflowError):
            continue
    return out


def available(name, snap, arg=None):
    if name not in DEFINITIONS:
        return False, "技能不在能力目录中"
    arg = arg or {}
    es, inv, p = snap.get("entities") or [], snap.get("inventory") or [], snap.get("player") or {}
    menu = snap.get("menu") or {}
    tool = lambda t: any(i.get("tool") == t for i in inv)
    target = next((e for e in es if target_id(snap.get("location"), e) == arg.get("target")), None)
    if name in ("memory", "observe", "wait"):
        return True, ""
    if name == "menu":
        return bool(menu.get("options")), "需要当前菜单提供的可选项"
    if name in ("menu_click", "menu_close"):
        return bool(menu), "需要先打开菜单，控件由observe发现"
    if menu:
        if name == "buy_seeds" and menu.get("type") == "ShopMenu":
            return True, ""
        return False, "菜单未处理，先观察并选择menu；不会自动点击"
    if not p.get("canMove", True):
        return False, "人物正在动画、过场或使用工具"
    if name in ("use", "till", "plant", "water", "clear", "chop", "till_plant") and float(p.get("energy") or 0) < 15:
        return False, "体力不足以继续耗体力的技能"
    if name == "water":
        return any(i.get("tool") == "WateringCan" and (i.get("water") or 0) > 0 for i in inv) and any(
            e.get("kind") == "soil" and not e.get("watered") and (e.get("crop") or e.get("tilled")) for e in es), "需要有水的水壶和缺水地块"
    if name == "harvest":
        return any(e.get("crop") and (e.get("harvestable") or e.get("ready")) for e in es), "没有已观察到的成熟作物"
    if name in ("plant", "till_plant"):
        seeds = any(i.get("category") == -74 or "种子" in (i.get("name") or "") or "Seeds" in (i.get("name") or "") for i in inv)
        return seeds and any((e.get("tilled") and not e.get("crop")) or (name == "till_plant" and e.get("diggable") and tool("Hoe")) for e in es), "需要种子和可播种土地"
    if name == "till":
        return tool("Hoe") and any(e.get("diggable") and not e.get("tilled") for e in es), "需要锄头与空置可耕地"
    if name == "refill":
        return tool("WateringCan") and any(e.get("refillable") for e in es), "需要水壶和已观察水源"
    if name == "chop":
        return tool("Axe") and any(e.get("name") in ("Tree", "树") for e in es), "需要斧头和已观察树木"
    if name == "clear":
        from farmer import CLEAR_TOOLS, tool_slot
        return any(tool_slot(snap, CLEAR_TOOLS[e["name"]]) for e in es if e.get("name") in CLEAR_TOOLS), "需要已观察障碍与对应工具"
    if name == "select":
        return bool(inv), "背包没有可选物品"
    if name == "sleep":
        return any(e.get("kind") == "bed" for e in es), "先找到床；可以探索门和室内"
    if name == "fish":
        return tool("FishingRod"), "需要鱼竿、当前岸边和已加载独立钓鱼Mod"
    if name == "buy_seeds":
        return False, "先打开商店并观察商品index；不会预设路线"
    if name == "sell":
        return snap.get("location") == "Farm", "当前桥的发货接口仅在农场提供"
    if arg.get("target") and not target and name != "move":
        return False, "目标已不在当前观察中，需重新观察或走近"
    return True, ""


def catalog(snap):
    result = []
    for name, (description, args) in DEFINITIONS.items():
        ready, reason = available(name, snap)
        result.append({"name": name, "description": description, "args": args,
                       "available": ready, "unavailable_reason": reason if not ready else ""})
    return result
