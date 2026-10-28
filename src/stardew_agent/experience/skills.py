"""组合技能定义：受限格式的校验与构造。

定义是纯数据，只能引用已注册的底层能力；不接受任意代码、脚本或无限循环。
首版不支持占位参数、条件分支嵌套与动态预算——需要时按版本升级格式。

字段（``schema_version=1``）::

    {
      "schema_version": 1,
      "id": "skill:water-three",
      "claim": "已耕地连续浇水三步的做法",
      "premises": [{"path": "location", "op": "==", "value": "Farm"}],
      "abilities": [{"id": "use", "version": "1.0.0"}],
      "steps": [
        {"ability": "use", "params": {"target": "...", "expect": "water"},
         "max_steps": 2, "until": {"path": "...", "op": "==", "value": true}}
      ],
      "max_steps": 12,
      "max_seconds": 120,
      "completion": {"path": "...", "op": "==", "value": true}
    }
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..contracts.errors import Rejected
from ..contracts.version import SKILL_SCHEMA_VERSION
from ..decision import predicates

MAX_STEPS = 12
MAX_STEP_LIMIT = 8
MAX_SECONDS = 300
MAX_ABILITIES = 8


class SkillDefinitionError(Rejected):
    code = "SKILL_DEFINITION"


def _require_int(value: Any, name: str, *, low: int, high: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise SkillDefinitionError("%s 必须是 %d～%d 的整数" % (name, low, high))
    return value


def validate_skill(
    definition: Mapping[str, Any] | None, *, ability_versions: Mapping[str, str] | None = None
) -> dict:
    """校验技能定义并返回规范化副本。

    ``ability_versions`` 提供时（能力 id → 版本），会核对引用的底层能力存在且版本一致，
    避免“技能写死了一个已经改过的动作”。
    """
    if not isinstance(definition, Mapping):
        raise SkillDefinitionError("技能定义必须是对象")
    if definition.get("schema_version") != SKILL_SCHEMA_VERSION:
        raise SkillDefinitionError("不支持的技能格式版本：%s" % definition.get("schema_version"))
    skill_id = definition.get("id")
    if not isinstance(skill_id, str) or not skill_id:
        raise SkillDefinitionError("技能缺少 id")
    claim = definition.get("claim")
    if not isinstance(claim, str) or not claim.strip():
        raise SkillDefinitionError("技能缺少 claim（要证明的结论）")

    premises = predicates.validate_all(definition.get("premises"))

    steps = list(definition.get("steps") or [])
    if not steps or len(steps) > MAX_STEPS:
        raise SkillDefinitionError("步骤数量必须在 1～%d" % MAX_STEPS)
    normalized_steps = []
    for index, step in enumerate(steps):
        if not isinstance(step, Mapping):
            raise SkillDefinitionError("第 %d 步必须是对象" % (index + 1))
        ability = step.get("ability")
        if not isinstance(ability, str) or not ability:
            raise SkillDefinitionError("第 %d 步缺少 ability" % (index + 1))
        params = step.get("params") or {}
        if not isinstance(params, Mapping):
            raise SkillDefinitionError("第 %d 步 params 必须是对象" % (index + 1))
        limit = step.get("max_steps", 4)
        normalized = {
            "ability": ability,
            "params": dict(params),
            "max_steps": _require_int(limit, "第 %d 步 max_steps" % (index + 1), low=1, high=MAX_STEP_LIMIT),
        }
        if step.get("until") is not None:
            normalized["until"] = predicates.validate(step["until"])
        normalized_steps.append(normalized)

    abilities = []
    for row in definition.get("abilities") or []:
        if not isinstance(row, Mapping) or not isinstance(row.get("id"), str):
            raise SkillDefinitionError("abilities 条目需要 id 与 version")
        version = str(row.get("version") or "")
        if not version:
            raise SkillDefinitionError("abilities 条目缺少 version")
        abilities.append({"id": row["id"], "version": version})
    if len(abilities) > MAX_ABILITIES:
        raise SkillDefinitionError("引用能力数量超过上限 %d" % MAX_ABILITIES)
    referenced = {str(step["ability"]) for step in normalized_steps}
    declared = {row["id"] for row in abilities}
    if not referenced <= declared:
        raise SkillDefinitionError("步骤引用了未在 abilities 里声明的能力：%s" % ", ".join(sorted(referenced - declared)))
    if ability_versions is not None:
        for row in abilities:
            actual = ability_versions.get(row["id"])
            if actual is None:
                raise SkillDefinitionError("引用的能力不存在：%s" % row["id"])
            if str(actual) != row["version"]:
                raise SkillDefinitionError(
                    "能力 %s 版本已变化（定义 %s / 当前 %s）" % (row["id"], row["version"], actual)
                )

    completion = definition.get("completion")
    return {
        "schema_version": SKILL_SCHEMA_VERSION,
        "id": skill_id,
        "claim": claim.strip(),
        "premises": premises,
        "abilities": abilities,
        "steps": normalized_steps,
        "max_steps": _require_int(definition.get("max_steps", MAX_STEPS), "max_steps", low=1, high=MAX_STEPS),
        "max_seconds": _require_int(definition.get("max_seconds", MAX_SECONDS), "max_seconds", low=1, high=MAX_SECONDS),
        "completion": predicates.validate(completion) if completion is not None else None,
    }


def build_skill(
    skill_id: str,
    claim: str,
    steps: Sequence[Mapping[str, Any]],
    *,
    abilities: Sequence[Mapping[str, str]],
    premises: Sequence[Mapping[str, Any]] = (),
    completion: Mapping[str, Any] | None = None,
    max_steps: int = MAX_STEPS,
    max_seconds: int = MAX_SECONDS,
) -> dict:
    """按受限格式构造一份技能定义（写入前仍需 :func:`validate_skill`）。"""
    definition = {
        "schema_version": SKILL_SCHEMA_VERSION,
        "id": skill_id,
        "claim": claim,
        "premises": list(premises),
        "abilities": [{"id": str(row["id"]), "version": str(row["version"])} for row in abilities],
        "steps": [dict(step) for step in steps],
        "max_steps": int(max_steps),
        "max_seconds": int(max_seconds),
        "completion": dict(completion) if completion else None,
    }
    return validate_skill(definition)
