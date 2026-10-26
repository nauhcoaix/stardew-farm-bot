"""自动开星露谷并进入存档。

流程：
  1. 没开就启动 StardewModdingAPI.exe
  2. 等窗口 + 等桥（WS 17654）起来
  3. 如果还没读存档：点「加载」→ 点第一行存档 → 等 saveLoaded
坐标是按窗口尺寸的比例算的，换分辨率也能用。
"""
import asyncio
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

DEV = os.environ.get("FARM_DATA") or os.path.dirname(os.path.abspath(__file__))
GAME = os.path.dirname(DEV)
EXE = os.path.join(GAME, "StardewModdingAPI.exe")
RUN_NAME = os.path.join(DEV, "launch-farm.log")

sys.path.insert(0, DEV)
from farmer import _force_foreground, find_game_window  # noqa: E402

u = ctypes.windll.user32

# 相对窗口的比例（2560x1440 下量出来的）
BTN_LOAD = [(0.451, 0.910), (0.383, 0.837)]
ROW_FIRST = [(0.370, 0.358), (0.370, 0.300)]


def log(msg):
    line = "%s %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(RUN_NAME, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def window_size(hwnd):
    r = wt.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top




def fill_screen(hwnd):
    """把游戏窗口铺满主屏（无边框窗口下等价全屏）。"""
    try:
        import ctypes
        import ctypes.wintypes as wt
        u = ctypes.windll.user32
        # 最后一道保险：控制台窗口绝不动
        _cb = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(hwnd, _cb, 256)
        _cls = (_cb.value or "")
        if ("console" in _cls.lower()) or ("terminal" in _cls.lower()) or \
           _cls in ("PseudoConsoleWindow", "CASCADIA_HOSTING_WINDOW_CLASS"):
            log("这不是游戏窗口（%s），不铺满" % _cls)
            return False
        # 先取消最大化/还原，再按屏幕尺寸摆位
        u.ShowWindow(hwnd, 9)  # SW_RESTORE
        time.sleep(0.4)
        screen_w = u.GetSystemMetrics(0)
        screen_h = u.GetSystemMetrics(1)
        SWP_SHOWWINDOW = 0x0040
        SWP_FRAMECHANGED = 0x0020
        u.SetWindowPos(hwnd, 0, 0, 0, screen_w, screen_h, SWP_SHOWWINDOW | SWP_FRAMECHANGED)
        time.sleep(0.6)
        r = wt.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        log("窗口拉到 %dx%d（屏幕 %dx%d）" % (r.right - r.left, r.bottom - r.top, screen_w, screen_h))
        return True
    except Exception as e:  # noqa: BLE001
        log("铺满屏幕失败：%s" % e)
        return False

def click_ratio(hwnd, ratio, wait=1.2):
    _force_foreground(hwnd)
    time.sleep(0.3)
    left, top, w, h = window_size(hwnd)
    x = int(left + w * ratio[0])
    y = int(top + h * ratio[1])
    u.SetCursorPos(x - 3, y - 3)
    time.sleep(0.2)
    u.SetCursorPos(x, y)
    time.sleep(0.3)
    u.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.12)
    u.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(wait)
    return x, y


def live_game_window(verbose=False):
    """找游戏窗口，并且确认那个进程还活着（防止拿到刚关掉的残影）。"""
    hwnd = find_game_window(verbose=verbose)
    if not hwnd:
        return None
    pid = wt.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return None
    k32 = ctypes.windll.kernel32
    hproc = k32.OpenProcess(0x1000, False, pid.value)
    if not hproc:
        return None
    try:
        code = wt.DWORD()
        if not k32.GetExitCodeProcess(hproc, ctypes.byref(code)):
            return None
        if code.value != 259:  # STILL_ACTIVE
            return None
    finally:
        k32.CloseHandle(hproc)
    return hwnd


def _hwnd_desc(hwnd):
    """窗口类名 + 尺寸，写日志用（以后认错窗口一眼能看出来）。"""
    try:
        cb = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(hwnd, cb, 256)
        left, top, w, h = window_size(hwnd)
        return "%s %dx%d" % (cb.value, w, h)
    except Exception:  # noqa: BLE001
        return "?"


def ensure_game_started(timeout=90):
    # 第一次找窗口时把候选都打进日志：以后认错了一眼能看出来
    hwnd = live_game_window(verbose=True)
    if hwnd:
        log("游戏已经在跑（窗口 %s）" % _hwnd_desc(hwnd))
        return hwnd
    if not os.path.exists(EXE):
        log("找不到 %s" % EXE)
        return None
    log("启动游戏…")
    # 标准流要断开，否则游戏进程继承管道，调用方会一直等
    subprocess.Popen([EXE], cwd=GAME, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0 = time.time()
    while time.time() - t0 < timeout:
        hwnd = live_game_window()
        if hwnd:
            log("找到游戏窗口（%s）" % _hwnd_desc(hwnd))
            log("窗口出来了（%.0f 秒）" % (time.time() - t0))
            fill_screen(hwnd)
            return hwnd
        time.sleep(1.0)
    log("等了 %d 秒还没窗口" % timeout)
    return None


async def hello(timeout=5):
    """先试我们自己的 WS 桥（AgentStardew），不行再试 Remix 的 HTTP。"""
    try:
        from farm_client import Bridge
        async with Bridge() as b:
            h = await b.hello()
            return {"saveLoaded": bool(h.get("saveLoaded")), "ok": True}
    except Exception:
        pass
    import json as _json
    import urllib.request as _rq
    def _get():
        with _rq.urlopen("http://localhost:7842/status", timeout=timeout) as r:
            return _json.loads(r.read().decode())
    try:
        d = await asyncio.to_thread(_get)
    except Exception:
        return None
    return {"saveLoaded": bool(d.get("worldReady")), "ok": True}


async def wait_hello(timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            h = await asyncio.wait_for(hello(), timeout=8)
            if h:
                return h
        except Exception:
            await asyncio.sleep(2.0)
    return None


async def ensure_loaded(timeout=180):
    hwnd = ensure_game_started()
    if not hwnd:
        return False
    h = await wait_hello(timeout=150)
    if not h:
        log("游戏起来了但桥一直没响应")
        return False
    if h.get("saveLoaded"):
        log("存档已经在游戏里了")
        return True

    for attempt in range(1, 3):
        log("第 %d 次尝试进存档" % attempt)
        for btn, row in zip(BTN_LOAD, ROW_FIRST):
            click_ratio(hwnd, btn, wait=3.0)
            click_ratio(hwnd, row, wait=3.0)
            hh = await asyncio.wait_for(hello(), timeout=8)
            if hh and hh.get("saveLoaded"):
                log("进存档成功")
                return True
        t0 = time.time()
        while time.time() - t0 < 21:
            try:
                hh = await asyncio.wait_for(hello(), timeout=6)
            except Exception:
                hh = None
            if hh and hh.get("saveLoaded"):
                log("进存档成功（%.0f 秒）" % (time.time() - t0))
                return True
            await asyncio.sleep(3.0)
        log("这一次没进去")
        if find_game_window() == hwnd:
            _force_foreground(hwnd)
            from farmer import send_key
            send_key(0x1B)
            time.sleep(1.0)
    return False


async def main():
    ok = await ensure_loaded()
    log("结果：%s" % ("可以玩了" if ok else "没进去"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
