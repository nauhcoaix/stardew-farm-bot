import subprocess
p = r"""D:\code\stardew-farm-bot\README.md"""
r = open(p, encoding="utf-8").read()
if "校验文件一致性" not in r:
    r = r.replace("## 许可", """## 校验文件一致性

仓库里带 `CHECKSUMS.sha256`（每个文件的 sha256，按 LF 归一化算，Windows/Linux 结果一样）。

```powershell
# 校验（不一致会列出哪个文件、返回码 1）
python scripts/checksums.py --verify

# 改了文件之后重新生成
python scripts/checksums.py --write
```

Linux/macOS 上也能直接用标准工具（注意必须没开 autocrlf，否则换行会不一致）：

```bash
sha256sum -c CHECKSUMS.sha256
```

## 许可""")
    open(p, "w", encoding="utf-8", newline="\n").write(r)
    print("README 加了校验一节")

def git(cmds):
    for c in cmds:
        x = subprocess.run(c, cwd=r"""D:\code\stardew-farm-bot""", capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        out = ((x.stdout or "") + (x.stderr or "")).strip()
        print("  $ " + " ".join(c))
        for line in (out.splitlines()[:6] if out else []):
            if "LF will be replaced" in line: continue
            print("    " + line)

git([["git", "add", "-A"],
     ["git", "-c", "user.name=小船", "-c", "user.email=chuan@local", "commit", "-q", "-m",
      "加 CHECKSUMS.sha256 + scripts/checksums.py：校验文件一致性"],
     ["git", "-c", "http.proxy=http://127.0.0.1:7890", "-c", "https.proxy=http://127.0.0.1:7890",
      "push", "origin", "main"],
     ["git", "log", "--oneline"]])