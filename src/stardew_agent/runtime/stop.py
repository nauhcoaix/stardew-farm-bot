"""停止信号。

支持两种来源，语义一致：进程内直接请求，或另一进程写停止文件（CLI ``stop``）。
停止不是异常兜底：收到后不下发新动作、取消在途委托、检查控制释放并输出已发生的结果。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol


class StopSource(Protocol):
    """``reason()`` 返回停止原因；未请求停止时返回 ``None``。"""

    def reason(self) -> str | None: ...


class StopSignal:
    """进程内停止开关。"""

    def __init__(self) -> None:
        self._reason: str | None = None

    def reason(self) -> str | None:
        return self._reason

    def request(self, reason: str = "user") -> None:
        self._reason = reason

    def clear(self) -> None:
        self._reason = None

    def to_dict(self) -> dict:
        return {"stopped": self._reason is not None, "reason": self._reason}


class StopFile:
    """跨进程停止文件；写入使用临时文件替换，读方不会看到半个 JSON。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def request(self, reason: str = "user") -> dict:
        payload = {"reason": reason, "written_by": "cli"}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, self.path)
        return payload

    def reason(self) -> str | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return str(payload.get("reason") or "requested")

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
