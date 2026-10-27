---
name: explore
description: 让本项目的星露谷机器人主动选择路径探索、对象交互或功能试验，依据实际观察与统一记忆推进，并把结果写回记忆。由游戏planner选择调用。
---

# 自主探索

目的地、对象和试验假设由模型选择。`bot/exploration.py` 根据现场与记忆生成可达前沿、对象及已发现连接；`bot/agent_skills.py` 执行有界动作。没有按日期或固定坐标安排的探索路线。

## 三种模式

- `path`：从当前 `exploration_candidates` 选择一个前沿ID，或给出已知可达坐标。每段最多走3格，逐格观察并记录；后续路径按新现场计算。
- `interaction`：选择现场对象的稳定target ID，靠近并交互。对话/菜单返回后，由模型继续选择menu或先observe查看控件。
- `capability`：选择目标、use/interact、工具槽以及要验证的question，试一次。结果来自游戏和执行器，不因“已经尝试”就宣称功能可用。

```json
{"action":"explore","arg":{"mode":"path","candidate":"从当前输入复制的frontier候选ID"},"reason":"查看已观察边缘以外的区域"}
```

```json
{"action":"explore","arg":{"mode":"capability","target":"当前对象的target ID","skill":"use","slot":0,"question":"该工具能否作用于这个对象？"},"reason":"验证工具用途"}
```

## 使用证据

先查memory可以重用走过的路和出口。可通行格子来自观察，visited才表示实际到过；两者不能混淆。已观察到的出口是连接证据，实际通过后为confirmed。室内与室外保持不同区域，回程没有证据时不补假路。

失败、无进展和路径受阻会记录到同一个记忆库；同一未变化现场不重复试。目标、起点或相关地图知识变化后才能重新评估。探索后，模型可以继续当前问题、改试别的能力、做其他工作或结束，没有强制“探索完就回农场”。

未知协议命令和猜测屏幕坐标不属于本技能。实际可用的原子能力由目录列出；菜单点击只能选择observe发现的控件ID，执行前重新复核位置和菜单身份。STOP和玩家新指令在动作片段边界优先。
