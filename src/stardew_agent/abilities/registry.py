"""能力注册表。

新能力通过注册自己的描述、参数校验、前提检查与执行函数接入，主循环不增加业务分支。
首版静态注册：不实现热加载或远程插件市场。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from ..contracts.ability import AbilityDescriptor
from ..contracts.decision import AbilityCall
from ..contracts.errors import ConfigError, Rejected
from ..contracts.observation import Observation
from ..contracts.results import Delegation
from .base import AbilitySpec, PremiseCheck, raise_for_premises


class AbilityRegistry:
    """按 id 查找能力；所有对外行为都经过参数校验与前提检查。"""

    def __init__(self, specs: Iterable[AbilitySpec] = ()) -> None:
        self._specs: dict[str, AbilitySpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: AbilitySpec) -> None:
        if spec.id in self._specs:
            raise ConfigError("能力 id 重复注册：%s" % spec.id)
        self._specs[spec.id] = spec

    def unregister(self, ability_id: str) -> None:
        self._specs.pop(ability_id, None)

    def known(self, ability_id: str) -> bool:
        return ability_id in self._specs

    def get(self, ability_id: str) -> AbilitySpec:
        spec = self._specs.get(ability_id)
        if spec is None:
            raise Rejected("未知能力 %s（可用：%s）" % (ability_id, ", ".join(sorted(self._specs)) or "无"))
        return spec

    def descriptors(self) -> list[AbilityDescriptor]:
        """给模型看的能力目录：只包含 ``exposed=True`` 的能力。"""
        return [
            self._specs[key].descriptor
            for key in sorted(self._specs)
            if self._specs[key].exposed
        ]

    def all_descriptors(self) -> list[AbilityDescriptor]:
        """包含隐藏能力的完整目录，用于诊断与本地执行器。"""
        return [self._specs[key].descriptor for key in sorted(self._specs)]

    def model_visible(self, ability_id: str) -> bool:
        spec = self._specs.get(ability_id)
        return bool(spec is not None and spec.exposed)

    def validate_model_params(self, ability_id: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        """模型决策专用：隐藏能力直接拒绝，避免绕过例行程序逐格下指令。"""
        spec = self._specs.get(ability_id)
        if spec is None:
            visible = sorted(key for key, row in self._specs.items() if row.exposed)
            raise Rejected("未知能力 %s（模型可直接调用：%s）" % (ability_id, ", ".join(visible) or "无"))
        if not spec.exposed:
            visible = sorted(key for key, row in self._specs.items() if row.exposed)
            raise Rejected(
                "能力 %s 是底层机械动作，不直接对模型开放；请用高层能力（%s）"
                % (ability_id, ", ".join(visible) or "无")
            )
        return spec.validate(params)

    def validate_params(self, ability_id: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        """供决策解析使用：能力不存在或参数非法时抛 ``Rejected``。"""
        return self.get(ability_id).validate(params)

    def check_premises(self, call: AbilityCall, observation: Observation) -> list[PremiseCheck]:
        return list(self.get(call.ability).check_premises(call.params, observation))

    def predicates(self, call: AbilityCall, observation: Observation) -> Sequence[Mapping[str, Any]]:
        """供计划新鲜度使用的前提判据（字段级、可求值）。"""
        return self.get(call.ability).predicates(call.params)

    def start(self, call: AbilityCall, observation: Observation, *, action_id: str = "") -> Delegation:
        """启动一次委托；参数与前提都在这里统一校验。"""
        spec = self.get(call.ability)
        params = spec.validate(call.params)
        checks = list(spec.check_premises(params, observation))
        raise_for_premises(checks)
        return spec.start(params, observation, action_id)

    def snapshot(self) -> dict:
        return {
            "abilities": [descriptor.to_dict() for descriptor in self.all_descriptors()],
            "model_visible": sorted(key for key, spec in self._specs.items() if spec.exposed),
        }
