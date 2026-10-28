"""能力描述契约。

能力描述是模型能看到的“操作接口说明”，必须说清参数、观察需求、真实前提、
作用范围和完成判据。尚不确定的信息要显式写成未知，不能编造可用性。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class AbilityDescriptor:
    id: str
    version: str
    summary: str
    params: Mapping[str, str] = field(default_factory=dict)
    observation_needs: tuple[str, ...] = ()
    scope: str = ""
    completion: str = ""
    cancellable: bool = True
    limits: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "version": self.version,
            "summary": self.summary,
            "params": dict(self.params),
            "observation_needs": list(self.observation_needs),
            "scope": self.scope,
            "completion": self.completion,
            "cancellable": self.cancellable,
            "limits": list(self.limits),
        }
