"""游戏窗口左上角的叠加层：显示「下一步要做什么」和「机器人记住的内容」。

Mod 端命令：{"command": "hud", "args": {"title": str, "lines": [str], "ttlMs": int}}
旧桥不支持时自动关闭，不影响主循环。
"""

MAX_LINES = 10


class Hud:
    """一条连接上的叠加层推送；不支持时只提示一次。"""

    def __init__(self, log=print, ttl_ms=60000):
        self.log = log
        self.ttl_ms = ttl_ms
        self.disabled = False

    @staticmethod
    def action_line(action):
        arg = action.get("arg") or {}
        text = ", ".join("%s=%s" % (k, v) for k, v in arg.items() if v is not None)
        return "%s %s" % (action.get("action") or "?", ("{" + text + "}") if text else "")

    @staticmethod
    def memory_lines(memory, snap=None, limit=5):
        """最近几条记忆压成短句：动作结果、模型笔记、玩家意图。"""
        try:
            rows = memory.query(limit=limit, snap=snap)
        except Exception:
            return []
        out = []
        for row in rows:
            data = row.get("data") or {}
            text = (data.get("goal") or data.get("text") or data.get("skill")
                    or data.get("detail") or row.get("kind") or "")
            text = str(text).replace("\n", " ").strip()
            if not text:
                continue
            label = str(row.get("kind") or "?")
            status = row.get("status") or ""
            if status and status not in ("observed", "succeeded", "proposed"):
                label += "/" + str(status)
            out.append("[%s] %s" % (label, text[:70]))
        return out

    async def push(self, bridge, title, lines, ttl_ms=None):
        if self.disabled:
            return False
        payload = {
            "title": str(title)[:120],
            "lines": [str(x).replace("\n", " ")[:120] for x in lines][:MAX_LINES],
            "ttlMs": int(ttl_ms or self.ttl_ms),
        }
        try:
            await bridge.request("hud", payload)
            return True
        except Exception as exc:  # noqa: BLE001
            text = str(exc)
            if "UNSUPPORTED" in text or "未知命令" in text or "INVALID_ARGUMENT" in text:
                self.disabled = True
                self.log("游戏桥不支持窗口叠加层（hud），已关闭；装新版 AgentStardew 桥后可用")
            return False

    async def show_action(self, bridge, action, *, goal="", queued=0, thinking=False,
                          memory=None, snap=None):
        lines = [self.action_line(action)]
        if goal:
            lines.append("目标：" + str(goal)[:80])
        lines.append("剩余 %d 步 · %s" % (queued, "后台规划中" if thinking else "无新请求"))
        if memory is not None:
            lines.extend(self.memory_lines(memory, snap))
        return await self.push(bridge, "下一步动作", lines)

    async def show_status(self, bridge, title, extra=(), memory=None, snap=None):
        lines = [str(x)[:100] for x in extra if x]
        if memory is not None:
            lines.extend(self.memory_lines(memory, snap))
        return await self.push(bridge, title, lines)
