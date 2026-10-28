"""地区连接图上的出口链。

来源：``stardew_valley_dfy/bot/exploration.py`` 的 ``region_route``。连接只能来自
已记录的有向边；不继承固定路线表，也不凭常识猜 ``FarmHouse`` 与 ``Farm`` 相通。
"""

from __future__ import annotations

from collections import deque
from typing import Any, Iterable, Mapping


def region_route(links: Iterable[Mapping[str, Any]], origin: str | None, destination: str | None) -> list[dict] | None:
    """按有向连接返回 ``origin`` 到 ``destination`` 的出口序列；不可达返回 ``None``。"""
    if not origin or not destination:
        return None
    edges = [dict(edge) for edge in links if edge.get("from") and edge.get("to")]
    if origin == destination:
        return []
    prior: dict[str, Mapping[str, Any] | None] = {origin: None}
    queue = deque([origin])
    while queue:
        current = queue.popleft()
        for edge in edges:
            if edge.get("from") != current or edge.get("to") in prior:
                continue
            prior[edge["to"]] = edge
            if edge["to"] == destination:
                route = []
                node = destination
                while prior[node] is not None:
                    step = prior[node]
                    route.append(dict(step))
                    node = step["from"]
                return list(reversed(route))
            queue.append(edge["to"])
    return None
