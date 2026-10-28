"""受限条件表达式：模型只能提出字段判据，运行环境负责校验与求值。

允许：少量比较运算符 + 观察契约内的点路径 + JSON 字面量。
禁止：函数调用、属性写法、任意代码、越界路径。

求值结果三态：``True`` / ``False`` / ``None``（字段未知）。未知不等于不满足：
调用方不能把“没看到”当成“为否”。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from ..contracts.errors import Rejected
from ..contracts.observation import FIELD_ROOTS, Observation

#: 支持的运算符
OPS = ("==", "!=", "<", "<=", ">", ">=", "in", "not_in", "exists", "absent")

#: 允许的路径字符（只允许字母数字下划线与点）
_PATH_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.")

MAX_PREDICATES = 12


class PredicateError(Rejected):
    code = "PREDICATE"


@dataclass(frozen=True)
class Evaluation:
    """一次判据求值。``met`` 为 ``None`` 表示当前观察无法判定。"""

    met: bool | None
    reason: str = ""

    def to_dict(self) -> dict:
        return {"met": self.met, "reason": self.reason}


def validate(predicate: Mapping[str, Any] | None) -> dict:
    """校验并规范化一条判据；非法时抛 :class:`PredicateError`。"""
    if not isinstance(predicate, Mapping):
        raise PredicateError("判据必须是对象")
    path = predicate.get("path")
    op = predicate.get("op")
    if not isinstance(path, str) or not path:
        raise PredicateError("判据缺少 path")
    if not set(path) <= _PATH_CHARS:
        raise PredicateError("判据 path 含非法字符：%s" % path)
    if ".." in path or path.startswith(".") or path.endswith("."):
        raise PredicateError("判据 path 不能包含空段或上级跳转：%s" % path)
    root = path.split(".", 1)[0]
    if root not in FIELD_ROOTS:
        raise PredicateError("判据 path 越界：%s（允许的根字段：%s）" % (path, ", ".join(FIELD_ROOTS)))
    if op not in OPS:
        raise PredicateError("不支持的运算符：%s" % op)
    if op in ("exists", "absent"):
        return {"path": path, "op": op}
    if "value" not in predicate:
        raise PredicateError("判据 %s %s 缺少 value" % (path, op))
    value = predicate.get("value")
    if not _is_literal(value):
        raise PredicateError("判据 value 必须是 JSON 字面量或字面量列表")
    return {"path": path, "op": op, "value": value}


def validate_all(predicates: Iterable[Mapping[str, Any]] | None) -> list[dict]:
    rows = list(predicates or [])
    if len(rows) > MAX_PREDICATES:
        raise PredicateError("判据数量超过上限 %d" % MAX_PREDICATES)
    return [validate(row) for row in rows]


def evaluate(predicate: Mapping[str, Any], observation: Observation) -> Evaluation:
    """按观察求值；字段未知时返回 ``met=None``。"""
    path = str(predicate.get("path") or "")
    op = str(predicate.get("op") or "")
    field = observation.field(path)
    if op == "exists":
        return Evaluation(True, "字段存在") if field.state == "observed" else Evaluation(False, field.reason)
    if op == "absent":
        return Evaluation(True, "字段不存在") if field.state != "observed" else Evaluation(False, "字段存在")
    if field.state != "observed":
        return Evaluation(None, field.reason or "字段未知")
    actual = field.value
    expected = predicate.get("value")
    try:
        if op == "==":
            met = actual == expected
        elif op == "!=":
            met = actual != expected
        elif op == "in":
            met = actual in _as_container(expected)
        elif op == "not_in":
            met = actual not in _as_container(expected)
        else:
            met = _compare(op, actual, expected)
    except TypeError as exc:
        raise PredicateError("判据类型不可比较：%s %s %r" % (path, op, expected)) from exc
    return Evaluation(bool(met), "%s %s %r 实际为 %r" % (path, op, expected, actual))


def evaluate_all(predicates: Sequence[Mapping[str, Any]], observation: Observation) -> tuple[bool | None, list[str]]:
    """全部判据是否满足。任一为假即 ``False``；全真为 ``True``；否则 ``None``。"""
    unknown: list[str] = []
    for predicate in predicates:
        result = evaluate(predicate, observation)
        if result.met is False:
            return False, [result.reason]
        if result.met is None:
            unknown.append(result.reason)
    if unknown:
        return None, unknown
    return True, []


def _compare(op: str, actual: Any, expected: Any) -> bool:
    if op == "<":
        return actual < expected
    if op == "<=":
        return actual <= expected
    if op == ">":
        return actual > expected
    return actual >= expected


def _as_container(value: Any) -> Sequence[Any] | str:
    if isinstance(value, (list, tuple, str)):
        return value
    raise TypeError("in/not_in 需要列表或字符串")


def _is_literal(value: Any, depth: int = 0) -> bool:
    if depth > 2:
        return False
    if value is None or isinstance(value, (bool, int, float, str)):
        return True
    if isinstance(value, (list, tuple)):
        return all(_is_literal(item, depth + 1) for item in value)
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and _is_literal(item, depth + 1) for key, item in value.items())
    return False
