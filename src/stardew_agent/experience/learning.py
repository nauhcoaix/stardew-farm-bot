"""记忆库写入端：把真实动作结果变成经验，并在重复证据下升级。

规则（与 ``verdict`` 一致）：

* 只有 ``completed`` / ``failed`` 构成证据；``unknown`` / ``cancelled`` 不写经验；
* 一条经验 = 主张（地点 + 能力 + 任务）+ 证据事件；同一主张合并证据；
* 同一存档内 3 次可区分的真实成功 → ``verified``；已验证经验再遇到失败 → 退回 ``candidate``；
* ``farm_routine`` 的重复成功会生成候选技能（仍需取消测试才能晋级）；
* 模型的总结、相似度或自信都不能在这里提升状态——只有执行事件可以。
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

from .skills import build_skill
from .store import ExperienceStore

#: 值得固化成经验的能力：移动/单步交互由能力自身与寻路保证，写进经验只会变成噪声。
LEARNABLE_ABILITIES = frozenset(
    {
        "farm_routine",
        "buy_item",
        "fish",
        "craft_item",
        "inventory_action",
        "explore_area",
    }
)

#: 达到这个数量的可区分成功才升级为 verified
VERIFY_SUCCESSES = 3


def derived_skill_id(location: str, task: str) -> str:
    """farm_routine 派生技能的稳定 id（同一地点 + 任务共用一份候选）。"""
    digest = hashlib.sha1(("%s|%s" % (location or "", task)).encode("utf-8")).hexdigest()[:8]
    return "skill:farm_routine." + digest


def record_skill_feedback(
    store: ExperienceStore,
    *,
    location: str,
    task: str,
    event_id: str,
    state: str,
) -> str | None:
    """把真实 farm_routine 的取消/失败反馈记到派生技能上。

    取消事件是晋级所需的“停止测试”证据；满足前提仍失败则把已验证技能退回候选。
    """
    if not task:
        return None
    skill_id = derived_skill_id(location, task)
    current = store.skill(skill_id)
    if current is None:
        return None
    version = int(current["version"])
    store.record_skill_run(
        skill_id, version, event_id, state, cancelled_cleanly=state == "cancelled"
    )
    if state == "cancelled":
        store.promote_skill(skill_id)
    elif state == "failed" and current["status"] == "verified":
        store.demote_skill(skill_id, version, "满足前提仍失败，退回候选并要求新证据")
    return skill_id


def _param_tag(ability: str, params: Mapping[str, Any]) -> str:
    """只取最能区分经验的参数，避免同一句话被不同参数复用。"""
    if ability == "farm_routine":
        task = str(params.get("task") or "").strip()
        return " task=" + task if task else ""
    for key in ("action", "recipe", "item", "itemId"):
        value = params.get(key)
        if value not in (None, ""):
            return " %s=%s" % (key, str(value)[:24])
    return ""


def claim_for(*, location: str, ability: str, params: Mapping[str, Any], state: str) -> str:
    """生成可读、可去重的主张文字（不塞坐标，否则每条经验都只能用一次）。"""
    verb = {"completed": "能完成", "failed": "会失败"}.get(state, "结果未知")
    return "%s：%s%s %s" % (location or "未知地点", ability, _param_tag(ability, params), verb)


def learn_action(
    store: ExperienceStore,
    *,
    event_id: str,
    save_key: str,
    location: str,
    ability: str,
    state: str,
    params: Mapping[str, Any] | None = None,
    ability_versions: Mapping[str, str] | None = None,
) -> dict:
    """把一次动作结果写入经验库；返回本次学习摘要（供日志/测试）。"""
    params = dict(params or {})
    if state not in ("completed", "failed"):
        return {"learned": False, "reason": "unknown/cancelled 不作为经验证据"}
    if ability not in LEARNABLE_ABILITIES and not ability.startswith("skill:"):
        return {"learned": False, "reason": "该能力不做经验固化（移动/单步由能力自身保证）"}
    if not save_key:
        return {"learned": False, "reason": "缺少 save_key，无法按存档隔离经验"}

    claim = claim_for(location=location, ability=ability, params=params, state=state)
    success = state == "completed"
    if not success:
        #: 失败优先作为已有“能完成”经验的反例，而不是另建一条“会失败”的经验。
        success_claim = claim_for(location=location, ability=ability, params=params, state="completed")
        prior = store.experience_by_claim(success_claim, scope_save=save_key)
        if prior is not None:
            claim = success_claim
    existing = store.experience_by_claim(claim, scope_save=save_key)
    created = upgraded = demoted = False

    if existing is None:
        premises = [{"path": "location", "op": "==", "value": location}] if location else []
        record = store.add_experience(
            claim,
            kind="action",
            premises=premises,
            source="executor",
            confidence="execution",
            scope="save",
            scope_save=save_key,
            evidence_ids=(event_id,) if success else (),
            counterexample_ids=() if success else (event_id,),
            status="candidate",
        )
        experience_id = record.id
        created = True
    else:
        experience_id = existing["id"]
        store.add_evidence(experience_id, event_id, counterexample=not success)
        counts = store.evidence_counts(experience_id)
        if success and existing["status"] == "candidate" and counts["support"] >= VERIFY_SUCCESSES:
            store.set_experience_status(
                experience_id, "verified", note="%d 次可区分的真实执行成功" % counts["support"]
            )
            upgraded = True
        elif not success and existing["status"] == "verified":
            store.set_experience_status(
                experience_id, "candidate", note="满足前提仍失败，退回候选并要求新证据"
            )
            demoted = True

    counts = store.evidence_counts(experience_id)
    current = store.experience(experience_id) or {}
    skill_id = None
    if success and counts["support"] >= VERIFY_SUCCESSES:
        skill_id = _ensure_skill(
            store,
            location=location,
            ability=ability,
            params=params,
            ability_versions=dict(ability_versions or {}),
            experience_id=experience_id,
        )
    return {
        "learned": True,
        "claim": claim,
        "experience_id": experience_id,
        "status": current.get("status", "candidate"),
        "support": counts["support"],
        "counter": counts["counter"],
        "created": created,
        "upgraded": upgraded,
        "demoted": demoted,
        "skill_id": skill_id,
    }


def _ensure_skill(
    store: ExperienceStore,
    *,
    location: str,
    ability: str,
    params: Mapping[str, Any],
    ability_versions: Mapping[str, str],
    experience_id: str,
) -> str | None:
    """farm_routine 重复成功后登记候选技能；已存在同 id 技能就不重复创建。"""
    if ability != "farm_routine":
        return None
    version = ability_versions.get("farm_routine")
    task = str(params.get("task") or "").strip()
    if not version or not task:
        return None
    skill_id = derived_skill_id(location, task)
    if store.skill(skill_id) is not None:
        return skill_id
    try:
        definition = build_skill(
            skill_id,
            claim="%s：farm_routine task=%s 的可复用流程（来自真实执行证据）" % (location or "未知地点", task),
            steps=[{"ability": "farm_routine", "params": {"task": task}, "max_steps": 1}],
            abilities=[{"id": "farm_routine", "version": version}],
            premises=[{"path": "location", "op": "==", "value": location}] if location else [],
            max_steps=1,
            max_seconds=300,
        )
        store.add_skill(definition, ability_versions=ability_versions, status="candidate")
        for support_id in store.experience_evidence_ids(experience_id):
            store.record_skill_run(skill_id, 1, support_id, "completed")
        store.promote_skill(skill_id)  # 缺取消测试时保持候选，不会误升级
    except Exception:  # noqa: BLE001 - 技能生成失败不能影响动作结算
        return None
    return skill_id
