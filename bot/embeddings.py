"""真正的向量检索：bge-small-zh-v1.5（ONNX）+ tokenizers，纯 CPU。

模型缺失或依赖没装时自动降级（返回 None），上层继续用 bigram 检索。
"""
import json
import os
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIR = os.path.join(_HERE, "models", "bge-small-zh-v1.5")
_LOCK = threading.Lock()
_STATE = {"session": None, "tokenizer": None, "tried": False, "dim": 512}


def model_dir():
    return os.environ.get("FARM_EMBED_DIR") or DEFAULT_DIR


def available():
    d = model_dir()
    files = [os.path.join(d, "tokenizer.json")]
    files += [os.path.join(d, f) for f in os.listdir(d)] if os.path.isdir(d) else []
    has_tok = any(f.endswith("tokenizer.json") and os.path.exists(f) for f in files)
    has_onnx = os.path.isdir(d) and any(f.endswith(".onnx") for f in os.listdir(d))
    return bool(has_tok and has_onnx)


def _load():
    if _STATE["tried"]:
        return _STATE["session"] is not None
    _STATE["tried"] = True
    if not available():
        return False
    try:
        import onnxruntime as ort
        from tokenizers import Tokenizer
        d = model_dir()
        onnx_file = next(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".onnx"))
        _STATE["tokenizer"] = Tokenizer.from_file(os.path.join(d, "tokenizer.json"))
        sess = ort.InferenceSession(onnx_file, providers=["CPUExecutionProvider"])
        _STATE["session"] = sess
        dim = sess.get_outputs()[0].shape[-1]
        if isinstance(dim, int) and dim > 0:
            _STATE["dim"] = dim
        return True
    except Exception:
        _STATE["session"] = None
        return False


def dim():
    _load()
    return _STATE["dim"]


def encode(texts, batch=16, max_len=128):
    """把文本编码成 (n, dim) 的 L2 归一化向量；不可用时返回 None。"""
    if isinstance(texts, str):
        texts = [texts]
    texts = [str(t)[:2000] for t in texts]
    if not texts:
        return None
    if not _load():
        return None
    import numpy as np
    tok, sess = _STATE["tokenizer"], _STATE["session"]
    if tok is None or sess is None:
        return None
    out = []
    with _LOCK:
        tok.enable_truncation(max_length=max_len)
        tok.enable_padding(pad_id=0, pad_token="[PAD]")
        for i in range(0, len(texts), batch):
            chunk = texts[i:i + batch]
            encs = tok.encode_batch(chunk)
            ids = np.array([e.ids for e in encs], dtype=np.int64)
            mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
            feeds = {}
            names = [x.name for x in sess.get_inputs()]
            feeds[names[0]] = ids
            if len(names) > 1:
                feeds[names[1]] = mask
            if len(names) > 2:
                feeds[names[2]] = np.array([e.type_ids for e in encs], dtype=np.int64)
            hidden = sess.run(None, feeds)[0]                       # (b, seq, dim)
            m = mask[..., None].astype(np.float32)
            vec = (hidden * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-6, None)
            norm = np.linalg.norm(vec, axis=1, keepdims=True)
            vec = vec / np.clip(norm, 1e-6, None)
            out.append(vec.astype(np.float32))
    return np.vstack(out)


def cosine(query_vec, matrix):
    """单个查询向量 vs 矩阵的余弦（都已归一化，等价点积）。"""
    import numpy as np
    return np.asarray(matrix, dtype=np.float32) @ np.asarray(query_vec, dtype=np.float32).reshape(-1)


def to_blob(vec):
    import numpy as np
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_blob(blob):
    import numpy as np
    return np.frombuffer(blob, dtype=np.float32)


def self_test_dir():
    """调试用：返回模型目录和是否可用。"""
    return {"dir": model_dir(), "available": available(), "dim": dim()}


if __name__ == "__main__":
    print(json.dumps(self_test_dir(), ensure_ascii=False))
    v = encode(["去小镇买防风草种子", "给作物浇水", "今天天气不错"])
    if v is None:
        print("模型不可用")
    else:
        import numpy as np
        print("shape:", v.shape)
        print("种子 vs 浇水:", float(v[0] @ v[1]))
        print("种子 vs 天气:", float(v[0] @ v[2]))
