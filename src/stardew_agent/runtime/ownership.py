"""动作所有权：写游戏的通道同一时刻只允许一个执行器。

游戏桥和独立钓鱼 Mod 都能操纵角色，因此它们的写操作必须共享同一份所有权：
推理可以与已委托动作并行，但两个执行器不能同时动手。

这不只是礼貌性检查：所有权同时承担“谁有权取消”和“停止时谁负责释放控制”的语义。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Callable

from ..contracts.errors import OwnershipBusy


@dataclass(frozen=True)
class Lease:
    """一次动作所有权租约。"""

    owner: str
    action_id: str
    acquired_at: float

    def to_dict(self) -> dict:
        return {"owner": self.owner, "action_id": self.action_id, "acquired_at": self.acquired_at}


class ActionOwnership:
    """进程内单一所有者的租约管理。"""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._lease: Lease | None = None
        self._lock = asyncio.Lock()

    @property
    def current(self) -> Lease | None:
        return self._lease

    async def acquire(self, owner: str, action_id: str) -> Lease:
        """取得所有权；已有其他所有者时抛 ``OwnershipBusy``。"""
        async with self._lock:
            if self._lease is not None and self._lease.owner != owner:
                raise OwnershipBusy(
                    "动作所有权在 %s 手上（动作 %s），拒绝 %s" % (self._lease.owner, self._lease.action_id, owner)
                )
            lease = Lease(owner=owner, action_id=action_id, acquired_at=self.clock())
            self._lease = lease
            return lease

    async def release(self, lease: Lease) -> None:
        """释放租约；必须由持有者释放，避免误放别人的控制权。"""
        async with self._lock:
            if self._lease is None:
                return
            if self._lease != lease:
                raise OwnershipBusy("租约不匹配：当前持有 %s" % (self._lease.action_id,))
            self._lease = None

    async def force_release(self) -> Lease | None:
        """停止/故障时强制收回控制权，返回被收回的租约供记录。"""
        async with self._lock:
            lease, self._lease = self._lease, None
            return lease

    def snapshot(self) -> dict:
        return {"held": self._lease.to_dict() if self._lease else None}
