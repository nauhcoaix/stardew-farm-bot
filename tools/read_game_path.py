"""从 ``config.json`` 读取 ``game_path``，供 Windows 批处理使用。

批处理不能安全地在 ``for /f`` 里内联 Python 代码：代码里的括号会被 cmd 当成
命令结构的一部分而截断。因此把读取逻辑放在独立脚本里，调用方只传路径。

输出约定：

* 有可用路径 → 打印 Windows 风格路径（正斜杠转反斜杠）；
* 文件缺失、JSON 损坏或没有 ``game_path`` → 不输出任何内容，调用方回退默认值。

用法：``python tools/read_game_path.py config.json``
"""

from __future__ import annotations

import json
import pathlib
import sys


def read_game_path(path: str | pathlib.Path) -> str:
    """返回配置里的游戏目录；不可用时返回空字符串（不抛异常）。"""
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    value = str(data.get("game_path") or "").strip()
    return value.replace("/", "\\")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("用法：read_game_path.py <config.json>", file=sys.stderr)
        return 2
    value = read_game_path(argv[1])
    if value:
        print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
