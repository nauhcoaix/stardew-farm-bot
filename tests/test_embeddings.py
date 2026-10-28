"""白名单 6：本地向量模块在模型缺失时的明确状态。"""

from __future__ import annotations

from pathlib import Path

import pytest

from stardew_agent.embeddings import Encoder, cosine, from_blob, is_available, to_blob


def test_目录缺少模型时报告不可用(tmp_path: Path):
    assert is_available(tmp_path) is False
    encoder = Encoder(tmp_path)
    assert encoder.available is False
    assert encoder.status()["available"] is False
    assert encoder.encode("测试") is None
    assert encoder.encode_one("测试") is None


def test_只有tokenizer没有onnx也不算可用(tmp_path: Path):
    (tmp_path / "tokenizer.json").write_text("{}", encoding="utf-8")
    assert is_available(tmp_path) is False


def test_向量二进制往返():
    pytest.importorskip("numpy")
    values = [0.5, -0.25, 0.0, 1.0]
    assert from_blob(to_blob(values)) == pytest.approx(values)


def test_余弦相似度不依赖模型文件():
    query = [1.0, 0.0]
    scores = cosine(query, [[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]])
    assert scores[0] == pytest.approx(1.0)
    assert scores[1] == pytest.approx(0.0)
    assert scores[2] == pytest.approx(-1.0)
