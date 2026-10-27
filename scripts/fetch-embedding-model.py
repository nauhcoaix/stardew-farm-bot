"""下载 bge-small-zh-v1.5 的 ONNX 模型（走 hf-mirror），放到 bot/models/ 下。

用法：
  python scripts/fetch-embedding-model.py
  python scripts/fetch-embedding-model.py --repo Xenova/bge-small-zh-v1.5 --dtype quantized
"""
import argparse
import os
import sys
import urllib.request

MIRRORS = ("https://hf-mirror.com", "https://huggingface.co")
DEFAULT_REPO = "Xenova/bge-small-zh-v1.5"
FILES = {
    "quantized": ["onnx/model_quantized.onnx", "tokenizer.json"],
    "full": ["onnx/model.onnx", "tokenizer.json"],
}
ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot", "models")


def download(url, dest, timeout=60):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": "farm-bot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                print("    %.0f%% (%.1f/%.1f MB)" % (done * 100.0 / total, done / 1048576, total / 1048576),
                      end="\r", flush=True)
    os.replace(tmp, dest)
    print("    -> %s (%.1f MB)" % (dest, os.path.getsize(dest) / 1048576))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--dtype", default="quantized", choices=("quantized", "full"))
    ap.add_argument("--out", default=os.path.join(ROOT, DEFAULT_REPO.split("/")[-1]))
    a = ap.parse_args()

    for f in FILES[a.dtype]:
        dest = os.path.join(a.out, os.path.basename(f))
        if os.path.exists(dest) and os.path.getsize(dest) > 1024 * 1024:
            print("  已存在，跳过: %s" % dest)
            continue
        ok = False
        for base in MIRRORS:
            url = "%s/%s/resolve/main/%s" % (base, a.repo, f)
            print("  下载 %s" % url)
            try:
                download(url, dest)
                ok = True
                break
            except Exception as exc:
                print("    失败：%s" % exc)
        if not ok:
            print("  所有镜像都失败：%s" % f)
            return 1
    print("\n完成。模型目录：%s" % a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
