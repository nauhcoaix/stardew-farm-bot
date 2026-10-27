# 星露谷农场机器人 — 架构与功能现状

> 生成时间：2026-10-27 23:10
> 代码基线：`pantent2 @ 960c975`
> 数据基线：`D:\1\steamapps\common\Stardew Valley\_dev\memory.sqlite3`

---

## 1. 一句话定位

一个「**LLM 规划 + 技能执行 + 持久记忆**」的星露谷自动玩家：

```
游戏 (SMAPI mod) ←WebSocket→ Python 桥 → 技能执行器 → LLM 规划器
                              ↕
                        SQLite 记忆库（事实/任务/地标/作物/检索）
```

不是状态机脚本，而是**每轮让模型看现场+记忆，自己选下一步技能**。

---

## 2. 运行形态

### 2.1 进程拓扑

| 进程 | 角色 | 启动方式 |
|---|---|---|
| `StardewModdingAPI.exe` | 游戏 + `AgentStardew` mod，监听 `ws://127.0.0.1:17654` | `run-farm-launch.bat` |
| `farm_service.py --loop` | 主循环：读快照 → 规划 → 执行 | `run-farm-service.bat` / `start-farm.bat` |
| `StandaloneFishingSkill` (可选) | 独立钓鱼 mod，写 `runtime/status.json` 心跳 | 未安装 |

**手机/微信侧**：`inbox` 投递任务，`outbox` 收汇报（由 `pump_loop` 5 秒轮询）。

### 2.2 数据目录（`_dev`）

```
_dev/
  bot/                  → 指向仓库 bot 的 junction（改代码即生效）
  memory.sqlite3        记忆库（约 110 MB）
  action-history.json   动作失败历史
  planner-config.json   LLM key/base/model（不入库）
  knowledge.json        （可选）覆盖 bot/knowledge.json
  routes/               录制的路线与出口坐标
  inbox/ outbox/        任务与汇报
  farm-service.log  _svc-log.txt  status.json  decisions.jsonl
```

### 2.3 环境变量

| 变量 | 作用 |
|---|---|
| `FARM_DATA` | 数据目录（默认 `_dev`） |
| `FARM_CFG` | 规划器配置路径 |
| `FARM_REPORT_CHAT` | 汇报目标微信会话 |
| `FARM_EMBED_DIR` | 覆盖向量模型目录 |
| `STARDEW_GAME_PATH` | mod 编译用游戏路径 |
| `APPDATA` | 定位 SMAPI 错误日志 |

---

## 3. 代码结构（bot/，25 个模块 / 6729 行）

### 3.1 分层

| 层 | 模块 | 行数 | 职责 |
|---|---|---|---|
| **入口** | `farm_service.py` | 578 | 主循环、inbox 指令、状态写入、日程推进 |
| **规划** | `planner.py` | 179 | 组装 LLM 请求（prompt + JSON 解析） |
| | `work_planner.py` | 211 | 计划队列、后台预取、任务/汇报、跳过判定 |
| **执行** | `agent_skills.py` | 732 | 技能运行时：坐标/区域/前置条件/浇水记账 |
| | `skill_catalog.py` | 190 | 动作定义、参数白名单、`requires`、`available()` |
| | `farmer.py` | 1245 | 农务原语：till/plant/water/harvest/clear/chop（巨石） |
| **世界模型** | `farm_layout.py` | 393 | 农场摘要、聚类、通行网格、BFS、清障候选 |
| | `places.py` | 136 | 区域图、出口解析、录到的路线 |
| | `knowledge.py` + `knowledge.json` | 88 | 已知世界事实（坐标/作物/规则/礼物） |
| **记忆/检索** | `memory_store.py` | 1197 | SQLite：events/tiles/links/facts/tasks/planted/landmarks/schedule/grams/vectors |
| | `embeddings.py` | 124 | bge-small-zh ONNX 编码 + 余弦 |
| | `action_history.py` | 143 | 动作失败记忆（配合 blocked 指纹） |
| **桥** | `farm_client.py` | 175 | WebSocket 客户端（24 个命令封装） |
| | `observed_bridge.py` | 84 | 读取快照的只读包装 |
| **子系统** | `skill_fishing.py` | 121 | 调用独立钓鱼 mod（心跳/启停） |
| | `exploration.py` | 96 | 探索候选、`path_to`、`region_route` |
| **辅助** | `launch_farm.py` | 240 | 开游戏 + 点读档（比例坐标） |
| | `farm-doctor.py` | 133 | 自检（桥/存档/窗口） |
| | `memory_cli.py` | 54 | 记忆统计/导出 |
| **历史遗留** | `shop.py` `go_buy.py` `go_shop.py` `shop_hunt.py` `go_south.py` `junimo.py` | 610 | 早期导航/商店脚本，主链路已不依赖 |

### 3.2 主循环时序（`farm_service.run_once`）

```
1. bridge.snapshot(radius≤16)           ← 唯一感知来源
2. memory.sync_facts(snap)              ← 派生 seeds/crops/dry/energy/location…
   ├─ update_phases()                   作物阶段
   ├─ update_landmarks()                障碍/水源/箱子进地标表
   └─ 跨天检测（day 变化清任务、写 session）
3. memory.build_schedule()/validate/auto_advance   每日 3~5 阶段
4. work_planner.poll(state_text)        ← 后台线程调 LLM
   └─ 产出 steps（技能 + 参数），入队
5. take_action(ready)                   ← 按 requires/availability/blocked 过滤
6. SkillRuntime._execute(name,arg)      ← 前置条件 → 区域半径 → 具体执行
7. bridge.<cmd>                         ← 发给 mod，等响应
8. 结果写 events（skill/skill_call/reflection），更新 facts
```

**节拍**：规划 1.4~3.2 s/轮，动作 0.2~1 s；紧急指令（task/status）会打断当前规划。

---

## 4. 技能系统

### 4.1 动作清单（`skill_catalog.DEFINITIONS`，26 个）

| 动作 | 作用 | 关键参数 | 前置条件 |
|---|---|---|---|
| `observe` | 拍快照 | radius 1..16 | — |
| `memory` | 查/写记忆 | op=recall\|remember, query, kind | — |
| `move` | 走到坐标/目标 | x, y, target, steps 1..24 | 目标可达 |
| `explore` | 探索前沿/地标 | mode=path\|capability | 有候选或地标 |
| `interact` | 交互 NPC/门/床 | target | 目标在观察中 |
| `use` | 对目标用工具 | target, slot | 目标在观察中 |
| `select` | 切物品槽 | slot 0..35 | 背包非空 |
| `menu` / `menu_click` / `menu_close` | 菜单操作 | target（控件 ID） | 有菜单 |
| `till` | 锄地 | target | **seeds≥1** + 锄头 |
| `plant` | 播种 | target, slot | **seeds≥1** |
| `till_plant` | 锄+种（连种 3 格自动浇水） | limit 1..24 | **seeds≥1** |
| `water` | 浇水（视野外走记忆坐标） | limit | 有水水壶 |
| `harvest` | 收成 | limit | 有成熟作物 |
| `clear` | 清障（农舍半径内优先） | limit, min_energy, target | 有工具+障碍 |
| `chop` | 砍树 | limit | 斧头 |
| `refill` | 补喷壶 | target | 水源 |
| `buy_seeds` | 商店购买 | index, qty | ShopMenu |
| `sell` / `store` / `ship` | 卖/存/发货 | target, slot | 农场 |
| `craft` / `recipes` | 制造 / 列配方 | item, count / limit | 配方已学+材料 |
| `sleep` | 睡觉 | target | 床 |
| `wish`/`junimo` 等历史项 | — | — | 已不用 |

### 4.2 三重约束

```
① requires（声明式）  till/plant/till_plant → seeds≥1；harvest → harvestable≥1
② available()（现场态）视野内有目标 或 记忆里有（facts）→ 最新改动
③ blocked()（经验态）同 fingerprint 失败 → 30 秒冷却
    fingerprint = [地点, 目标语义/坐标, 菜单, 选中物品]
```

### 4.3 作业半径（农舍糖）

```
默认 12 格；目标超出 → 若 ≤ 半径+6 自动放宽到该距离，否则拒绝
清障候选按"离农舍距离"排序，近的优先
```

---

## 5. 记忆与检索

### 5.1 SQLite 表（实测行数）

| 表 | 行数 | 作用 |
|---|---|---|
| `events` | 38 938 | 全量事件（observation/skill/plan/intent/reflection/session/resource…） |
| `tiles` | 39 330 | 走过的地图格（含可行走/障碍语义） |
| `links` | 204 | 区域连接（含出口坐标、是否验证） |
| `grams` | 457 360 | 中文 bigram 倒排索引 |
| `vectors` | 4 010 | bge-small-zh 向量（512 维） |
| `facts` | 84 | 当前状态事实（seeds/crops_thirsty/obstacles_known/is_fresh_save…） |
| `tasks` | 58 | 任务板（前置条件/blocked 原因） |
| `planted` | 18 | 已种作物账本（游戏日/阶段/浇水） |
| `landmarks` | 2 359 | 地标（障碍/水源/箱子/床/商店门） |
| `schedule` | 12 | 每日 3~5 阶段 |
| `imports` | 11 | 旧数据迁移标记 |
| `cursors` | 6 | 轨迹游标 |

### 5.2 检索（RAG）

```
候选：bigram 倒排（中文 2-gram / 英文整词）→ 最多 400 条
打分：0.45 向量余弦 + 0.30 bigram 余弦 + 0.15 时间衰减(6h) + 0.10 类型权重
类型权重：lesson 3.0 / instruction 2.2 / import 2.0 / reflection 1.8 / plan 0.7
过滤：plan/intent 只认"同一游戏日"（跨天不召回）
常驻上下文：lessons（跨存档长期经验）+ world_knowledge（18~20 行）
```

### 5.3 生长账本

```
planted(save, location, x, y, crop, crop_name, planted_day, watered_day,
        phase_last_seen, phase_seen_day)
facts: crops_growing / crops_ready_tomorrow / crops_overdue / crops_thirsty / planted_unwatered
规则：不浇水当天不长；换季前 3 天不种慢熟作物
```

### 5.4 经验压缩（L4）

`scripts/consolidate-memory.py`：读 24h 内 reflection/instruction/plan/resource → LLM 压成 ≤5 条教训 → 写 `events(kind='lesson')`（跨存档常驻）。

---

## 6. 桥与 mod 协议

### 6.1 传输

```
WebSocket ws://127.0.0.1:17654
请求 {v, requestId, actionId, command, args}
响应 {status: completed|failed|cancelled, result, error:{code,message,retryable}}
写动作必须带 actionId（幂等）；读动作 snapshot radius 1..16
```

### 6.2 命令（Protocol.cs）

| 类别 | 命令 |
|---|---|
| 读 | `hello` `snapshot` `status` `screenshot` `menudump` |
| 写 | `move` `select` `use` `interact` `menu` |
| 菜单 | `menuclick` `menuclickinv` `menuclose` `shopbuy` |
| 容器 | `placechest` `chestput` `chestget` `ship` `bin` |
| 世界 | `warp` `forceunlock` `reset` |
| 制造 | `craft` `recipes` |

### 6.3 mod 源码（`mod/agent-stardew/mods/AgentStardew/`，1384 行）

| 文件 | 行数 | 职责 |
|---|---|---|
| `ModEntry.cs` | 177 | 入口、命令分发、主线程调度 |
| `GameActions.cs` | 305 | 移动/使用/交互的动作状态机 + 证据判定 |
| `Observations.cs` | 176 | 快照（实体语义、cropPhase、passable、trajectory） |
| `MenuDump.cs` | 274 | 菜单反射、商店、箱子、warp |
| `Crafting.cs` | 166 | 制造（解析 Data/CraftingRecipes） |
| `BridgeServer.cs` | 101 | 并发/会话/写锁 |
| `ActionCoordinator.cs` | 84 | 同一时刻只允许一个写动作 |
| `Protocol.cs` | 60 | 参数白名单、错误码 |
| `MovementInput.cs` | 36 | 走格输入 |
| `BackgroundUpdates.cs` | 48 | 后台刷新 |

**编译**：`D:\dotnet-sdk\dotnet.exe build -c Release -p:GamePath="<游戏目录>"`（.NET SDK 8 已装）。

---

## 7. 规划器

### 7.1 输入（每轮）

```
state_text = {
  player, inventory, menu, targets(64 个实体语义), date, time,
  skills(动作目录 + requires + available), exploration_candidates,
  memory(recent 8), state_facts, open_tasks, landmarks,
  water_targets, seeds, today_brief, schedule, current_stage,
  world_knowledge(18行), lessons(10)
}
objective = 玩家任务（inbox）或空
```

### 7.2 输出

```json
{"goal": "小目标", "steps": [{"action":"...","arg":{...},"reason":"..."}],
 "reply": "给玩家的汇报", "reflection": "自我反思"}
```

`clean_steps` 白名单校验：动作必须在目录内、参数必须在声明内、`steps` 每次 ≤6 条。

### 7.3 任务生命周期

```
inbox {"cmd":"task","arg":"…"} → submit_task → intent/objective
   → 规划 → 执行 → 完成判定（目标含"报告/已到达/完成"且只选 observe/memory）
   → 自动汇报 → 放下任务
   → 连续空转 4 轮也汇报并放下
```

---

## 8. 现有功能清单

| 类别 | 功能 | 状态 |
|---|---|---|
| 农务 | 锄地、播种、浇水、收成、补喷壶 | ✅ 真机验证 |
| 清障 | 杂草/石头/嫩枝，农舍半径优先，自动扩圈 | ✅ 真机验证 |
| 砍树 | 攒木材（`chop`） | ✅ 可用 |
| 制造 | 宝箱/火把/围栏等（配方数据解析） | ✅ 真机验证材料检查，⏳ 成品入包未验证 |
| 跨图 | Farm↔BusStop↔Town、Farm↔Backwoods 等 | ✅ 部分；出口 x=-1 踩不进去 |
| 探索 | 前沿探索、朝地标走 | ✅ 可用 |
| 商店 | `buy_seeds`（需先开商店菜单） | ⚠️ 未打通到店 |
| 睡觉 | `sleep`（需到床边） | ⚠️ 交互常失败 |
| 钓鱼 | 独立 mod + 客户端 + `fish` 技能 | ⚠️ mod 未装、无鱼竿 |
| 记忆 | 事实/任务/地标/作物/日程/检索/向量/教训 | ✅ |
| 经验学习 | L4 压缩成教训 | ✅ 每日可跑 |
| 汇报 | outbox + 微信播报 | ✅ |
| 自检 | `farm-doctor.py` | ✅ |

---

## 9. 测试体系

15 个文件 / 121 个用例 / 2186 行，`python -m unittest discover -s tests`：

| 文件 | 用例 | 覆盖 |
|---|---|---|
| `test_autonomy.py` | 27 | 技能运行、菜单、失败记录、半径 |
| `test_continuous_work.py` | 18 | 持续工作、队列、中断 |
| `test_facts_tasks.py` | 12 | 状态事实、任务板、requires |
| `test_action_history.py` | 10 | 失败记忆 |
| `test_growth_ledger.py` | 8 | 作物账本、生长进度 |
| `test_landmarks_schedule.py` | 8 | 地标、日程 |
| `test_memory_rag.py` | 8 | bigram/向量检索、知识 |
| `test_day_scope.py` | 5 | 跨天隔离 |
| `test_craft.py` | 6 | craft/recipes |
| `test_move_steps.py` | 4 | steps 语义 |
| `test_zone.py` | 4 | 半径拦截 |
| `test_zone_expand.py` | 3 | 半径自动扩圈 |
| `test_water_loop.py` | 3 | 浇水死循环防护 |
| `test_house_priority.py` | 3 | 农舍优先 |
| `test_replant.py` | 2 | 收完就地补种 |

---

## 10. 运维脚本（`scripts/`）

| 脚本 | 用途 |
|---|---|
| `send-task.ps1` / `.bat` | 给机器人下任务（写 inbox） |
| `prune-memory.py` | 剪记忆噪声（默认干跑，`--apply` 才写） |
| `consolidate-memory.py` | L4 经验压缩（**每天该跑一次**） |
| `backfill-embeddings.py` | 补向量 |
| `fetch-embedding-model.py` | 下载 bge-small-zh（23 MB，走 hf-mirror） |
| `checksums.py` | 生成/校验 `CHECKSUMS.sha256` |
| `install-agent-bridge.ps1` | 部署 mod |

---

## 11. 已知限制 / 技术债（重构参考）

### 11.1 架构层面

| 问题 | 表现 | 影响 |
|---|---|---|
| **感知只有 16 格** | 走远就“忘了”现场 | 靠 facts/landmarks 补，但仍不全 |
| **可用性判定查视野** | 目标不在视野就判“不可用” | 已改成“视野 或 记忆”，仍有边界 |
| **巨石模块** | `farmer.py` 1245 行、`memory_store.py` 1197 行 | 难测、易回归 |
| **双份规划路径** | `planner.py`（旧）与 `work_planner.py`（新）并存 | 概念重复 |
| **历史脚本残留** | `shop/go_buy/go_shop/shop_hunt/go_south/junimo` 610 行 | 死代码 |
| **协议与 bot 版本耦合** | 命令参数硬编码在两侧 | 升级需同步 |
| **单进程假设** | 多开服务会抢桥、目标互相打乱 | 无锁/无 leader 选举 |
| **saveId 会话漂移** | 旧数据用 `session:` 作用域，新用 `save:` | 记忆出现“孤儿行” |
| **数据无 schema 版本迁移** | `user_version=2`，靠 try/except ALTER | 升级易漏 |

### 11.2 行为层面（实测）

| 现象 | 根因 | 现状 |
|---|---|---|
| memory/observe 空转 | 模型“先定位”，上下文无坐标 | 已修（直接给 water_targets） |
| clear 一直失败 | 半径边界拒 13~14 格 | 已修（自动扩圈） |
| 浇水死循环 | 记忆里存在不可达待浇格 | 已修（3 次清记录） |
| move 选了不动 | fingerprint 不含坐标，全部被拦 | 已修（坐标进指纹） |
| 旧日计划干扰 | plan 无日期过滤 | 已修（跨天不召回） |
| 旧存档找开局礼物 | 礼物知识无条件 | 已修（is_fresh_save + 条件） |
| 进农舍空转 | `move` 把门当目标 | ❌ 未修 |
| 出口 x=-1 踩不进去 | 传送格不在可通行网格 | ❌ 未修 |
| 结果不可验证的制造 | 无木材，未能成功做一次 | ⏳ 未验证 |

### 11.3 运行环境

| 问题 | 频率 |
|---|---|
| 游戏崩溃/变僵尸（桥 1225/超时） | 每 10~15 分钟一次 |
| 存档读档需要 ~90 s 重开 | 每次崩溃后 |
| 规划延迟 1.4~3.2 s/轮 | 8 小时游戏时间 ≈ 真实 1 天多 |

---

## 12. 重构建议（基于现状）

### 12.1 分层目标

```
┌ World Model ─── 全图记忆（tiles/landmarks/links/planted），唯一真相
├ Perception ──── snapshot → 增量更新 World Model（幂等）
├ Scheduler ───── 日程/任务（前置条件 + 阻塞原因）
├ Planner ─────── LLM：读 World Model 摘要 + 日程 → steps
├ Executor ────── steps → 技能调用（requires/blocked/radius 统一在此）
└ Skills ──────── 单一职责、可单测（farmer 拆成 till/plant/water/harvest/clear）
```

### 12.2 优先动作

1. **拆巨石**：`farmer.py` → 5 个技能文件；`memory_store.py` → `schema/events/facts/tasks/world/rag`
2. **统一入口**：删掉 `planner.py` 旧路径与 610 行历史脚本
3. **世界模型独立**：`World Model` 作为只读对象，技能不得直接读 snapshot 判断“有没有活”
4. **协议契约化**：`protocol.json` 单一来源，两侧生成
5. **schema 迁移**：`user_version` + 迁移脚本
6. **单实例保护**：文件锁 + 桥 owner 检测
7. **进程守护**：崩溃自动重启游戏与服务（收益最大）
8. **回放/评测**：录一段 snapshot+step 序列，离线回归技能与规划质量

---

## 附：数据文件一览

| 路径 | 内容 | 是否入库 |
|---|---|---|
| `bot/knowledge.json` | 世界知识 | ✅ |
| `bot/models/` | 向量模型 23 MB | ❌（脚本下载） |
| `_dev/memory.sqlite3` | 记忆库 | ❌ |
| `_dev/routes/` | 录制路线 | ❌ |
| `_dev/planner-config.json` | LLM key | ❌ |
| `_dev/action-history.json` | 失败历史 | ❌ |
| `mod/**/*.dll` | 编译产物 | ❌ |
| `CHECKSUMS.sha256` | 一致性清单（191 文件） | ✅ |
