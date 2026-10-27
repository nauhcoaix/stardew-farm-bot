# 从预设农务改为模型自主行动

当前主服务使用“能力目录 → 模型选择 → 执行 → 现场复核 → 统一记忆 → 再选择”的循环。模型收到玩家意图、可用技能和前提、现场目标、探索候选、相关记忆及上次结果。它决定眼下的小目标和下一组行动；执行器负责合法参数、当前目标、真实操作与结果验证。

模型可选 memory、explore、observe、move、travel、interact、select、use、menu、menu_click、menu_close、refill、till、plant、harvest、water、till_plant、clear、chop、buy_seeds、sell、store、sleep、fish、wait。目录位于 `bot/skill_catalog.py`，每项有说明、参数、available和不可用原因。不可用通常表示缺少前提，模型可以先找水、找工具、走近对象或探索。

## 统一记忆格式

采用 SQLite 的 `memory.sqlite3`，而非分散的“路线文件、工具经验文件、当天计划文件”。SQLite便于按存档、时间、类型和对象检索，事务保证中断后已写记录完整；JSONL用于导出和交换。Python内置sqlite3，无新增依赖。

所有事件共用以下信封，data按事件类型承载内容：

```json
{
  "schema_version": 1,
  "id": "事件ID",
  "save_id": "save:真实存档ID/player:玩家ID",
  "session_id": "本次游戏实例:代数",
  "recorded_at": "UTC时间",
  "game_time": {"year": 1, "season": "spring", "day": 1, "time": 900},
  "kind": "skill",
  "source": "executor",
  "confidence": "observed",
  "status": "failed",
  "location": "Farm",
  "actor": {"x": 5, "y": 4},
  "parent_id": "本次模型选择事件ID",
  "evidence": ["现场及底层调用事件ID"],
  "data": {"skill": "explore", "args": {}, "target": {}, "result": {"detail": "实际失败原因"}}
}
```

这是格式示例，不代表已经在真实存档里进行该试验。

| 内容 | 事件/索引 | 含义 |
|---|---|---|
| 看到什么、人物资源、菜单 | observation / tiles | 只保存有变化的地块；未观察区域保持未知 |
| 走过哪些坐标 | position、movement、trace_gap / visited | 原生位置样本按序号去重，采样缺口明确标记；不猜中间路线 |
| 地区出口与进入新地图 | transition / links | 可见出口与真实过图形成连接；保留方向和触发对象 |
| 模型想做什么 | intent、plan | 假设和选择，不能当完成证据 |
| 技能与底层调用结果 | skill、skill_call、skill_target | 成功、失败、无进展、原因及前后现场 |
| 玩家要求和模型反思 | instruction、reflection | 前者reported，后者hypothesis；反思可引用证据ID |

source与confidence用于区分游戏事实、执行记录、玩家陈述和模型假设。模型写入的记录始终是hypothesis；无法引用另一存档的事件作为证据。地图与连接表只是同一记忆的检索索引，实际证据仍在events中。

记忆按真实saveId和playerId隔离，跨日及重启保留。AgentStardew桥0.1.1增加这两个只读字段、菜单identity及原生位置轨迹。轨迹保留最近256个变位点，Python按会话/序号去重；高层农务内部走动也记录实际经过的位置。超出缓冲或未收到的中间格记为缺口，不补猜测路线。使用旧桥时显式退化为会话隔离，不会把日期相似的两个存档合并。旧动作缓存会迁移；无法证明归属的记录进入unassigned-legacy命名空间，供人工查看，不用于当前地图规划。

## memory与explore

memory支持按文字、类型和区域检索，以及保存带证据的笔记。每次模型请求只携带有限摘要；需要更早的经历时由模型继续查询。大结果保存在库中，避免把整段历史递归塞入下一次提示。

explore有路径、交互和功能试验三种模式。模型选目的地或对象，寻路器根据已观察/记忆地形计算路径，每段最多3格；执行后重新观察。前沿是“可达的已知格子旁边存在未知区域”，来自现场计算，不是写死的坐标列表。进入门或出口后会学习真实连接，travel只使用这些连接，Farm与FarmHouse保持不同地点，不猜回程。

功能探索可以试“拿当前工具作用于这个对象”，也可以先交互查看菜单，再用observe发现控件和商品。失败会给出真实原因并记住，同一未变化目标不会盲目重试。换了工具、目标状态、起点或相关地图知识后可以重新评估。

## 行为变化

- 本地固定农务优先级、固定购买/清障日程、自动返回农场、固定地点补水、节日自动参加及自动建箱子已退出主服务。
- 收获、播种、卖货、睡觉分别选择；收获后不自动补种或卖掉。买东西需要已打开的商店及当前商品index和名称，卖货需要明确槽位。
- 到水边与钓鱼分别选择；独立钓鱼附加模块0.4.0保留，模型选fish后只在当前站位钓一竿，服务负责串行移交控制。
- 菜单由模型处理。menu使用当前选项ID；menu_click使用observe实际发现的控件ID并重新验证，menu_close需明确选择。观察失败时不盲点屏幕。
- 背景模型仍只有一个在途请求，一次最多6步。模型思考时继续已经明确委托的技能片段；失败会清掉依赖旧前提的后续步骤，并丢弃失败前已发出的后台规划，再依据结果重新选择。
- 没有模型配置或请求失败时会显示等待/退避，不悄悄切回预设日程。没有选择过的新行动不会执行。

旧farm-state/action-history/decisions文件继续作为兼容缓存或报表接口；新的权威记忆写入统一库。`places.py`和旧独立脚本仍可作为历史代码保留，新的运行时不引用预设区域图。

## 查看与启用

正常退出游戏后更新AgentStardew至本次编译的0.1.1，再重启SMAPI和农务服务；无需修改模型密钥配置。本机游戏目录的bot通过目录联接指向本仓库，Python源码更新已经同步，进程仍需重启加载。此次检查时游戏正在运行，已加载的桥仍为0.1.0，因此没有替换运行中的DLL。

在仓库目录执行以下命令安装。脚本要求游戏已经退出，备份旧桥到游戏目录的agent-update-backups，再只替换并校验AgentStardew.dll和manifest.json；保留独立钓鱼0.4.0、用户配置和存档。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install-agent-bridge.ps1
```

```powershell
python bot/farm_service.py --loop
python bot/memory_cli.py stats
python bot/memory_cli.py recall --scope "stats返回的save_id" --kind movement
python bot/memory_cli.py export --scope "stats返回的save_id" --out memory-export.jsonl
```

通过原inbox/task入口给目标即可，例如“了解附近有哪些可交互对象，记住走过的路”“找到一个能钓鱼的地方，缺少路线时先探索”。数量、物品和不卖货等限制会随玩家意图传给模型。SQLite库和导出文件按运行数据忽略，不提交存档或私人游戏记忆。

首版检索采用结构化条件和文字匹配，未引入向量库。事实会过时，出发前用现场重新验证；未观察到的路不保证可达。接口和离线测试通过不等于模型已在真实游戏中学会所有策略，需要后续用专用测试存档检验行为质量。

## 本次验证与后续发展

本次57项主流程离线测试、5项现有钓鱼客户端测试通过；memory/explore技能格式校验和Python编译检查通过。测试覆盖持久记忆、存档隔离、轨迹去重、探索移动、记忆中的门口动态寻路、目标重新绑定、重复失败阻止、换工具重试、菜单现场验证、失败后废弃后台旧规划，以及没有模型选择时不强制回家/睡觉/钓鱼。桥接0.1.1构建成功，0错误，保留原工程Netcode引用未解析的1条警告。未调用真实模型接口，也未对用户存档进行游戏行为测试。安装脚本已用本机Windows PowerShell完成只读检查。

可以沿三个方向继续发展，但不把它们变成固定日程：

1. 让经验成为可验证知识：从多次成功/失败事件归纳“什么前提下什么动作有效”，每条规则保留证据、反例和适用范围。模型笔记不能直接升级成事实。
2. 给探索提供可比较的价值：候选附带未知程度、预期能回答的问题、移动与体力成本，由模型权衡；系统提供信息，不规定今天必须探索哪里。
3. 从重复成功中学习新技能：将经过验证的交互步骤提炼为带参数、前提、完成判据和中断条件的技能；路线仍根据当时地图求解，不固化成某个存档的坐标脚本。
