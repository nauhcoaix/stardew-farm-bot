# 下次开工清单（2026-10-26 夜）

## 现在到哪了

`travel` 技能能按录到的拐点走到出口，**但最后一步踩不出去**。

实机日志（Town → Mountain，20 个拐点全走到了）：

```
按录到的路线走：20 个拐点，终点 (81,0)
拐点1 (81,0) -> 已到达目标
…
拐点20 (81,0) -> 已到达目标
没切到 Mountain（现在 Town）
```

## 明天先试这 4 种"踩出口"的方法

在 `bot/farm_service.py` 的 `travel()` 里，学到坐标那段之后补：

1. **往外多走一格**：走到 `(x, y-1)` 或 `(x-1, y)`（取决于出口在哪条边）
2. **分两步靠近**：先到相邻格 `(x, y+1)`，再 `move` 到 `(x, y)`
3. **交互一次**：`b.interact(ref)`（有些门/口需要交互）
4. **连点方向键**：`farmer.send_key(VK_UP/W/A/S)` 走一格（绕过寻路）

一次试一种，看哪种能让 `location` 变。

## 已经验证能用的

```
FarmHouse ↔ Farm       ✓
Farm → BusStop         ✓
BusStop → Town         ✓
Farm → Backwoods       ✓（视野外出口，往边缘找）
```

## 数据在哪

```
_dev\routes\exits-learned.json     11 张地图 / 26 条连接 / 每条最多 40 个拐点
_dev\routes\trail-*.jsonl          原始采样（要重算就跑 bot\route_learn.py）
```

## 明天的顺序

1. 跑通"踩出口" → `travel` 闭环
2. 把 `travel` 加进 `local_action` + planner 的可用动作（现在只有模型能规划它）
3. 补录缺的路线：`Mountain → Railroad`、`Mine`、`Desert`

## 环境提醒

- 游戏今天出现过 3 次"僵尸进程"（进程在、没窗口、桥不通）：杀掉重开 `_dev\stock-farm-launch.bat` 即可
- 读档后约 20 秒内所有动作都是 BUSY，`travel` 已经会自动等
- 服务启动用 `_dev\run-farm-service.bat`，或 `pythonw` + `DETACHED_PROCESS`
