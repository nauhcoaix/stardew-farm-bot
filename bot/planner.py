"""让模型给农场出计划（每天一次 + 事件触发）。

密钥与模型设置只认一个地方：bot/planner-config.json（.gitignore 已忽略）。
也可以用环境变量 FARM_CFG 指向另一份同格式的配置文件（优先于上面的默认路径）。

planner-config.json：
{
  "api_key": "sk-...",
  "api_base": "https://api.deepseek.com/chat/completions",
  "model": "deepseek-chat"
}
"""
import json
import os
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_NAME = "planner-config.json"
DEFAULT_BASE = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"
# example 里的占位符：万一有人直接把 example 当配置用，别拿它去请求
PLACEHOLDERS = {"在这里填你的 key", "your-key", "your-api-key", "sk-xxx"}

CANDIDATES = [
    os.environ.get("FARM_CFG") or "",
    os.path.join(_HERE, CONFIG_NAME),
]

_announced_ok = False
_announced_missing = False

SYSTEM = """你是星露谷物语的农场助手，替玩家管理一个标准农场。

你能做的事（执行层已经实现）：
- harvest 收成熟作物
- clear 清障碍（杂草/嫩枝/石头，用对应工具），体力低于阈值会停
- till_plant 用现有种子翻地播种
- water 给缺水的作物浇水
- chop 砍树攒木材
- buy_seeds 去镇上 Pierre 买种子（9:00-21:00，走路来回约 10 分钟游戏时间）
- sell 把作物丢发货箱（第二天结算）
- stash 杂物进箱子
- sleep 回屋睡觉（推进到第二天）

你现在要给出「今天怎么安排」。只输出一个 JSON 对象，不要解释，不要代码块：

{
  "crop": "要种的作物种子名，比如 防风草种子 / 土豆种子 / 花椰菜种子",
  "buy_qty": 整数，这次买多少种子（0 表示不买）,
  "buy_when_below": 整数，种子低于多少就去补货,
  "clear_limit": 整数，每轮最多清几个障碍,
  "clear_min_energy": 整数，体力低于它就停止清障,
  "chop": true/false，今天要不要砍树攒木材,
  "sleep_at": 整数，比如 2100 表示 21:00 睡觉,
  "reason": "一句话说明今天的重点"
}

判断原则：
- 钱少就种回本快的（防风草 20G 4天，土豆 50G 6天但产量高）
- 体力有限，清障和浇水要平衡，浇水优先
- 木材少于 100 可以安排砍树
- 背包快满就少清障
"""


def _pick_key(cfg):
    """从配置里取密钥：兼容 api_key / apiKey，跳过空值与占位符。"""
    if not isinstance(cfg, dict):
        return None
    key = str(cfg.get("api_key") or cfg.get("apiKey") or "").strip()
    if not key or key in PLACEHOLDERS or not key.startswith("sk-"):
        return None
    return key


def _cfg():
    """读密钥配置 -> (key, api_base, model)；没有可用配置时 key 为 None。"""
    global _announced_ok, _announced_missing
    for path in CANDIDATES:
        if not path:
            continue
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                cfg = json.load(f)
        except FileNotFoundError:
            continue
        except Exception as e:  # noqa: BLE001
            print("[planner] 配置读取失败 %s：%s" % (path, e), flush=True)
            continue
        key = _pick_key(cfg)
        if not key:
            print("[planner] %s 里没有可用的 api_key（空值或占位符）" % path, flush=True)
            continue
        base = cfg.get("api_base") or DEFAULT_BASE
        model = cfg.get("model") or DEFAULT_MODEL
        if not _announced_ok:
            _announced_ok = True
            print("[planner] 使用配置 %s：key=%s…%s model=%s" % (path, key[:6], key[-4:], model), flush=True)
        return key, base, model
    if not _announced_missing:
        _announced_missing = True
        print("[planner] 没有可用密钥配置（期望 %s），模型入口降级为规则流程" % os.path.join(_HERE, CONFIG_NAME), flush=True)
    return None, DEFAULT_BASE, DEFAULT_MODEL


def ask(state_text, timeout=60):
    key, base, model = _cfg()
    if not key:
        return None
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": state_text},
        ],
        "temperature": 0.3,
        "max_tokens": 500,
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        text = payload["choices"][0]["message"]["content"]
        return json.loads(text)
    except Exception as e:  # noqa: BLE001
        print("规划失败：%s" % e, flush=True)
        return None



ACTION_SYSTEM = """你在玩星露谷物语，替玩家操作一个标准农场。每一轮给你当前状态，你只挑一个立刻要做的动作。

可选动作：
- harvest   收成熟作物
- clear     清一块障碍（杂草/嫩枝/石头），arg 可给 {"limit":8,"min_energy":70}
- till_plant 用现有种子翻地播种，arg 可给 {"limit":4}
- water     给缺水的作物浇水
- chop      砍树攒木材
- buy_seeds 去镇上买种子，arg {"qty":20}
- sell      作物丢发货箱、杂物进箱子
- sleep     回屋睡觉（推进到第二天）
- idle      什么都不做（体力太低、没事可干时）

只输出 JSON：{"action":"上面的动作","arg":{...},"reason":"一句话为什么"}
不要解释，不要代码块。

原则：
- 成熟作物优先收；缺水优先浇；体力低于 40 别干重活
- 23:00 前要睡觉，超过 24:00 会晕倒扣钱
- 种子少于 5 且白天（9:00-21:00）就去买
- 木材少于 100 且体力充裕可以砍树
- 背包快满先 sell 腾地方
"""


def next_action(state_text, timeout=45):
    key, base, model = _cfg()
    if not key:
        return None
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": ACTION_SYSTEM},
            {"role": "user", "content": state_text},
        ],
        "temperature": 0.2,
        "max_tokens": 200,
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        raw = payload["choices"][0]["message"]["content"]
        got = _extract_json(raw)
        if got is not None:
            return got
        try:
            with open(os.path.join(_HERE, "planner_raw.log"), "a", encoding="utf-8") as f:
                f.write(raw + "\n---\n")
        except Exception:
            pass
        return None
    except Exception as e:  # noqa: BLE001
        print("问动作失败：%s" % e, flush=True)
        return None

def _extract_json(text):
    """模型经常先说一段再给 JSON，甚至改主意。取最后一个能解析的对象。"""
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    best = None
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    frag = text[start:i + 1]
                    try:
                        obj = json.loads(frag)
                        if isinstance(obj, dict):
                            best = obj
                    except Exception:
                        pass
    return best


TASK_SYSTEM = """你在玩星露谷物语。玩家通过微信给你下了一条任务，你要把它翻译成你能执行的动作序列。

可用动作：
- harvest 收成熟作物
- clear 清障碍，arg {"limit":8,"min_energy":70}
- till_plant 翻地播种，arg {"limit":4}
- water 浇水
- chop 砍树
- buy_seeds 去 Pierre 买种子，arg {"qty":20}
- sell 作物丢发货箱、杂物进箱子
- sleep 回屋睡觉
- idle 什么都不做

只能挑上面的动作，最多 6 步。做不到的任务就给空数组并在 reply 里说明原因。

只输出 JSON：
{"steps":[{"action":"...","arg":{}}],"reply":"给玩家的一句话回复"}
"""


def plan_task(task_text, state_text, timeout=60):
    key, base, model = _cfg()
    if not key:
        return None
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": TASK_SYSTEM},
            {"role": "user", "content": "当前状态：%s\n\n玩家任务：%s" % (state_text, task_text)},
        ],
        "temperature": 0.2,
        "max_tokens": 400,
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        raw = payload["choices"][0]["message"]["content"]
        got = _extract_json(raw)
        if got is not None:
            return got
        try:
            with open(os.path.join(_HERE, "planner_raw.log"), "a", encoding="utf-8") as f:
                f.write(raw + "\n---\n")
        except Exception:
            pass
        return None
    except Exception as e:  # noqa: BLE001
        print("任务规划失败：%s" % e, flush=True)
        return None



TOOL_SYSTEM = """你在玩星露谷物语，替玩家操作一个角色。每轮给你当前状态和周围环境，你只挑一个工具执行。

只准用这些工具：
- harvest {"radius":15}      收成熟作物
- water {"radius":15}        给缺水作物浇水（会自动补水壶）
- till {"x":..,"y":..,"rows":..,"length":..,"direction":"horizontal"}   锄一片地
- plant {"seed":"防风草种子","x":..,"y":..,"rows":..,"length":..,"direction":"horizontal"}  在锄过的地播种
- buy {"id":"472","quantity":10}   远程买种子（防风草=472 青豆=473 花椰菜=474 土豆=475，售价×2）
- ship {"all":true}          作物丢发货箱（第二天结算）
- chest_store {"x":..,"y":..}   把非工具物品存进那格的箱子
- sleep {}                   回屋睡觉推进一天
- go_to {"x":..,"y":..}      走路过去（跨地图也一样：走到门口/路口自己会切图）
- go_to {"x":..,"y":..}      走到某格
- idle {}                    什么都不做

只输出 JSON：{"tool":"工具名","args":{...},"reason":"一句话为什么"}

原则：
- 不要瞬移，所有移动都用 go_to 走路；在屋里就走到门口那格（look 里能看到出口）
- 有成熟的先 harvest；有缺水的先 water
- 背包快满就先 ship（或在箱子旁边时 chest_store）
- 种子 0 且钱够就 buy
- 23:00 前 sleep，别熬到 2:00
"""


def next_tool(state_text, look_text, timeout=60):
    key, base, model = _cfg()
    if not key:
        return None
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": TOOL_SYSTEM},
            {"role": "user", "content": "状态：\\n%s\\n\\n周围：\\n%s" % (state_text, look_text[:2500])},
        ],
        "temperature": 0.2,
        "max_tokens": 300,
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        raw = payload["choices"][0]["message"]["content"]
        got = _extract_json(raw)
        if got is not None:
            return got
        try:
            with open(os.path.join(_HERE, "planner_raw.log"), "a", encoding="utf-8") as f:
                f.write(raw + "\n---\n")
        except Exception:
            pass
        return {"tool": "idle", "args": {}, "reason": "模型返回无法解析"}
    except Exception as e:  # noqa: BLE001
        print("问工具失败：%s" % e, flush=True)
        return None
WORK_SYSTEM = """你是星露谷农场的后台规划者。人物会在你思考时继续干活，返回的计划将按最新状态重新检查。
给出未来几项有价值的农务；不要逐格控制，不要输出目标ref、鼠标坐标或底层命令。
可用动作与参数：harvest {}、water {}、till_plant {"limit":6}、clear {"limit":6,"min_energy":70}、
chop {}、buy_seeds {"qty":20}、sell {}、sleep {}。
water/harvest会分段持续到附近目标完成；clear/till_plant的limit是本步骤总量。最多6步。
优先当前玩家任务，遵守其限制；做不到就返回空steps并说明原因。没有玩家任务时照料现有作物，再考虑扩种。
不要为了保持忙碌耗尽体力；clear_min_energy至少40，睡眠时间不晚于2300。种植与清障按执行器可用范围进行。
只输出JSON：
{"daily":{"buy_qty":20,"buy_when_below":5,"clear_limit":3,"clear_min_energy":70,"chop":false,"sleep_at":2200,"reason":"今日重点"},
 "steps":[{"action":"water","arg":{},"reason":"照料作物"}],"reply":"给玩家的简短说明"}
daily只调整必要字段。没有有价值的工作时steps可为空。不要重复已经完成的工作。
action_history记录近期目标结果：done已完成，progress有实际进展，failed失败，no_progress无变化。
blocked_targets在目标状态变化前禁止重试；改做其他可执行工作，不要反复安排只有受阻目标的技能。
"""


def plan_work(state_text, current_plan, task=None, timeout=30):
    """只做HTTP推理，不访问游戏；由单个后台工作线程调用。"""
    key, base, model = _cfg()
    if not key:
        return None
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": WORK_SYSTEM},
            {"role": "user", "content": json.dumps(
                {"state": state_text, "current_plan": current_plan, "player_task": task},
                ensure_ascii=False)},
        ],
        "temperature": 0.2, "max_tokens": 650,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(base, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        result = _extract_json(payload["choices"][0]["message"]["content"])
        return result if isinstance(result, dict) else None
    except Exception as exc:  # noqa: BLE001
        # 不打印请求/响应全文，避免配置与玩家文本进入异常日志。
        print("后台规划请求失败：%s" % type(exc).__name__, flush=True)
        return None


if __name__ == "__main__":
    import sys
    print(json.dumps(ask(sys.stdin.read()), ensure_ascii=False, indent=2))
