"""本地 ONNX 文本向量（来源 ``stardew_valley_dfy/bot/embeddings.py``）。

保留：模型目录检查与加载、CPU 批量编码为 L2 归一化向量、余弦相似度、
向量与可存库的二进制互转、模型不可用时的明确状态。

不包含：事实真实性判定、任务板、农务策略。模型缺失只表示“本模块不可用”，
不能因此把“是否学会”与外置模型是否安装绑定。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Iterable, Sequence

DEFAULT_DIR_NAME = "bge-small-zh-v1.5"


def default_model_dir() -> Path:
    override = os.environ.get("STARDEW_EMBED_DIR") or os.environ.get("FARM_EMBED_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "models" / DEFAULT_DIR_NAME


def _find_onnx(directory: Path) -> Path | None:
    if not directory.is_dir():
        return None
    for entry in sorted(directory.iterdir()):
        if entry.suffix == ".onnx":
            return entry
    return None


def is_available(model_dir: Path | str | None = None) -> bool:
    """目录里同时有 ``tokenizer.json`` 与至少一个 ``.onnx`` 才算可用。"""
    directory = Path(model_dir) if model_dir else default_model_dir()
    return (directory / "tokenizer.json").exists() and _find_onnx(directory) is not None


class Encoder:
    """一个可复用的编码器；加载失败时保持不可用状态，不抛异常。"""

    def __init__(self, model_dir: Path | str | None = None, *, batch: int = 16, max_length: int = 128) -> None:
        self.model_dir = Path(model_dir) if model_dir else default_model_dir()
        self.batch = int(batch)
        self.max_length = int(max_length)
        self._session = None
        self._tokenizer = None
        self._tried = False
        self._dim = 0
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        if self._session is not None:
            return True
        return is_available(self.model_dir)

    @property
    def dim(self) -> int:
        self.load()
        return self._dim

    def status(self) -> dict:
        return {"dir": str(self.model_dir), "available": self.available, "dim": self.dim if self._session else 0}

    def load(self) -> bool:
        """检查并加载模型；依赖或文件缺失时返回 ``False``。"""
        if self._tried:
            return self._session is not None
        self._tried = True
        if not is_available(self.model_dir):
            return False
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer

            onnx_file = _find_onnx(self.model_dir)
            if onnx_file is None:
                return False
            self._tokenizer = Tokenizer.from_file(str(self.model_dir / "tokenizer.json"))
            self._session = ort.InferenceSession(str(onnx_file), providers=["CPUExecutionProvider"])
            shape = self._session.get_outputs()[0].shape
            dim = shape[-1] if shape else 0
            if isinstance(dim, int) and dim > 0:
                self._dim = dim
            return True
        except Exception:
            self._session = None
            self._tokenizer = None
            return False

    def encode(self, texts: str | Iterable[str]) -> list[list[float]] | None:
        """批量编码为归一化向量；不可用时返回 ``None``。"""
        items = [texts] if isinstance(texts, str) else [str(text) for text in texts]
        items = [item[:2000] for item in items]
        if not items or not self.load():
            return None
        import numpy as np

        assert self._tokenizer is not None and self._session is not None
        vectors = []
        with self._lock:
            self._tokenizer.enable_truncation(max_length=self.max_length)
            self._tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
            for start in range(0, len(items), self.batch):
                chunk = items[start : start + self.batch]
                encodings = self._tokenizer.encode_batch(chunk)
                ids = np.array([row.ids for row in encodings], dtype=np.int64)
                mask = np.array([row.attention_mask for row in encodings], dtype=np.int64)
                names = [tensor.name for tensor in self._session.get_inputs()]
                feeds = {names[0]: ids}
                if len(names) > 1:
                    feeds[names[1]] = mask
                if len(names) > 2:
                    feeds[names[2]] = np.array([row.type_ids for row in encodings], dtype=np.int64)
                hidden = self._session.run(None, feeds)[0]
                weights = mask[..., None].astype(np.float32)
                pooled = (hidden * weights).sum(axis=1) / np.clip(weights.sum(axis=1), 1e-6, None)
                norm = np.linalg.norm(pooled, axis=1, keepdims=True)
                vectors.append((pooled / np.clip(norm, 1e-6, None)).astype(np.float32))
        return np.vstack(vectors).tolist()

    def encode_one(self, text: str) -> list[float] | None:
        rows = self.encode([text])
        return rows[0] if rows else None


def cosine(query: Sequence[float], matrix: Iterable[Sequence[float]]) -> list[float]:
    """归一化向量的点积即余弦；不依赖 numpy 也能算。"""
    rows = [list(map(float, row)) for row in matrix]
    query = list(map(float, query))
    return [sum(q * r for q, r in zip(query, row)) for row in rows]


def to_blob(vec: Sequence[float]) -> bytes:
    import numpy as np

    return np.asarray(list(vec), dtype=np.float32).tobytes()


def from_blob(blob: bytes) -> list[float]:
    import numpy as np

    return np.frombuffer(blob, dtype=np.float32).tolist()
