# 贡献指南

## 安装与模块检查

```powershell
python -m pip install -e ".[dev]"     # Python 3.10+，推荐 3.12
python -m pytest                       # 离线测试，不需要游戏、桌面或模型账户
python -m stardew_agent --help         # CLI 可用性
```

纯计算与替身测试不依赖 Windows 桌面；向量模型是可选项（`pip install -e ".[embeddings]"`）。
未安装向量模型时检索退回关键词 2-gram，`Encoder.status()` 明确报告不可用。

## 实机检查清单

在声称“能在游戏里做到”之前，逐项确认并保留证据：

1. 两个 Mod 已编译并安装：`mods/game_bridge_mod` → `Mods/AgentStardew`，
   `mods/fishing_mod` → `Mods/StandaloneFishingSkill`。
2. 游戏载入指定测试存档，游戏桥监听 `ws://127.0.0.1:17654/`；
   `python -m stardew_agent doctor --config config.json` 显示 `bridge.connected=true`、
   `fishing.installed=true`。
3. 用专用测试存档运行；允许失败、浪费体力、倒下，但调试传送/改规则不得计入学习成绩。
4. 运行后检查 `state.json` 的 `summary`（completed/failed/unknown/cancelled/blocked）、
   `recorder.metrics`（延迟、丢弃次数、空等时长）与 `data/experience.sqlite3` 里的动作事件。
5. 停止测试：运行中执行 `stop`，确认在途委托被取消、`ownership` 释放、没有新的写操作下发。
6. 重启复用测试：重启进程后确认技能仍为 `verified`、经验可检索，且不会重放旧动作队列。

报告时区分三类结论：程序测试通过 / 实机行为通过 / 学习效果有改善。只有第三类需要
`evals/` 的配对试验，且必须附全部尝试记录。

## 新增一个能力

能力 = 描述 + 参数校验 + 前提检查 + 启动函数（+ 可选字段判据）。以“打开背包里某个槽位
并确认”以外的例子为例，新增一个 `drop_item`（丢弃一件物品）大致如下：

```python
# src/stardew_agent/abilities/inventory.py
from ..contracts.ability import AbilityDescriptor
from ..contracts.observation import Observation
from .base import AbilitySpec, DelegationBase, PremiseCheck, reject_unknown_params, require_int
from ..contracts.results import Progress

class DropDelegation(DelegationBase):
    def __init__(self, port, params, observation, *, clock):
        super().__init__("drop_item", params, clock=clock)
        self.port = port
        self.slot = int(params["slot"])
        self.before = {item.get("slot"): item.get("count") for item in observation.inventory}

    async def step(self, observation):
        if self.cancelled:
            return self.cancelled_progress()
        if self.steps:
            after = {item.get("slot"): item.get("count") for item in observation.inventory}
            if after != self.before:
                return self.finish("completed", "背包数量变化", before=self.before, after=after)
            return self.finish("unknown", "接口已下发，但背包没有可验证变化")
        self.steps += 1
        await self.port.drop(self.slot)      # 端口需要先在 mod 与桥协议里实现
        return Progress("running", "已下发丢弃请求")

def drop_item_ability(port, *, clock=time.monotonic):
    descriptor = AbilityDescriptor(
        id="drop_item", version="1.0.0", summary="丢弃指定背包槽位的一件物品",
        params={"slot": "背包槽位 0～11"}, observation_needs=("inventory",),
        scope="只丢弃模型指定的槽位", completion="重新观察到的背包数量发生变化",
    )

    def validate(params):
        reject_unknown_params(params, ("slot",))
        return {"slot": require_int(params, "slot", low=0, high=11)}

    def premises(params, observation):
        return [PremiseCheck("user", None, "是否允许丢弃由用户限制决定（未配置时保持未知）")]

    def start(params, observation, action_id):
        delegation = DropDelegation(port, params, observation, clock=clock)
        delegation.action_id = action_id or delegation.action_id
        return delegation

    return AbilitySpec(descriptor=descriptor, validate=validate, check_premises=premises, start=start)
```

接入步骤：

1. 在 `mods/game_bridge_mod` 与协议里实现真正的游戏操作（新增能力若需要新写命令，必须同时
   定义协调、重试与结果核验语义）；
2. 在 `cli/wiring.build_registry` 注册；
3. 在 `tests/` 增加：参数非法、前提不满足、成功、无变化（unknown）、取消；
4. 更新 `docs/interfaces.md` 与 `docs/migration.md`。

### 要不要让模型直接调用？

默认 `AbilitySpec.exposed=True`，会出现在模型能力目录里。以下情况应设 `exposed=False`：

- 它是机械、重复、逐格的底层动作（`use`、`select_item`），模型只需要给高层任务；
- 它只该由 `farm_routine` 或组合技能在内部调用；
- 暴露它会让模型绕过前提检查或产生大量没有决策价值的请求。

隐藏后用 `AbilityRegistry.all_descriptors()` 仍能查到它，`registry.start()` 仍可启动；
`registry.validate_model_params()` 会拒绝模型下发（错误信息里会列出当前可见能力）。
需要临时对比旧行为时用 `expose_mechanics=true` 或 `run --expose-mechanics`，不要写进默认配置。

不要在 `runtime/loop.py` 里加业务分支；主循环只处理统一生命周期。

## 修改接口的流程

- 数据记录字段变化 → 递增 `contracts/version.py` 中的 `SCHEMA_VERSION` 或对应版本，
  并在 `docs/interfaces.md` 写兼容策略；
- 能力 id/版本变化 → 引用它的组合技能会因版本不符被 `validate_skill` 拒绝，需要新版本；
- 提示词变化 → 必须保持“不注入攻略/日程/路线”，并检查 `provided_knowledge` 是否同步。

## 修改记录（提交前必做）

`agent修改要求.md` 要求每次修改都在 `修改记录.md` 追加一条记录，并在回复/提交总结里
列出 **修改的文件** 与 **修改逻辑**。提交前逐项确认：

- [ ] `修改记录.md` 顶部新增一条记录，写清：修改的文件（含测试与文档）、修改逻辑、验证方式；
- [ ] 记录与提交一一对应：多条提交就多条记录，不要合并成一句“若干修复”；
- [ ] 记录标题与提交信息保持一致，方便用 `git log --oneline` 对照；
- [ ] 回复总结里重复“修改的文件 + 修改逻辑”，不要只说“已修复”；
- [ ] 新增/删除文件、配置项或 CLI 参数时，同时更新 `README.md` 与 `docs/interfaces.md`；
- [ ] 只改文档或测试也要记录（它们同样属于修改）。

记录模板：

```md
## 2026-01-01 · `<提交短哈希>` 一句话说明改了什么

**修改文件**
- `src/...`（新增/修改/删除）
- `tests/...`
- `docs/...`

**修改逻辑**
- 原来的行为 → 现在为什么这样改 → 边界与失败语义

**验证**
- `python -m pytest` 结果；实机/仿真证据（如有），注明是程序测试还是实机行为
```

## 提交与证据

- 提交前跑 `python -m pytest`，并确认 `修改记录.md` 已追加本次条目；
- 影响实机行为的改动附上 `doctor` 输出要点、`state.json` 片段与相关事件摘要
  （用 `export` 导出后脱敏）；
- 不提交 `config.json`、`model.json`、`data/`、`models/`、个人存档或密钥。
