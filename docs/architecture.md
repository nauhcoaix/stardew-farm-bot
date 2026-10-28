# 架构

## 目标

模型负责“想要什么、下一步做什么”，本地执行器负责“在授权范围内安全地做，并如实报告
结果”。这样既能保留自主性，又不把游戏操作、预算、验证和记忆耦合进同一条大流程。

## 依赖方向

```text
contracts（纯数据 + 错误 + 版本）
   ↑
observation（纯函数）      exploration（纯函数）     repeat_guard（纯函数）   embeddings（本地模型）
   ↑
bridge_client / fishing_client / model_client（外部 I/O）
   ↑
experience（证据、经验、技能版本、地图索引）
   ↑
decision（上下文、判据、解析、新鲜度）
   ↑
abilities（参数检查、前提、执行、验证）
   ↑
runtime（预算、所有权、停止、事件、循环）
   ↑
cli（配置、组装、命令）  ← 唯一同时接触全部模块的地方
```

规则：

- 下层不导入上层，模块不反向导入主程序；
- 纯计算函数不打开数据库、不发请求、不控制游戏；
- 跨模块读取只通过公开函数，不读别的模块的表结构或可变全局状态；
- 资源（连接、SQLite、模型会话）由 `cli/wiring.py` 创建并释放。

## 一次运行的时序

```text
cli.run_agent
  ├─ 读取配置（预算/限频/路径/长期目标）并清空停止文件
  ├─ 组装：Bridge、ChatClient、ExperienceStore、AbilityRegistry、AgentLoop
  └─ AgentLoop.run
       ├─ observe：桥快照 → Observation，并把地形写入该存档的地图索引
       ├─ request_decision（委托结算后发起，最多一个在途请求）
       │    ├─ 读取人类指令收件箱 → 上下文 instructions（并写 delivered 回执）
       │    ├─ 检索经验（本存档事实 + 已验证机制）
       │    ├─ build_context（截断必须报告）→ 模型 → parse_decision
       │    ├─ followed_instructions → inbox.follow（done）
       │    └─ 计数模型请求与 tokens
       ├─ 执行前新鲜度检查：场景/菜单/关键前提/绑定观察
       ├─ 限频检查（动作键 = 能力 + 规范参数）
       ├─ 取得动作所有权 → 启动委托 → 分段 step
       │    └─ 每步之后重新观察；完成判据只能否决成功，不能升级失败
       ├─ 结算：ActionResult + 事件（证据落库 → 经验/技能更新）
       └─ 停止/预算用尽：取消在途委托、释放所有权、输出已发生的结果
```

## 关键不变量

| 不变量 | 实现位置 |
|---|---|
| 同一时刻只有一个执行器操纵角色 | `runtime/ownership.py`，游戏桥与钓鱼能力共用 |
| 写入前核对现场，未知不当作禁止 | `decision/freshness.py`、`abilities/base.py` |
| 临时 ref 不跨快照直接执行 | `decision/binding.py`：执行前按决策时的观察改绑为坐标或当前 ref |
| 写操作超时是结果未知 | `bridge_client`（`ResultUnknown`）、能力把未知如实返回 |
| 完成判据只能否决成功 | `runtime/loop.py` 的 `_finish`（模型判据默认关闭，见 `model_completion_enabled`） |
| 机械动作不经模型 | `AbilitySpec.exposed=False`（`use`/`select_item`）+ `validate_model_params` 拒绝；由 `farm_routine` 内部调用 |
| 剩余目标本地续跑 | `loop._continuation_decision`：`routine_continuation` 命中时不再请求模型，最多 `max_auto_continue` 次 |
| 收获不挥工具 | `farm_routine` 的 `harvest` 走 `interact`（桥的 `use` 只挥当前工具/种子） |
| 上下文按预算压缩 | `decision/context.py`：地块→通行网格，实体只留非默认字段，候选去重 |
| 限频不把“有进展的续跑”算成新动作 | 只有新委托下发时 `RepeatGuard.record` |
| 经验状态不会被相似度或模型自信提升 | `experience/store.py`（显式状态变更 + 晋级判据；`status=verified` 必须带证据） |
| 经验只由真实执行写入/升级 | `experience/learning.py`：只学 completed/failed，3 次可区分成功才 verified，反例退回 candidate |
| 人类指令不能绕过校验 | 指令只是上下文数据；能力参数、前提、限频、所有权仍照常检查 |
| 交互后 CanMove 卡死会自愈 | `PlayerRecovery`：无菜单/事件/切图/动画等明确阻塞时，连续 2 帧后 `forceCanMove` |
| 存档事实不跨存档 | 所有索引表带 `save_key`；`scope='save'` 的经验必须带存档 |
| 换存档不继承动作历史 | `loop._note_context`：清空 `recent_actions`/`last_result` 并记 `context_switched`；`make_decider` 归零短期目标 |
| 一次逻辑委托只有一个动作 ID | 能力把 `delegation.action_id` 传给 `actionId`（`navigation`/`interaction`），桥按连接串行 |

## 数据所有权

| 事实 | 唯一拥有者 |
|---|---|
| 当前观察 | `runtime`（每次重新观察，不缓存旧现场） |
| 动作所有权/租约 | `runtime/ActionOwnership` |
| 预算账本 | `runtime/RunBudget` |
| 事件与经验/技能版本/地图索引 | `experience/ExperienceStore`（写入端：`experience/learning.py`） |
| 人类指令与回执 | `runtime/HumanInbox`（CLI 只创建指令文件；运行进程只写 `ack.json`） |
| 连接句柄 | `bridge_client.Bridge`、`fishing_client` 的文件邮箱 |
| 决策新鲜度 | `runtime` 持有“决策依据的观察”，`decision` 只做比较 |

## 已知能力缺口

- 快照没有玩家生命值与敌人状态 → 挖矿/战斗不能声称已掌握；需要先扩展 Mod 感知
  （`mods/game_bridge_mod/Observations.cs`）与协议。
- 只有当前观察范围的地图 → 跨地区只能依赖已记录连接；无全局地图。
- 购买、制造、箱子等扩展写操作没有统一动作 ID 保障 → 暂不暴露为默认能力。
- 组合技能首版不支持参数占位、条件分支嵌套与动态预算。
- 经验粒度只到“地点 + 能力 + 关键参数”（例如 `farm_routine task=water`），还没有参数级/时段前提；
  派生技能需要一次真实取消作为停止测试，否则停在候选（不会自动晋级）。
- 人类指令在**下一轮决策**生效，不会打断正在执行的动作；指令过期后不会补做。

这些缺口必须在文档和上下文中如实呈现，不能用提示词包装成“已支持”。
