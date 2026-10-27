---
name: memory
description: 为本项目的星露谷机器人查询和记录游戏记忆，包括技能结果、走过的路、看到的对象、发现的连接和带证据的假设。由游戏planner选择调用。
---

# 游戏统一记忆

主库为数据目录的 `memory.sqlite3`，实现是 `bot/memory_store.py`。观察、移动、跨图、技能调用、目标结果、规划和笔记共用版本化事件格式；地图与连接表是索引。跨日和重启保留，按游戏存档ID及玩家ID隔离。旧版游戏桥没有存档ID时只按会话隔离；跨重启记忆需要 AgentStardew 0.1.1。

## 机器人调用

```json
{"action":"memory","arg":{"op":"recall","kind":"movement","location":"Farm","limit":8},"reason":"回忆自己实际走过的路"}
```

```json
{"action":"memory","arg":{"op":"remember","text":"箱子可能需要交互，水壶使用已失败","evidence":["从返回结果取得的真实事件ID"]},"reason":"保存待验证推断"}
```

事件类型：`observation`、`position`、`movement`、`trace_gap`、`transition`、`intent`、`plan`、`skill_call`、`skill_target`、`skill`、`reflection`、`instruction`。可按 query、kind、location 检索。返回包含事件ID、来源、可信程度、游戏时间、位置及结果；用ID关联证据，不从记忆重放旧ref。

游戏现场为 `observed`，玩家指令及旧缓存迁移为 `reported`，模型笔记和计划为 `hypothesis`。模型记忆不能晋升为已验证事实。笔记引用的证据必须属于同一存档。旧事实可能已变化，到现场后重新观察。

桥在游戏更新时记录人物换格坐标，保留最近256个变位样本；记忆库按存档/会话/序号去重，重启也不重复导入。高层技能内部移动也有实际轨迹。原生样本序号缺失时用trace_gap标记；瞬移只记起终点，任何情况都不补画未经采样的中间格。门和出口只有被观察到或实际走过才形成连接；不会根据常识补反向路或预设世界地图。

自动记录由 `ObservedBridge` 和技能执行器完成，每次调用成功、失败、无进展都入库。模型不需要手动补一遍事实日志；remember用于解释、假设和反思。

## 人类查看

从仓库目录运行 `python bot/memory_cli.py stats`，再用 `recall --scope "stats中的save_id" --kind movement` 查看路径。`export --scope "..." --out memory-export.jsonl` 导出同一事件格式，原库不变。运行数据已加入 `.gitignore`。
