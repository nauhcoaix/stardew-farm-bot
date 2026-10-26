"""给仓库里所有被 git 跟踪的文件生成 / 校验 sha256。

用法：
  python scripts/checksums.py --write    # 重写 CHECKSUMS.sha256
  python scripts/checksums.py --verify   # 校验（不一致返回码 1）

输出格式和 sha256sum 一样，所以在 Linux/macOS 上也能直接：
  sha256sum -c CHECKSUMS.sha256
"""
import argparse
import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "CHECKSUMS.sha256")
SKIP = {os.path.basename(MANIFEST)}


def tracked_files():
    """用 git 列出被跟踪的文件（没有 git 就退化成遍历，但跳过 .git）。"""
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT,
                             capture_output=True, check=True).stdout
        files = [p for p in out.decode("utf-8", "replace").split("\0") if p]
    except Exception:
        files = []
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__")]
            for f in filenames:
                files.append(os.path.relpath(os.path.join(dirpath, f), ROOT).replace("\\", "/"))
    return sorted(f for f in files if os.path.basename(f) not in SKIP)


def sha256(path):
    """按 LF 归一化后算 sha256。

    这样 Windows（CRLF 检出）和 Linux（LF 检出）算出来一样，
    只有真正的内容变化才会被发现。
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        carry = b""
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            chunk = carry + chunk
            carry = b""
            if chunk.endswith(b"\r"):
                carry = b"\r"
                chunk = chunk[:-1]
            h.update(chunk.replace(b"\r\n", b"\n"))
        if carry:
            h.update(carry)
    return h.hexdigest()


def write_manifest():
    lines = []
    for rel in tracked_files():
        full = os.path.join(ROOT, rel)
        if not os.path.isfile(full):
            continue
        lines.append("%s  %s" % (sha256(full), rel))
    with open(MANIFEST, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print("写入 %d 个文件的哈希 -> %s" % (len(lines), os.path.basename(MANIFEST)))
    return 0


def verify_manifest():
    if not os.path.exists(MANIFEST):
        print("没有 %s，先跑 --write" % os.path.basename(MANIFEST))
        return 2
    bad, missing, ok = [], [], 0
    with open(MANIFEST, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            want, _, rel = line.partition("  ")
            full = os.path.join(ROOT, rel)
            if not os.path.isfile(full):
                missing.append(rel)
                continue
            got = sha256(full)
            if got == want:
                ok += 1
            else:
                bad.append((rel, want, got))
    # 清单之外多出来的被跟踪文件（新增却没更新清单）
    listed = set()
    with open(MANIFEST, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").partition("  ")
            if parts[1]:
                listed.add(parts[2])
    extra = [r for r in tracked_files() if r not in listed]

    print("一致: %d 个" % ok)
    if bad:
        print("内容变了: %d 个" % len(bad))
        for rel, want, got in bad[:20]:
            print("  ~ %s\n      期望 %s\n      实际 %s" % (rel, want[:16], got[:16]))
    if missing:
        print("文件没了: %d 个" % len(missing))
        for rel in missing[:20]:
            print("  - %s" % rel)
    if extra:
        print("清单里没有（新加的）: %d 个" % len(extra))
        for rel in extra[:20]:
            print("  + %s" % rel)
    if not (bad or missing or extra):
        print("全部一致 ✓")
        return 0
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="重新生成清单")
    ap.add_argument("--verify", action="store_true", help="校验现有清单")
    a = ap.parse_args()
    if a.write:
        return write_manifest()
    if a.verify:
        return verify_manifest()
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
