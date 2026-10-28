"""契约版本。

数据记录里持久化的 ``schema_version`` 指向这里；改动字段含义时必须递增，
并在 ``docs/interfaces.md`` 记录兼容策略。版本号不随包版本自动变化。
"""

#: 观察 / 决策 / 动作结果 / 经验 / 组合技能的记录版本
SCHEMA_VERSION = 1

#: 游戏桥 WebSocket 协议版本（与 mods/game_bridge_mod/Protocol.cs 对应）
BRIDGE_PROTOCOL_VERSION = 1

#: 独立钓鱼 Mod 状态文件版本（mods/fishing_mod/ModEntry.cs）
FISHING_STATUS_VERSION = 1

#: 组合技能定义的受限格式版本
SKILL_SCHEMA_VERSION = 1
