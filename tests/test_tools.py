"""``tools/read_game_path.py``：批处理读取游戏路径的小工具。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("read_game_path", ROOT / "tools" / "read_game_path.py")
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

read_game_path = _MODULE.read_game_path
main = _MODULE.main


def write_config(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_读取路径并转成反斜杠(tmp_path):
    path = write_config(tmp_path, {"game_path": "D:/Games/Stardew Valley"})
    assert read_game_path(path) == "D:\\Games\\Stardew Valley"


def test_缺失或空的game_path返回空(tmp_path):
    assert read_game_path(write_config(tmp_path, {})) == ""
    assert read_game_path(write_config(tmp_path, {"game_path": None})) == ""
    assert read_game_path(write_config(tmp_path, {"game_path": "   "})) == ""


def test_损坏JSON或缺失文件返回空(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert read_game_path(bad) == ""
    assert read_game_path(tmp_path / "missing.json") == ""


def test_支持带BOM的UTF8(tmp_path):
    path = tmp_path / "bom.json"
    payload = json.dumps({"game_path": "C:/Game"}, ensure_ascii=False).encode("utf-8")
    path.write_bytes(b"\xef\xbb\xbf" + payload)
    assert read_game_path(path) == "C:\\Game"


def test_缺参数时返回用法错误(capsys):
    assert main(["read_game_path.py"]) == 2
    assert "用法" in capsys.readouterr().err
