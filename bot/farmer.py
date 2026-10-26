"""大肥鱼的农场循环。

一个游戏日的流程：
  1. 补水壶（水不够时找水源）
  2. 收成熟作物
  3. 给缺水的作物浇水
  4. 有空地+种子就种
  5. 回屋睡觉，推进到第二天

用法：
  python farmer.py --peek          只看现状，不动手
  python farmer.py --day           干一天（浇水/收/种/睡）
  python farmer.py --day --dry     只打印计划，不动手
"""
import argparse
import asyncio
import ctypes
import json
import os
import sys
import time
from action_history import ActionHistory, target_progress
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from farm_client import Bridge  # noqa: E402

SEED_KEYWORDS = ("种子", "Seeds")


PLOT = {"x1": 60, "x2": 69, "y1": 15, "y2": 22}


def in_plot(tile):
    if not tile:
        return False
    return PLOT["x1"] <= tile.get("x", -1) <= PLOT["x2"] and PLOT["y1"] <= tile.get("y", -1) <= PLOT["y2"]


_DATA_DIR = os.environ.get("FARM_DATA") or os.path.dirname(os.path.abspath(__file__))
LOCK_FILE = os.path.join(_DATA_DIR, "input.lock")
WECHAT_LOCK = os.path.join(_DATA_DIR, "wechat.lock")




def wechat_busy(max_wait=20):
    """微信正在操作时，农场先等着（别抢焦点）。"""
    t0 = time.time()
    while time.time() - t0 < max_wait:
        try:
            if not os.path.exists(WECHAT_LOCK):
                return False
            exp = float((open(WECHAT_LOCK, encoding="utf-8").read().strip() or 0))
            if time.time() > exp:
                return False
        except Exception:
            return False
        time.sleep(0.4)
    return True


def lock_input(seconds=5):
    """占住鼠标键盘：微信那边看到这个文件就先等着。"""
    wechat_busy(20)
    try:
        with open(LOCK_FILE, "w", encoding="utf-8") as f:
            f.write(str(time.time() + seconds))
    except Exception:
        pass


def unlock_input():
    try:
        os.remove(LOCK_FILE)
    except Exception:
        pass


CONSOLE_CLASSES = {
    "ConsoleWindowClass",
    "PseudoConsoleWindow",
    "CASCADIA_HOSTING_WINDOW_CLASS",   # Windows Terminal
    "mintty",
    "ConEmuControl",
    "VirtualConsoleClass",
}

# 游戏窗口标题：英文版 "Stardew Valley"，中文版 "星露谷物语"
GAME_TITLE_HINTS = ("Stardew Valley", "星露谷物语", "星露谷")


def _is_console_class(cls):
    c = (cls or "").lower()
    return ("console" in c) or (cls in CONSOLE_CLASSES) or ("terminal" in c)


def _class_name(user32, hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value or ""


def _client_size(user32, hwnd):
    r = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    return r.right - r.left, r.bottom - r.top


GAME_EXES = ("stardewmoddingapi.exe", "stardew valley.exe")
SHELL_CLASSES = {"CabinetWClass", "ExploreWClass", "Progman", "WorkerW"}


def _window_exe(hwnd):
    """窗口属于哪个进程的可执行文件名（小写）。取不到就返回空串。"""
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    h = kernel32.OpenProcess(0x1000, False, pid.value)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if not kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return ""
        return os.path.basename(buf.value).lower()
    finally:
        kernel32.CloseHandle(h)


def find_game_window(verbose=False):
    """找游戏主窗口。

    SMAPI 控制台标题里也带 "Stardew Valley"/"SMAPI"，以前会认错。现在：
      1) 标题要像游戏（英文 Stardew Valley / 中文 星露谷物语）
      2) 类名像控制台的一律排除（ConsoleWindowClass / CASCADIA... / 含 console 的）
      3) 客户区太小（<320x240）的排除
      4) 有多个候选时，优先标题里没有 SMAPI 的，再取客户区最大的
    verbose=True 时把每个候选都打出来，方便看日志排查。
    """
    user32 = ctypes.windll.user32
    cands = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value or ""
        if not any(h in title for h in GAME_TITLE_HINTS):
            return True
        cls = _class_name(user32, hwnd)
        cw, ch = _client_size(user32, hwnd)
        is_console = _is_console_class(cls)
        # 资源管理器/桌面窗口标题里也可能带 Stardew Valley（比如打开游戏文件夹）
        if cls in SHELL_CLASSES:
            if verbose:
                try:
                    print("  [候选] class=%s %dx%d %s  ← 资源管理器，排除" % (cls, cw, ch, title[:50]), flush=True)
                except Exception:
                    pass
            return True
        # 最硬的判据：看进程是谁
        exe = _window_exe(hwnd)
        if exe and exe not in GAME_EXES:
            if verbose:
                try:
                    print("  [候选] class=%s %dx%d %s  ← 进程 %s 不是游戏，排除" % (cls, cw, ch, title[:40], exe), flush=True)
                except Exception:
                    pass
            return True
        if verbose:
            try:
                print("  [候选] class=%s %dx%d %s%s" % (
                    cls, cw, ch, title[:60], "  ← 控制台，排除" if is_console else ""), flush=True)
            except Exception:
                pass
        if is_console:
            return True
        if cw < 320 or ch < 240:
            return True
        has_smapi = "SMAPI" in title
        cands.append((0 if has_smapi else 1, cw * ch, hwnd, cls, cw, ch, title))
        return True

    user32.EnumWindows(cb, 0)
    if not cands:
        return None
    cands.sort(reverse=True)
    best = cands[0]
    if verbose:
        try:
            print("  [选中] class=%s %dx%d %s" % (best[3], best[4], best[5], best[6][:60]), flush=True)
        except Exception:
            pass
    return best[2]




def _force_foreground(hwnd):
    """后台进程直接 SetForegroundWindow 经常被系统拒绝，用 AttachThreadInput 硬切。"""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    fg = user32.GetForegroundWindow()
    tid_fg = user32.GetWindowThreadProcessId(fg, None)
    tid_me = kernel32.GetCurrentThreadId()
    attached = False
    try:
        if tid_fg and tid_fg != tid_me:
            attached = bool(user32.AttachThreadInput(tid_me, tid_fg, True))
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetFocus(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(tid_me, tid_fg, False)


def click_at_ratio(rx, ry):
    """按窗口比例点一下（0.5,0.5 就是正中）。"""
    lock_input(5)
    user32 = ctypes.windll.user32
    hwnd = find_game_window()
    if not hwnd:
        return False
    _force_foreground(hwnd)
    time.sleep(0.25)
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w = r.right - r.left
    h = r.bottom - r.top
    if w <= 0 or h <= 0:
        return False
    cx = r.left + int(w * rx)
    cy = r.top + int(h * ry)
    user32.SetCursorPos(cx, cy)
    time.sleep(0.1)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.06)
    user32.mouse_event(0x0004, 0, 0, 0, 0)
    return True


def click_game_center():
    lock_input(5)
    """睡觉后画面会停在黑屏等一次点击；这里替点一下。"""
    user32 = ctypes.windll.user32
    hwnd = find_game_window()
    if not hwnd:
        return False
    user32.ShowWindow(hwnd, 9)          # SW_RESTORE
    _force_foreground(hwnd)
    time.sleep(0.35)
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    cx = (r.left + r.right) // 2
    cy = (r.top + r.bottom) // 2
    user32.SetCursorPos(cx, cy)
    time.sleep(0.1)
    user32.mouse_event(0x0002, 0, 0, 0, 0)   # down
    time.sleep(0.06)
    user32.mouse_event(0x0004, 0, 0, 0, 0)   # up
    # 有些界面认键盘更灵，补一下空格和回车
    user32.keybd_event(0x20, 0, 0, 0)        # space down
    time.sleep(0.03)
    user32.keybd_event(0x20, 0, 2, 0)        # space up
    time.sleep(0.05)
    user32.keybd_event(0x0D, 0, 0, 0)        # enter down
    time.sleep(0.03)
    user32.keybd_event(0x0D, 0, 2, 0)        # enter up
    return True


def ents(snap):
    return snap.get("entities") or []


def by_kind(snap, kind):
    return [e for e in ents(snap) if e.get("kind") == kind]


def find_action(snap, action):
    for e in ents(snap):
        if (e.get("action") or "").lower() == action.lower():
            return e
    return None


def send_key(vk, times=1):
    lock_input(5)
    """给游戏窗口发个键（ESC 关菜单之类）。"""
    user32 = ctypes.windll.user32
    hwnd = find_game_window()
    if not hwnd:
        return False
    _force_foreground(hwnd)
    time.sleep(0.2)
    for _ in range(times):
        user32.keybd_event(vk, 0, 0, 0)
        time.sleep(0.05)
        user32.keybd_event(vk, 0, 2, 0)
        time.sleep(0.2)
    return True


VK_ESCAPE = 0x1B

# 农场上要清的东西 -> 用哪把工具
CLEAR_TOOLS = {
    "嫩枝": "Axe",
    "树枝": "Axe",
    "木头": "Axe",
    
    "石头": "Pickaxe",
    "岩石": "Pickaxe",
    # 杂草只能用镰刀割（斧头/镐都砍不掉）；mod 已支持镰刀
    "杂草": "Scythe",
    "Grass": "Scythe",
    "草": "Scythe",
    "杂草丛": "Scythe",
}


SCYTHE_IDS = ("47", "53")          # 47 普通镰刀 / 53 金镰刀


def is_scythe_item(it):
    """镰刀在游戏里是 MeleeWeapon，观察数据里 tool=MeleeWeapon，只有名字/ID 能认。"""
    name = (it.get("name") or "")
    item_id = str(it.get("id") or it.get("itemId") or "")
    if "镰刀" in name or "scythe" in name.lower():
        return True
    if "scythe" in (it.get("tool") or "").lower():
        return True
    return any(item_id == x or item_id.endswith(")" + x) for x in SCYTHE_IDS)


def tool_slot(snap, tool_name):
    for it in snap.get("inventory") or []:
        if (it.get("tool") or "") == tool_name or (it.get("name") or "") == tool_name:
            return it
    if tool_name == "Scythe":
        for it in snap.get("inventory") or []:
            if is_scythe_item(it):
                return it
        return None
    # 其余工具用名字兜底（不同语言/别名）
    alias = {"Axe": "斧头", "Pickaxe": "十字镐", "Hoe": "锄头", "WateringCan": "喷壶"}
    want = alias.get(tool_name, tool_name)
    for it in snap.get("inventory") or []:
        if (it.get("name") or "") == want:
            return it
    return None


def seeds_in(snap):
    out = []
    for it in snap.get("inventory") or []:
        name = it.get("name") or ""
        if any(k in name for k in SEED_KEYWORDS):
            out.append(it)
    return out


def water_can(snap):
    for it in snap.get("inventory") or []:
        if it.get("tool") == "WateringCan":
            return it
    return None


def hoe_slot(snap):
    for it in snap.get("inventory") or []:
        if it.get("tool") == "Hoe":
            return it
    return None


def water_targets(snap):
    """该浇的：翻过但没浇的土（有作物没作物都算）。"""
    out = []
    for e in ents(snap):
        if e.get("kind") != "soil" or e.get("watered"):
            continue
        if e.get("crop") or e.get("tilled"):
            out.append(e)
    return out


def harvestable(snap):
    out = []
    for e in ents(snap):
        if e.get("crop") and (e.get("ready") or e.get("harvestable")):
            out.append(e)
    return out


def empty_tilled(snap):
    out = []
    for e in ents(snap):
        if e.get("kind") == "soil" and e.get("tilled") and not e.get("crop") and in_plot(e.get("tile")):
            out.append(e)
    return out


def water_sources(snap):
    return [e for e in ents(snap) if e.get("refillable")]


class Farmer:
    def __init__(self, bridge, dry=False, log=print, state_path=None, history=None):
        self.b = bridge
        self.dry = dry
        self.log = log
        self.history = history if history is not None else ActionHistory(log=log)
        self.last_error = ""
        self.min_energy = 25
        self.last_harvest = 0
        self.last_till = 0
        self.last_planted = 0
        self.last_watered = 0
        self.last_cleared = 0
        self.last_cleared_in = 0      # 清在开垦区内的格数
        self.last_cleared_out = 0     # 清在开垦区外的格数
        self.last_chops = 0
        self.last_seed_gain = []
        self.state_path = state_path
        self.state = {}
        if state_path and os.path.exists(state_path):
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    self.state = json.load(f)
            except Exception:
                self.state = {}

    def save_state(self):
        if not self.state_path:
            return
        try:
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False)
        except Exception:
            pass
        self.last_harvest = 0
        self.last_till = 0
        self.last_planted = 0
        self.last_watered = 0
        self.last_cleared = 0

    async def snap(self, radius=14):
        return await self.b.snapshot(radius)

    async def ensure_free(self, snap):
        """角色被对话/菜单/结算界面卡住时点一下。"""
        for _ in range(3):
            menu = (snap or {}).get("menu") or {}
            if menu.get("type") == "ShippingMenu":
                self.log("  结算界面，点 OK")
                click_at_ratio(0.597, 0.686)
                await asyncio.sleep(1.2)
                snap = await self.snap(12)
                continue
            if (snap.get("player") or {}).get("canMove", True):
                return snap
            self.log("  角色卡住了，点一下屏幕")
            click_game_center()
            await asyncio.sleep(1.0)
            snap = await self.snap(12)
        return snap


    async def chest_get(self, snap, tile, name=""):
        """从箱子拿东西进背包。"""
        try:
            r = await self.b.request("chestget", {"x": int(tile["x"]), "y": int(tile["y"]), "name": name})
            self.log("  翻箱子 %s：%s" % ((tile["x"], tile["y"]), r.get("moved") or r.get("reason")))
        except Exception as e:  # noqa: BLE001
            self.log("  翻箱子失败：%s" % e)
        return await self.snap(12)



    async def loot_container(self, snap, radius=6):
        """走到最近的可交互物件旁，打开并把里面的东西全拿进背包。"""
        ents_ = [e for e in ents(snap) if e.get("kind") == "object"]
        if not ents_:
            return snap
        pos = (snap.get("player") or {}).get("position") or {}
        ents_.sort(key=lambda e: abs((e.get("tile") or {}).get("x", 0) - pos.get("x", 0)) + abs((e.get("tile") or {}).get("y", 0) - pos.get("y", 0)))
        for e in ents_[:3]:
            r = await self.do("走到 %s 旁" % (e.get("name"),), self.b.move(near=e["ref"]))
            snap = (r or {}).get("snapshot") or snap
            r = await self.do("打开 %s" % (e.get("name"),), self.b.interact(e["ref"]))
            snap = (r or {}).get("snapshot") or await self.snap(12)
            menu = snap.get("menu") or {}
            if not menu:
                continue
            self.log("  打开了：%s" % (menu.get("type"),))
            for _ in range(6):
                try:
                    md = await self.b.request("menudump", {})
                except Exception:
                    break
                items = md.get("items") or []
                if not items:
                    break
                it = items[0]
                cx = int(it.get("x", 0) + it.get("w", 0) // 2)
                cy = int(it.get("y", 0) + it.get("h", 0) // 2)
                try:
                    await self.b.request("menuclick", {"x": cx, "y": cy})
                except Exception:
                    break
                await asyncio.sleep(0.25)
            await self.clear_menu(snap)
            snap = await self.snap(12)
            break
        return snap

    async def clear_menu(self, snap):
        """把挡路的对话框点掉（开包裹、拾取提示之类）。"""
        for _ in range(6):
            menu = (snap or {}).get("menu")
            if not menu:
                return snap
            opts = menu.get("options") or []
            if not opts:
                # 背包/系统菜单这种 mod 不支持的：先点 OK（按 ESC 往往没反应）
                self.log("  不认识的菜单（%s），点一下 OK 试试" % (menu.get("type"),))
                closed = False
                for _rx, _ry in ((0.597, 0.686), (0.5, 0.62), (0.5, 0.5), (0.5, 0.85)):
                    try:
                        click_at_ratio(_rx, _ry)
                    except Exception:
                        pass
                    await asyncio.sleep(0.7)
                    snap = await self.snap(12)
                    if not (snap.get("menu") or {}):
                        closed = True
                        break
                if closed:
                    return snap
                self.log("  点了也没关掉，按 ESC 兜底")
                send_key(VK_ESCAPE)
                await asyncio.sleep(1.0)
                snap = await self.snap(16)
                continue
            pick = None
            for o in opts:
                lab = (o.get("label") or o.get("name") or "").strip()
                if lab in ("继续", "确定", "是", "知道了", "好", "OK", "Yes"):
                    pick = o
                    break
            pick = pick or opts[0]
            try:
                r = await self.b.menu(pick["ref"])
                snap = (r or {}).get("snapshot") or await self.snap(16)
            except Exception as e:  # noqa: BLE001
                if "INSUFFICIENT_RESOURCE" in str(e) or "体力不足" in str(e):
                    self.no_stamina = True
                self.log("  关菜单失败：%s" % e)
                return snap
            await asyncio.sleep(0.3)
        return snap

    async def do(self, what, coro):
        self.last_error = ""
        # 体力见底：所有后续动作直接放弃，别再刷日志
        if getattr(self, "no_stamina", False) or getattr(self, "_energy_low", False):
            self.log("  [跳过] %s：没体力了" % what)
            try:
                coro.close()
            except Exception:
                pass
            return None
        if self.dry:
            try:
                coro.close()
            except Exception:
                pass
            self.log("  [dry] 会做：%s" % what)
            return None
        try:
            r = await coro
            if isinstance(r, dict) and r.get("ok") is False:
                raise RuntimeError(str(r.get("error") or r.get("detail") or "动作返回失败"))
            self.log("  %s -> %s" % (what, (r or {}).get("detail") or "ok"))
            return r
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)
            self.log("  %s 失败：%s" % (what, e))
            return None

    def available_targets(self, snap, action, targets):
        return [e for e in targets if self.history.allowed(snap, action, e)]

    async def work_target(self, snap, action, entity, slot=None):
        """一次目标动作：移动后更新ref，检查实际结果，失败记忆跨技能轮次保留。"""
        if not self.history.allowed(snap, action, entity):
            return snap, False
        if self.dry:
            self.log("  [dry] %s %s" % (action, entity.get("tile")))
            return snap, False
        before = snap
        current = next((e for e in ents(snap) if e.get("tile") == entity.get("tile")
                        and e.get("kind") == entity.get("kind")
                        and e.get("name") == entity.get("name")), None)
        if not current:
            return snap, False
        moved = await self.do("走到 %s" % entity.get("tile"), self.b.move(near=current["ref"]))
        error = self.last_error
        snap = (moved or {}).get("snapshot") or await self.snap(16)
        if not moved:
            self.history.record(before, snap, action, entity, False, error or "移动未完成")
            return snap, False
        # 每次观察的ref都可能更换；按物理目标重新绑定，不复用计划里的旧ref。
        current = next((e for e in ents(snap) if e.get("tile") == entity.get("tile")
                        and e.get("kind") == entity.get("kind")
                        and e.get("name") == entity.get("name")), None)
        if snap.get("location") != before.get("location") or not current:
            self.history.record(before, snap, action, entity, False, "目标已离开当前观察范围")
            return snap, False
        if slot is not None:
            selected = await self.do("选择工具/种子", self.b.select(slot))
            if not selected:
                error = self.last_error
                self.history.record(before, snap, action, entity, False, error or "选择工具失败")
                return snap, False
            # select 返回的新快照也会使ref更新。
            snap = selected.get("snapshot") or await self.snap(16)
            current = next((e for e in ents(snap) if e.get("tile") == entity.get("tile")
                            and e.get("kind") == entity.get("kind")
                            and e.get("name") == entity.get("name")), None)
            if not current:
                self.history.record(before, snap, action, entity, False, "目标已改变")
                return snap, False
        before = snap
        command = self.b.interact if action == "harvest" else self.b.use
        result = await self.do("%s %s" % (action, entity.get("tile")), command(current["ref"]))
        error = self.last_error
        snap = (result or {}).get("snapshot") or await self.snap(16)
        progress = target_progress(action, before, snap, entity, verified=bool(result))
        self.history.record(before, snap, action, entity, progress, error)
        return snap, progress

    async def refill_if_needed(self, snap, force=False):
        can = water_can(snap)
        if not can:
            return snap
        water = can.get("water") or 0
        cap = can.get("waterCapacity") or 40
        if not force and water > cap * 0.2:
            return snap
        for _ in range(3):
            snap = await self.snap(16)
            srcs = water_sources(snap)
            if not srcs:
                await self.find_water(snap)
                snap = await self.snap(16)
                srcs = water_sources(snap)
                if not srcs:
                    self.log("  水壶只剩 %s，找不到水源" % water)
                    return snap
            src = srcs[0]
            tile = src.get("tile") or {}
            # 自己找路走到水边（mod 自带寻路容易被树挡住）
            snap = await self.walk_to(snap, tile.get("x"), tile.get("y"), tries=20, arrive=1)
            snap = await self.snap(16)
            srcs = water_sources(snap)
            if not srcs:
                continue
            src = srcs[0]
            self.state["water_tile"] = src.get("tile")
            self.save_state()
            can = water_can(snap) or can
            await self.do("选水壶", self.b.select(can["slot"]))
            r = await self.do("补水", self.b.use(src["ref"]))
            snap = (r or {}).get("snapshot") or await self.snap(16)
            can2 = water_can(snap) or {}
            self.log("  补完水壶 %s" % can2.get("water"))
            if (can2.get("water") or 0) > 0:
                return snap
        return snap

    async def find_water(self, snap):
        """视野里没水，就去几个可能有水的地方转一圈。"""
        spots = []
        wt = self.state.get("water_tile")
        if wt:
            spots.append((wt.get("x"), wt.get("y")))
        spots += [(20, 20), (35, 30), (20, 50), (60, 45), (30, 60)]
        tried = set()
        for spot in spots:
            if not spot[0] or spot in tried:
                continue
            tried.add(spot)
            try:
                snap = await self.walk_to(snap, spot[0], spot[1], tries=15)
            except Exception:
                continue
            srcs = water_sources(snap)
            if srcs:
                self.log("  找到水源：%s" % (srcs[0].get("tile"),))
                return srcs
        return []

    async def water_crops(self, snap, limit=None):
        targets = self.available_targets(snap, "water", water_targets(snap))
        if limit is not None:
            targets = targets[:max(0, limit)]
        self.log("  需要浇水：%d 格" % len(targets))
        done = 0
        for e in targets:
            can = water_can(snap)
            if not can:
                break
            if (can.get("water") or 0) <= 0:
                snap = await self.refill_if_needed(snap, force=True)
                can = water_can(snap) or can
                if (can.get("water") or 0) <= 0:
                    self.log("  没水了，停止浇水")
                    break
            snap, progress = await self.work_target(snap, "water", e, can["slot"])
            if progress:
                done += 1
        self.last_watered = done
        self.log("  实际浇水：%d 格" % done)
        return snap

    async def harvest(self, snap, limit=None):
        # 只收快照确认成熟且不在失败历史中的作物。
        crops = [e for e in ents(snap) if e.get("crop")]
        targets = self.available_targets(snap, "harvest", harvestable(snap))
        if limit is not None:
            targets = targets[:max(0, limit)]
        self.log("  作物 %d 格，其中成熟 %d 格" % (len(crops), len(targets)))
        got = 0
        for e in targets:
            snap, progress = await self.work_target(snap, "harvest", e)
            if progress:
                got += 1
        self.last_harvest = got
        self.log("  实际收了：%d" % got)
        return snap

    async def plant(self, snap, limit=None):
        seeds = seeds_in(snap)
        plots = self.available_targets(snap, "plant", empty_tilled(snap))
        if limit is not None:
            plots = plots[:max(0, limit)]
        if not seeds:
            self.log("  背包里没有种子，跳过种植")
            return snap
        if not plots:
            self.log("  没有翻好的空地，跳过种植")
            return snap
        self.log("  种子：%s x%s，空地 %d 格" % (seeds[0].get("name"), seeds[0].get("count"), len(plots)))
        planted = 0
        for e in plots:
            seeds = seeds_in(snap)
            if not seeds:
                break
            snap, progress = await self.work_target(snap, "plant", e, seeds[0]["slot"])
            if progress:
                planted += 1
        self.last_planted = planted
        self.log("  实际种了：%d" % planted)
        return snap

    async def retry(self, fn, what="动作", times=3, delay=1.5):
        """遇到 BUSY（动画/过场/菜单）就等一下再试。"""
        last = None
        for i in range(times):
            try:
                return await fn()
            except Exception as e:  # noqa: BLE001
                last = e
                if "BUSY" in str(e) or "TRANSITION" in str(e):
                    self.log("  忙，等一下再试（%d/%d）" % (i + 1, times))
                    await asyncio.sleep(delay)
                    continue
                raise
        raise last

    async def go_outside(self, snap):
        exit_e = None
        for attempt in range(3):
            if (snap.get("location") or "") != "FarmHouse":
                return snap
            for e in ents(snap):
                if e.get("kind") == "exit" and "Farm" in (e.get("name") or ""):
                    exit_e = e
                    break
            if exit_e:
                break
            self.log("  没看到出口（第%d次），重看一下" % (attempt + 1))
            await asyncio.sleep(1.0)
            snap = await self.snap(16)
        if not exit_e:
            self.log("  找不到出口，出不去")
            return snap
        try:
            await self.retry(lambda: self.b.move(exit_e["tile"]["x"], exit_e["tile"]["y"]))
        except Exception as e:  # noqa: BLE001
            self.log("  走到出口失败：%s" % e)
            return snap
        await asyncio.sleep(1.2)
        snap = await self.snap(16)
        self.log("  现在在：%s" % snap.get("location"))
        return snap

    async def till(self, snap, limit=3):
        hoe = hoe_slot(snap)
        if not hoe:
            return snap
        # 挑离自己最近的可耕地，远处的经常走不过去
        pos = (snap.get("player") or {}).get("position") or {}
        px, py = pos.get("x", 0), pos.get("y", 0)
        cands = []
        for e in ents(snap):
            if (not e.get("diggable") or e.get("tilled") or not in_plot(e.get("tile"))
                    or not self.history.allowed(snap, "till", e)):
                continue
            t = e.get("tile") or {}
            cands.append((abs(t.get("x", 0) - px) + abs(t.get("y", 0) - py), e))
        cands.sort(key=lambda x: x[0])
        tiles = [e for d, e in cands[:limit]]
        if not tiles:
            self.log("  附近没有可耕地")
            return snap
        self.log("  要翻的地：%d 格（最近 %s）" % (len(tiles), tiles[0].get("tile")))
        done = 0
        for e in tiles:
            snap, progress = await self.work_target(snap, "till", e, hoe["slot"])
            if progress:
                done += 1
        self.last_till = done
        self.log("  实际翻地：%d" % done)
        return snap

    async def clear_obstacles(self, snap, limit=8, min_energy=70, zone="cluster",
                              need=None, picks=None):
        """清农场上的障碍。

        zone: none / cluster（只清开垦区内外 1 格）/ path（只清挡路的）/ all
        need: 还缺几格耕地；不给就按 limit 当缺口（老调用方式）
        picks: 只清候选清单里这几号（选择题模式用）
        """
        import farm_layout

        self.last_cleared = 0
        self.last_cleared_in = 0
        self.last_cleared_out = 0
        self.last_seed_gain = []
        seeds_before = {i.get("slot"): i.get("count", 0) for i in seeds_in(snap)}
        need_v = int(need) if need is not None else int(limit)
        if (zone or "cluster").lower() == "none" or need_v <= 0:
            self.log("  不清障碍（zone=%s 缺口=%s）" % (zone, need_v))
            return snap

        cands = farm_layout.clear_candidates(snap, zone=zone, need=need_v, max_n=int(limit))
        # 持续行动：冷却中的目标不再重复尝试（失败记忆在动作历史里）
        cands = [c for c in cands if self.history.allowed(snap, "clear", c)]
        if picks:
            picks = set(int(x) for x in picks)
            cands = [c for c in cands if c.get("i") in picks]
        if not cands:
            self.log("  需要的地方没障碍（zone=%s 缺口=%d）" % (zone, need_v))
            return snap

        self.log("  要清 %d 格（zone=%s 缺口=%d）：%s" % (
            len(cands), zone, need_v,
            "、".join("%s%s" % (c["name"], c["tile"]) for c in cands[:4])))

        done = 0
        for c in cands:
            energy = (snap.get("player") or {}).get("energy") or 0
            if not self.dry and energy < min_energy:
                self.log("  体力只剩 %s，停止清理" % energy)
                break
            it = tool_slot(snap, c["tool"])
            if not it:
                self.log("  背包里没有 %s（%s 清不了）" % (c["tool"], c["name"]))
                continue
            snap, progress = await self.work_target(snap, "clear", c, it["slot"])
            if progress:
                done += 1
                if c.get("in_cluster"):
                    self.last_cleared_in += 1
                else:
                    self.last_cleared_out += 1
        self.last_cleared = done
        for it in seeds_in(snap):
            was = seeds_before.get(it.get("slot"), 0)
            if it.get("count", 0) > was:
                self.last_seed_gain.append((it.get("name"), it.get("count", 0) - was))
        if self.last_seed_gain:
            self.log("  掉了种子：%s" % ", ".join("%s x%d" % g for g in self.last_seed_gain))
        self.log("  清了 %d 个障碍（区内 %d / 区外 %d，zone=%s）" % (
            done, self.last_cleared_in, self.last_cleared_out, zone))
        return snap

    def _find_house_door(self, snap):
        # 只认真农舍的门，别把温室、鸡舍的门当成家
        for e in ents(snap):
            if e.get("kind") == "door" and "农舍" in (e.get("name") or ""):
                return e
        return None

    def _passable_grid(self, snap):
        grid = {}
        for e in ents(snap):
            if "passable" not in e:
                continue
            t = e.get("tile") or {}
            grid[(t.get("x"), t.get("y"))] = bool(e.get("passable"))
        return grid

    def _bfs_path(self, snap, target):
        """在快照的可走格子上 BFS，返回从角色到目标的路径。"""
        from collections import deque
        grid = self._passable_grid(snap)
        pos = (snap.get("player") or {}).get("position") or {}
        start = (pos.get("x"), pos.get("y"))
        goal = (target[0], target[1])
        if start not in grid:
            return None
        prev = {start: None}
        q = deque([start])
        while q:
            cur = q.popleft()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nxt = (cur[0] + dx, cur[1] + dy)
                if nxt in prev or not grid.get(nxt):
                    continue
                prev[nxt] = cur
                q.append(nxt)
        end = goal if goal in prev else None
        if end is None:
            # 目标不可达时，退到能到的、离目标最近的点
            best = None
            for p in prev:
                d = abs(p[0] - goal[0]) + abs(p[1] - goal[1])
                if best is None or d < best[0]:
                    best = (d, p)
            if not best:
                return None
            end = best[1]
        path = []
        c = end
        while c is not None:
            path.append(c)
            c = prev[c]
        path.reverse()
        return path

    async def walk_to(self, snap, tx, ty, tries=15, arrive=1, chunk=6):
        """自己找路，一段一段往目标挪（比 mod 自带寻路抗堵）。"""
        for _ in range(tries):
            pos = (snap.get("player") or {}).get("position") or {}
            if abs(pos.get("x", 0) - tx) + abs(pos.get("y", 0) - ty) <= arrive:
                return snap
            path = self._bfs_path(snap, (tx, ty))
            if not path or len(path) < 2:
                if (snap.get("location") or "") == "Farm":
                    self.log("  路被堵住，先清一下再走")
                    snap = await self.clear_toward(snap, tx, ty, actions=3)
                    continue
                self.log("  找不到去 %s,%s 的路" % (tx, ty))
                return snap
            step = path[min(len(path) - 1, chunk)]
            try:
                await self.b.move(step[0], step[1])
            except Exception as e:  # noqa: BLE001
                self.log("  挪不动：%s" % e)
                snap = await self.snap(12)
                if "BUSY" in str(e):
                    snap = await self.clear_menu(snap)
                    await asyncio.sleep(0.5)
                    snap = await self.snap(12)
                continue
            snap = await self.snap(12)
        return snap

    async def clear_toward(self, snap, tx, ty, actions=14):
        # 镇上砍树会被判违规（会弹对话），只在农场清
        """把挡在去目标路上的东西清掉（贴着已通区域、离目标最近的那个）。"""
        if (snap.get("location") or "") != "Farm":
            return snap
        cleared = 0
        for _ in range(actions):
            reach = set(self._bfs_path(snap, (tx, ty)) or [])
            if (tx, ty) in reach:
                self.log("  路通了（清了 %d 下）" % cleared)
                return snap
            grid = self._passable_grid(snap)
            best = None
            for e in ents(snap):
                tool = CLEAR_TOOLS.get(e.get("name") or "")
                if not tool:
                    continue
                t = e.get("tile") or {}
                p = (t.get("x"), t.get("y"))
                if grid.get(p):
                    continue
                near = any((p[0] + dx, p[1] + dy) in reach for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))
                if not near:
                    continue
                d = abs(p[0] - tx) + abs(p[1] - ty)
                if best is None or d < best[0]:
                    best = (d, e, tool)
            if not best:
                self.log("  前面没可清的东西了")
                return snap
            _, e, tool = best
            it = tool_slot(snap, tool)
            if not it:
                self.log("  缺工具 %s" % tool)
                return snap
            try:
                await self.b.move(near=e["ref"])
                await self.b.select(it["slot"])
                r = await self.b.use(e["ref"])
                cleared += 1
                snap = (r or {}).get("snapshot") or await self.snap(12)
            except Exception as ex:  # noqa: BLE001
                self.log("  清 %s 失败：%s" % (e.get("name"), ex))
                snap = await self.snap(12)
        return snap
    async def chop_trees(self, snap, want_wood=60, max_swings=60):
        """砍树攒木头：盯着一棵砍到底，再换下一棵。"""
        axe = tool_slot(snap, "Axe")
        if not axe:
            self.log("  没有斧头")
            return snap
        self.last_chops = 0
        target_tile = None
        for _ in range(max_swings):
            wood = sum(i.get("count", 0) for i in snap.get("inventory") or [] if (i.get("name") or "") in ("木材", "木头"))
            if wood >= want_wood:
                self.log("  木头够了：%d" % wood)
                break
            if ((snap.get("player") or {}).get("energy") or 0) < 30:
                self.log("  体力不够了")
                break
            trees = self.available_targets(snap, "chop", [e for e in ents(snap)
                                                          if (e.get("name") or "") in ("Tree", "树")])
            if not trees:
                self.log("  附近没树了")
                break
            # 优先继续砍同一棵
            tree = None
            if target_tile:
                tree = next((e for e in trees if e.get("tile") == target_tile), None)
                if tree is None:
                    self.log("  这棵砍倒了，换一棵")
                    target_tile = None
            if tree is None:
                pos = (snap.get("player") or {}).get("position") or {}
                trees.sort(key=lambda e: abs((e.get("tile") or {}).get("x", 0) - pos.get("x", 0)) + abs((e.get("tile") or {}).get("y", 0) - pos.get("y", 0)))
                tree = trees[0]
                target_tile = tree.get("tile")
            snap, progress = await self.work_target(snap, "chop", tree, axe["slot"])
            if progress:
                self.last_chops += 1
            else:
                target_tile = None
        wood = sum(i.get("count", 0) for i in snap.get("inventory") or [] if (i.get("name") or "") in ("木材", "木头"))
        self.log("  砍了 %d 下，木头 %d" % (self.last_chops, wood))
        return snap

    async def go_inside(self, snap):
        if (snap.get("location") or "") == "FarmHouse":
            return snap
        door = self._find_house_door(snap)
        if not door:
            # 人在农场远处时看不到门，先往记住的农舍位置走
            home = self.state.get("house_door") or {"x": 64, "y": 14}
            self.log("  看不到门，先往农舍走（%s,%s）" % (home.get("x"), home.get("y")))
            for _ in range(4):
                snap = await self.walk_to(snap, home["x"], home["y"], tries=25)
                await asyncio.sleep(1.0)
                snap = await self.snap(16)
                if (snap.get("location") or "") == "FarmHouse":
                    return snap
                door = self._find_house_door(snap)
                if door:
                    break
        if not door:
            self.log("  还是找不到门，回不了家")
            return snap
        self.state["house_door"] = door.get("tile")
        self.save_state()
        await self.do("走到门口", self.b.move(near=door["ref"]))
        await self.do("进门", self.b.interact(door["ref"]))
        await asyncio.sleep(1.0)
        snap = await self.snap()
        if (snap.get("location") or "") == "FarmHouse":
            return snap
        await self.do("踩门格", self.b.move(door["tile"]["x"], door["tile"]["y"]))
        await asyncio.sleep(1.0)
        return await self.snap()

    async def _try_sleep_target(self, target):
        snap = None
        for label, fn in (("交互", self.b.interact), ("菜单", self.b.menu)):
            try:
                r = await fn(target["ref"])
                snap = (r or {}).get("snapshot") or await self.snap(16)
                if (snap or {}).get("menu"):
                    return snap
            except Exception as e:  # noqa: BLE001
                self.log("  %s 不行：%s" % (label, e))
        return snap or await self.snap(16)

    async def sleep(self, snap):
        # 睡觉前重新看一眼大范围（刚才的截图可能太近，看不到门）
        snap = await self.snap(16)
        snap_before = snap
        snap = await self.go_inside(snap)
        snap = await self.snap(16)
        bed = by_kind(snap, "bed")
        act = find_action(snap, "Sleep")
        if not bed and not act:
            self.log("  找不到床，睡不了（地图 %s）" % snap.get("location"))
            return None
        slept = False
        target = act or bed[0]
        try:
            await self.retry(lambda: self.b.move(near=target["ref"]))
        except Exception as e:
            self.log("  走到床边失败：%s" % e)
        snap2 = await self._try_sleep_target(target)
        menu = (snap2 or {}).get("menu")
        if not menu:
            r = await self.do("踩到床上", self.b.move(target["tile"]["x"], target["tile"]["y"]))
            # 菜单的 ref 跟生成它的那份快照绑定，必须用这一步返回的快照
            snap2 = (r or {}).get("snapshot") or await self.snap(16)
            menu = (snap2 or {}).get("menu")
        if menu:
            opts = menu.get("options") or []
            yes = None
            for o in opts:
                if (o.get("label") or o.get("name") or "").strip() in ("是", "Yes", "确定"):
                    yes = o
                    break
            if yes:
                await self.do("确认睡觉", self.b.menu(yes["ref"]))
                slept = True
            else:
                self.log("  菜单里没找到「是」：%s" % [o.get("label") or o.get("name") for o in opts])

        # 睡完会停在黑屏，要点一下才进新的一天
        if menu and not slept:
            slept = True   # 对话框已经弹出来了，点一下就等于选「是」
        if not slept:
            self.log("  没确认睡觉，不点屏幕")
            return snap2
        day0 = (snap_before or {}).get("date")
        spots = [(0.5, 0.5), (0.597, 0.686), (0.5, 0.62), (0.5, 0.72)]
        for i in range(12):
            rx, ry = spots[i % len(spots)]
            try:
                if rx == 0.5 and ry == 0.5:
                    click_game_center()
                else:
                    click_at_ratio(rx, ry)
            except Exception:
                pass
            await asyncio.sleep(1.6)
            try:
                s = await self.snap(8)
            except Exception as e:  # noqa: BLE001
                self.log("  过场里读不到快照，再等：%s" % type(e).__name__)
                await asyncio.sleep(1.5)
                continue
            if (s or {}).get("date") != day0:
                self.log("  新的一天：%s" % ((s or {}).get("date"),))
                return s
        self.log("  点了还是没进新的一天")
        try:
            return await self.snap(8)
        except Exception:
            return None


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--peek", action="store_true")
    ap.add_argument("--day", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--till", type=int, default=0, help="先翻几格地再浇（没有种子时用来试动作）")
    ap.add_argument("--sleep", action="store_true", help="干完活就回屋睡觉")
    a = ap.parse_args()

    async with Bridge() as b:
        f = Farmer(b, dry=a.dry)
        snap = await f.snap()
        p = snap.get("player") or {}
        print("== %s 第%s天 %s %s ==" % (snap.get("location"), (snap.get("date") or {}).get("day"),
                                        (snap.get("date") or {}).get("season"), snap.get("time")))
        print("位置 %s 体力 %s/%s 钱 %s" % (p.get("position"), p.get("energy"), p.get("maxEnergy"),
                                          (snap.get("player") or {}).get("money")))
        print("背包 %s" % ", ".join("%s x%s" % (i.get("name"), i.get("count")) for i in snap.get("inventory") or []))
        print("作物 %d，缺水 %d，可收 %d，空地 %d，水壶 %s" % (
            sum(1 for e in ents(snap) if e.get("crop")),
            len(water_targets(snap)), len(harvestable(snap)), len(empty_tilled(snap)),
            (water_can(snap) or {}).get("water")))
        if a.peek or not a.day:
            return 0

        snap = await f.clear_menu(snap)
        snap = await f.go_outside(snap)
        snap = await f.refill_if_needed(snap)
        snap = await f.harvest(snap)
        if a.till > 0:
            snap = await f.till(snap, limit=a.till)
        snap = await f.plant(snap)
        snap = await f.water_crops(snap)
        snap2 = None
        if a.sleep or (snap.get("time") or 0) >= 2200:
            snap2 = await f.sleep(snap)
        if snap2:
            print("睡下后：%s 第%s天 %s" % (snap2.get("location"), (snap2.get("date") or {}).get("day"),
                                          snap2.get("time")))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
