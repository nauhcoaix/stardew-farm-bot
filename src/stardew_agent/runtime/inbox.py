"""人类给 Agent 的指令/建议收件箱。

设计边界：

* 指令是**用户输入**，不是游戏事实；它只能影响目标选择，不能绕过能力参数校验、
  前提检查或安全边界；
* 新指令由 CLI 写成单个 JSON 文件（CLI 是创建者）；运行中的 Agent 只读这些文件，
  把“已展示 / 已照做”写入 ``ack.json``（运行进程是唯一写者），避免两个进程互相覆盖；
* 指令有 ``ttl_seconds``：过期自动变为 ``expired``，不会无限追问；
* 被展示但没被模型回执的指令下次仍然出现，直到照做、被撤销或过期。
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from ..contracts.errors import Rejected

#: 用户输入的类型：硬指令 / 建议 / 约束（提示词里按同样语义解释）
KINDS = ("instruction", "suggestion", "constraint")

#: 生命周期：未展示 / 已展示 / 已照做 / 被撤销 / 过期
STATUSES = ("pending", "delivered", "done", "dropped", "expired")

#: 未完成、需要继续展示给模型的状态
OPEN_STATUSES = ("pending", "delivered")

MAX_TEXT = 500
MIN_TTL = 30
MAX_TTL = 86400
MAX_PRIORITY = 9


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    """原子写 JSON：先写同目录临时文件再替换，读者不会看到半个文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("%s.tmp-%s" % (path.name, uuid.uuid4().hex[:8]))
    temporary.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


class HumanInbox:
    """文件收件箱：人写指令，Agent 读指令并写回执。"""

    def __init__(self, root: str | Path, *, clock: Callable[[], float] = time.time) -> None:
        self.root = Path(root)
        self.clock = clock
        self.ack_path = self.root / "ack.json"

    # ---- 人写入 ---------------------------------------------------
    def send(
        self,
        text: str,
        *,
        kind: str = "instruction",
        ttl_seconds: int = 1800,
        priority: int = 5,
    ) -> dict:
        """写一条指令；返回规范化后的记录（含 id）。"""
        cleaned = str(text or "").strip()
        if not cleaned:
            raise Rejected("指令内容不能为空")
        if len(cleaned) > MAX_TEXT:
            raise Rejected("指令内容最多 %d 字" % MAX_TEXT)
        if kind not in KINDS:
            raise Rejected("指令类型必须是 %s" % " / ".join(KINDS))
        ttl = int(ttl_seconds)
        if not MIN_TTL <= ttl <= MAX_TTL:
            raise Rejected("ttl_seconds 应在 %d～%d" % (MIN_TTL, MAX_TTL))
        level = int(priority)
        if not 1 <= level <= MAX_PRIORITY:
            raise Rejected("priority 应在 1～%d" % MAX_PRIORITY)
        record = {
            "id": "ins-" + uuid.uuid4().hex[:12],
            "created_at": self.clock(),
            "kind": kind,
            "text": cleaned,
            "priority": level,
            "ttl_seconds": ttl,
        }
        _atomic_write(self.root / (record["id"] + ".json"), record)
        return record


    # ---- 读取 -----------------------------------------------------
    def messages(self, *, now: float | None = None, include_finished: bool = True) -> list[dict]:
        """列出全部指令（默认含已完成/过期），按“待办优先、优先级高、时间早”排序。"""
        current = self.clock() if now is None else float(now)
        ack = self._read_ack()
        rows: list[dict] = []
        dirty = False
        for path in sorted(self.root.glob("ins-*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            receipt = dict(ack.get(raw["id"]) or {})
            status = str(receipt.get("status") or "pending")
            created = float(raw.get("created_at") or 0.0)
            ttl = int(raw.get("ttl_seconds") or 1800)
            if status in OPEN_STATUSES and current > created + ttl:
                status = "expired"
                receipt.update({"status": "expired", "updated_at": current})
                ack[raw["id"]] = receipt
                dirty = True
            rows.append(
                {
                    "id": raw["id"],
                    "text": str(raw.get("text") or ""),
                    "kind": str(raw.get("kind") or "instruction"),
                    "priority": int(raw.get("priority") or 5),
                    "ttl_seconds": ttl,
                    "created_at": created,
                    "status": status,
                    "delivered_at": receipt.get("delivered_at"),
                    "done_at": receipt.get("done_at"),
                    "note": str(receipt.get("note") or ""),
                    "age_seconds": round(max(0.0, current - created), 1),
                    "remaining_seconds": round(max(0.0, created + ttl - current), 1),
                }
            )
        if dirty:
            self._write_ack(ack)
        if not include_finished:
            rows = [row for row in rows if row["status"] in OPEN_STATUSES]
        rows.sort(
            key=lambda row: (
                row["status"] not in OPEN_STATUSES,
                -int(row.get("priority") or 5),
                float(row.get("created_at") or 0.0),
            )
        )
        return rows

    def pending(self, *, limit: int = 4, now: float | None = None) -> list[dict]:
        """需要模型现在看到的指令（含“已展示但没照做”的）。"""
        return self.messages(now=now, include_finished=False)[: max(0, int(limit))]

    # ---- Agent 回执 -----------------------------------------------
    def deliver(self, ids: Iterable[str], *, now: float | None = None) -> list[str]:
        """标记“已展示给模型”；已结束的指令不会被改写。"""
        return self._update(ids, now=now, status="delivered", fields=("delivered_at",))

    def follow(self, ids: Iterable[str], *, note: str = "", now: float | None = None) -> list[str]:
        """标记“模型说已经照做”；保留回执说明。"""
        return self._update(ids, now=now, status="done", fields=("done_at",), note=note)

    def drop(self, instruction_id: str, *, note: str = "", now: float | None = None) -> bool:
        """人撤销一条指令（例如改变主意）。"""
        return bool(self._update([instruction_id], now=now, status="dropped", fields=(), note=note))

    def summary(self) -> dict:
        rows = self.messages()
        counts = {status: 0 for status in STATUSES}
        for row in rows:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        return {
            "path": str(self.root),
            "total": len(rows),
            "pending": counts["pending"] + counts["delivered"],
            "counts": counts,
        }

    # ---- 内部 -----------------------------------------------------
    def _update(
        self,
        ids: Iterable[str],
        *,
        now: float | None,
        status: str,
        fields: tuple[str, ...],
        note: str = "",
    ) -> list[str]:
        current = self.clock() if now is None else float(now)
        wanted = [str(value) for value in ids if str(value).startswith("ins-")]
        if not wanted:
            return []
        ack = self._read_ack()
        changed: list[str] = []
        for instruction_id in wanted:
            if not (self.root / (instruction_id + ".json")).exists():
                continue
            receipt = dict(ack.get(instruction_id) or {})
            if receipt.get("status") in ("done", "dropped", "expired"):
                continue
            receipt["status"] = status
            receipt["updated_at"] = current
            for field in fields:
                receipt.setdefault(field, current)
            if note:
                receipt["note"] = str(note)[:300]
            ack[instruction_id] = receipt
            changed.append(instruction_id)
        if changed:
            self._write_ack(ack)
        return changed

    def _read_ack(self) -> dict:
        try:
            data = json.loads(self.ack_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return dict(data) if isinstance(data, dict) else {}

    def _write_ack(self, ack: Mapping[str, Any]) -> None:
        _atomic_write(self.ack_path, ack)
