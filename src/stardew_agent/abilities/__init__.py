"""能力层：每个能力自带描述、参数校验、前提检查、执行与结果验证。

* :mod:`base` — 前提分级、参数工具、委托基类
* :mod:`registry` — 静态注册与统一入口
* :mod:`navigation` — ``move_to``
* :mod:`interaction` — ``select_item`` / ``use`` / ``interact`` / ``choose_menu_option``
* :mod:`routines` — ``farm_routine``（浇水/锄地/收获/播种/装水的本地批量执行）
* :mod:`fishing` — ``fish``（委托独立钓鱼 Mod）
* :mod:`composite` — 组合技能执行
* :mod:`verification` — 前后观察差分证据
"""

from .base import AbilitySpec, DelegationBase, PremiseCheck, blocking_premises, raise_for_premises
from .composite import CompositeDelegation, composite_ability
from .fishing import FishingDelegation, FishingPort, fish_ability
from .interaction import (
    ActionPort,
    EntityActionDelegation,
    MenuDelegation,
    SelectDelegation,
    interact_ability,
    menu_ability,
    select_item_ability,
    use_ability,
)
from .navigation import MoveDelegation, MovePort, move_ability
from .registry import AbilityRegistry
from .routines import FarmRoutineDelegation, farm_routine_ability
from .verification import inventory_delta, position_delta, scene_changed, tile_delta

__all__ = [
    "AbilityRegistry",
    "AbilitySpec",
    "ActionPort",
    "CompositeDelegation",
    "DelegationBase",
    "EntityActionDelegation",
    "FarmRoutineDelegation",
    "FishingDelegation",
    "FishingPort",
    "MenuDelegation",
    "MoveDelegation",
    "MovePort",
    "PremiseCheck",
    "SelectDelegation",
    "blocking_premises",
    "composite_ability",
    "farm_routine_ability",
    "fish_ability",
    "interact_ability",
    "inventory_delta",
    "menu_ability",
    "move_ability",
    "position_delta",
    "raise_for_premises",
    "scene_changed",
    "select_item_ability",
    "tile_delta",
    "use_ability",
]
