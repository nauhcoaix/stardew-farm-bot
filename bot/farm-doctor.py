"""农场环境体检：窗口、进程、桥、日志，一次全打出来。

用法：python farm-doctor.py
"""
import ctypes
import ctypes.wintypes as wt
import glob
import json
import os
import sys
import time

DEV = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DEV)

u = ctypes.windll.user32
rows = []


@ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
def cb(hwnd, _):
    if not u.IsWindowVisible(hwnd):
        return True
    n = u.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return True
    b = ctypes.create_unicode_buffer(n + 1)
    u.GetWindowTextW(hwnd, b, n + 1)
    t = b.value or ""
    if not any(k in t for k in ("Stardew", "SMAPI", "星露谷", "StardewModdingAPI")):
        return True
    c = ctypes.create_unicode_buffer(256)
    u.GetClassNameW(hwnd, c, 256)
    r = wt.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    pid = wt.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    rows.append({
        "hwnd": int(hwnd), "class": c.value, "title": t[:70],
        "w": r.right - r.left, "h": r.bottom - r.top, "pid": pid.value,
    })
    return True


def say(s=""):
    print(s, flush=True)


say("=" * 64)
say("星露谷环境体检  " + time.strftime("%Y-%m-%d %H:%M:%S"))
say("=" * 64)

# 1) 窗口
say("\n【1】所有含 Stardew/SMAPI 的可见窗口")
u.EnumWindows(cb, 0)
if not rows:
    say("  （一个都没有：游戏没开？）")
for r in rows:
    cls = r["class"]
    is_console = ("console" in cls.lower()) or ("terminal" in cls.lower()) or \
                 cls in ("PseudoConsoleWindow", "CASCADIA_HOSTING_WINDOW_CLASS")
    tag = "控制台" if is_console else "像游戏"
    say("  %-34s %4dx%-4d pid=%-6d [%s] %s" % (cls, r["w"], r["h"], r["pid"], tag, r["title"]))

# 2) farmer.find_game_window 选谁
say("\n【2】程序会选哪个窗口")
try:
    import farmer
    say("  GAME_TITLE_HINTS = %s" % (farmer.GAME_TITLE_HINTS,))
    hwnd = farmer.find_game_window(verbose=True)
    say("  find_game_window() = %s" % hwnd)
    if hwnd:
        import launch_farm
        say("  描述: %s" % launch_farm._hwnd_desc(hwnd))
except Exception as e:  # noqa: BLE001
    say("  出错: %s: %s" % (type(e).__name__, e))

# 3) 进程
say("\n【3】进程")
try:
    import subprocess
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-Process | Where-Object { $_.ProcessName -match 'Stardew|SMAPI|junimo' } | "
         "Select-Object Id,ProcessName,MainWindowTitle | Format-Table -AutoSize | Out-String"],
        capture_output=True, text=True, timeout=20)
    txt = (out.stdout or "").strip()
    say("  " + (txt.replace("\n", "\n  ") if txt else "（没有相关进程）"))
except Exception as e:  # noqa: BLE001
    say("  查进程失败: %s" % e)

# 4) 桥（17654）
say("\n【4】SMAPI 桥（ws://127.0.0.1:17654）")
try:
    import socket
    s = socket.socket()
    s.settimeout(2)
    ok = s.connect_ex(("127.0.0.1", 17654)) == 0
    s.close()
    say("  端口 17654: " + ("通" if ok else "不通（游戏没加载 AgentStardew？）"))
except Exception as e:  # noqa: BLE001
    say("  检测失败: %s" % e)

# 5) 收件箱 / 状态 / 日志
say("\n【5】文件状态")
inbox = os.path.join(DEV, "inbox")
if os.path.isdir(inbox):
    files = sorted(glob.glob(os.path.join(inbox, "*.json")))
    say("  inbox: %d 个待处理" % len(files))
    for f in files[-3:]:
        try:
            say("    " + os.path.basename(f) + "  " + open(f, encoding="utf-8-sig").read()[:120])
        except Exception:
            pass
st = os.path.join(DEV, "status.json")
if os.path.exists(st):
    say("  status.json (改于 %s):" % time.strftime("%m-%d %H:%M", time.localtime(os.path.getmtime(st))))
    try:
        say("    " + json.dumps(json.load(open(st, encoding="utf-8-sig")), ensure_ascii=False)[:300])
    except Exception:
        pass
for name in ("launch-farm.log", "farm-service.log"):
    p = os.path.join(DEV, name)
    if os.path.exists(p):
        say("  %s (改于 %s) 最后 4 行:" % (name, time.strftime("%m-%d %H:%M", time.localtime(os.path.getmtime(p)))))
        try:
            lines = open(p, encoding="utf-8", errors="ignore").read().strip().splitlines()
            for ln in lines[-4:]:
                say("    " + ln)
        except Exception:
            pass

say("\n" + "=" * 64)
