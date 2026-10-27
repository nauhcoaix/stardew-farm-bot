# 怎么教农场机器人（不改代码）

代码里规划器只吃三样东西：**当前快照、记忆库、任务目标**。
所以"教"它的通道只有四个，全部是数据层面。

## 一、下任务（最有效）

任务会直接变成模型的 `objective`，同时写成一条 `player` 记忆。

```powershell
# 交互式（双击也行）
scripts\send-task.bat

# 命令行
pwsh -File scripts\send-task.ps1 -Task "只清农舍 12 格内的杂草，别出圈"

# 问状态
pwsh -File scripts\send-task.ps1 -Command status
```

任务要写清三件事：**在哪、做什么、不许什么**。

| 写法 | 效果 |
|---|---|
| 只清农舍 12 格内的杂草，不许出圈 | 好：有地点 + 有约束 |
| 把农舍南边空地锄了，种上现有种子 | 好：指定地点 + 动作 |
| 记住：农场去小镇走 (81,0) 再往北一格 | 好：教知识点 |
| 随便干点啥 | 差：会乱跑 |

**注意**：如果任务里写"确认有没有活可干 / 清完再报告"，它容易反复 `observe`+`memory` 空转。
写成"看到就割""直接锄"这类祈使句更顺。

## 二、记忆库（教事实，也教教训）

模型每次规划都会 recall 记忆。所以：

- **教事实**：用任务文本说一遍（"记住：……"），它会记成 player 记忆。
- **剪噪声**：观察/技能记录会无限膨胀，定期剪：

```powershell
python scripts\prune-memory.py            # 干跑，只看统计
python scripts\prune-memory.py --apply    # 真删 + VACUUM
```

保留的是：`reflection / plan / intent / transition / import / note`，以及 `links`（路线知识）。
删掉的是：老的 `observation / skill / skill_call / movement`。

**改库前必须先停服务**（`stop-farm.bat`），改完再起。

## 三、动作历史（让失败动作被回避）

`_dev\action-history.json` 记录每个动作的失败次数，模型挑目标时会跳过反复失败的。
手工把"move 撞门"这类条目写进去，它下次就更倾向避开。

## 四、路线数据（从人类示范学）

```powershell
python bot\route_recorder.py --seconds 600   # 你走，它记
python bot\route_learn.py                    # 整理成 exports
```

产物 `_dev\routes\exits-learned.json`：地图 → 目标 → 出口坐标 + 拐点。
这是它唯一的"地图知识"来源。

## 课程表建议

按顺序发，一条跑 10~20 分钟再发下一条：

1. `直接清农舍 12 格内的杂草和嫩枝`
2. `把农舍南边的空地锄 6 格`
3. `把现有种子全部种下`
4. `把所有刚种下的地浇一遍水`
5. `去小镇种子店买 10 个防风草种子再回来`

每条只教一件事，跑完再换下一条——一次说太多它会在几个目标间反复横跳。

## 已知硬伤

- **游戏每 10~15 分钟挂一次**（SMAPI 崩或变僵尸），学习会被反复打断
- 它只有"记忆层面的学习"，不会改进技能实现；重复的错误要靠记忆/动作历史压住

---

## 记忆与检索（L1~L4）

| 层 | 做了什么 | 位置 |
|---|---|---|
| L1 | 中文 bigram 倒排索引，替代 LIKE '%词%' 整词匹配 | ot/memory_store.py（grams 表） |
| L2 | bigram TF-IDF 余弦 + 时间衰减（6h 半衰）+ 类型权重 的混合打分 | memory_store.query() |
| L3 | 世界知识（坐标/出口/作物/规则）每轮直接进 prompt | ot/knowledge.json + ot/knowledge.py |
| L4 | 把当天的反思压成 ≤5 条「教训」，跨存档常驻 | scripts/consolidate-memory.py → kind='lesson' |

### 日常用法

``powershell
# 每晚压缩一次当天经验（调模型，生成 5 条以内）
python scripts\consolidate-memory.py --apply --hours 24 --max-lessons 5

# 只看看有哪些候选记录（不花 token）
python scripts\consolidate-memory.py --dry-run

# 剪掉观察类噪声（先停服务）
python scripts\prune-memory.py --apply
``

### 检索打分

``
score = 0.55 × 余弦相似度 + 0.25 × 时间衰减 + 命中数加成 × 类型权重
类型权重：lesson 3.0 / instruction 2.2 / import 2.0 / reflection 1.8 / transition 1.5
          skill 1.0 / observation 0.9 / plan 0.7 / movement 0.3
``

教训（lesson）**不按存档过滤**——它是游戏机制级知识，所有存档通用。
