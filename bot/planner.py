"""让模型给农场出计划（每天一次 + 事件触发）。

复用 wechat-auto 的 DeepSeek 配置，不额外申请 key。
"""
import json
import os
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
CANDIDATES = [
    os.environ.get("FARM_CFG") or "",
    os.path.join(_HERE, "planner-config.json"),
]

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

输入里会给你农场的方位和地图：

- 「你在：农舍东南约 12 格，开垦区边上」—— 你的位置
- 「农场统计：可耕地 N 格，已翻地 M 格，其中空着能直接种 K 格，作物 C 格，障碍 O 个（开垦区内 I 个）」
- 分区列表：每个区有多少耕地/作物/障碍
- 字符地图（@你 c作物 t空地 .可耕地 #障碍 w水 b箱子 H农舍）

**关键：清障碍是看「缺不缺地」，不是「有没有障碍」。**
能直接种的格子 = 已翻地空着的 + 可耕地的。只有这个不够你要种的量，才该清障碍，
而且只在开垦区内清。农场边角、路边、树底下那些杂草，种不到那里，永远不要清。

你现在要给出「今天怎么安排」。只输出一个 JSON 对象，不要解释，不要代码块：

{
  "crop": "要种的作物种子名，比如 防风草种子 / 土豆种子 / 花椰菜种子",
  "buy_qty": 整数，这次买多少种子（0 表示不买）,
  "buy_when_below": 整数，种子低于多少就去补货,
  "plant_target": 整数，今天打算种几格（清障和翻地都看这个数）,
  "clear_zone": "none" | "cluster" | "path" | "all"，
      none=今天不清；cluster=只清开垦区（默认，最安全）；
      path=只清挡路的；all=大面积开荒（只在钱多地多时用）,
  "clear_reason": "一句话说明为什么需要清（要说清缺几格）",
  "clear_min_energy": 整数，体力低于它就停止清障,
  "chop": true/false，今天要不要砍树攒木材,
  "sleep_at": 整数，比如 2100 表示 21:00 睡觉,
  "reason": "一句话说明今天的重点"
}

判断原则：
- 钱少就种回本快的（防风草 20G 4天，土豆 50G 6天但产量高）
- 能直接种的格子够 plant_target，clear_zone 就填 none
- 只有开垦区内缺地，才用 cluster；开垦区外一律不清
- 体力有限，浇水优先于清障
- 木材少于 100 可以安排砍树
- 背包快满就少清障
"""


def _cfg():
    for path in CANDIDATES:
        if not path:
            continue
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                c = json.load(f)
            if c.get("api_key"):
                return c.get("api_key"), (c.get("api_base") or "https://api.deepseek.com/chat/completions"), (c.get("model") or "deepseek-chat")
        except Exception:
            continue
    return None, "https://api.deepseek.com/chat/completions", "deepseek-chat"


def _old_cfg():
    try:
        with open(WORKSPACE_CFG, "r", encoding="utf-8-sig") as f:
            c = json.load(f)
        return c.get("api_key"), c.get("api_base") or "https://api.deepseek.com/chat/completions", c.get("model") or "deepseek-chat"
    except Exception:
        return None, "https://api.deepseek.com/chat/completions", "deepseek-chat"


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



PICK_SYSTEM = """你在替玩家决定「清哪几块障碍」。给你的候选都带编号、位置、理由、体力代价。
规矩：
- 只清「在开垦区内/挡路」的；「开荒（区外没用）」的不要选
- 一次最多选 6 个，够用就行，别贪
- 体力不够就少选
只输出 JSON：{"clear": [1,2], "reason": "一句话"}
"""


def pick_candidates(state_text, timeout=45):
    """让模型从候选清单里挑（选择题模式）。失败时返回 None，调用方按前 N 个兜底。"""
    key, base, model = _cfg()
    if not key:
        return None
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": PICK_SYSTEM},
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
        text = payload["choices"][0]["message"]["content"]
        return json.loads(text)
    except Exception as e:  # noqa: BLE001
        print("挑选失败：%s" % e, flush=True)
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
if __name__ == "__main__":
    import sys
    print(json.dumps(ask(sys.stdin.read()), ensure_ascii=False, indent=2))
