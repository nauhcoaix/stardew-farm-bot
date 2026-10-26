"""走到镇上，沿街截图，找皮埃尔杂货店的门。"""
import asyncio
import os
import subprocess
import sys

DEV = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DEV)
from farm_client import Bridge  # noqa: E402
from farmer import Farmer, ents  # noqa: E402

CAP = r"""
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;using System.Runtime.InteropServices;
public struct R9 { public int Left,Top,Right,Bottom; }
public static class G9 { [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out R9 r); [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint f); }
"@
$p = Get-Process -Name 'StardewModdingAPI' | Select-Object -First 1
$r = New-Object R9
[void][G9]::GetWindowRect($p.MainWindowHandle, [ref]$r)
$bmp = New-Object System.Drawing.Bitmap(($r.Right-$r.Left), ($r.Bottom-$r.Top))
$g = [System.Drawing.Graphics]::FromImage($bmp)
$hdc = $g.GetHdc()
[void][G9]::PrintWindow($p.MainWindowHandle, $hdc, 2)
$g.ReleaseHdc($hdc)
$g.Dispose()
$bmp.Save('%s', [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
"""


def capture(path):
    script = CAP % path.replace("'", "''")
    subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True)


async def main():
    async with Bridge() as b:
        f = Farmer(b, state_path=DEV + r"\farm-state.json")
        s = await f.snap(16)
        print("起点", s.get("location"), (s.get("player") or {}).get("position"), flush=True)

        if s.get("location") != "Town":
            if s.get("location") == "FarmHouse":
                s = await f.go_outside(s)
            if s.get("location") == "Farm":
                for wp in [(60, 40), (55, 55), (45, 61)]:
                    s = await f.clear_toward(s, wp[0], wp[1], actions=8)
                    s = await f.walk_to(s, wp[0], wp[1], tries=20)
                try:
                    await b.move(41, 65)
                except Exception as e:
                    print("去森林失败", e, flush=True)
                await asyncio.sleep(2.5)
                s = await b.snapshot(16)
            if s.get("location") == "Forest":
                s = await f.walk_to(s, 115, 24, tries=25)
                try:
                    await b.move(120, 25)
                except Exception as e:
                    print("去镇上失败", e, flush=True)
                await asyncio.sleep(2.5)
                s = await b.snapshot(16)
        print("到镇上", s.get("location"), (s.get("player") or {}).get("position"), flush=True)

        waypoints = [(25, 75), (30, 55), (35, 40), (40, 28)]
        for i, wp in enumerate(waypoints):
            s = await f.walk_to(s, wp[0], wp[1], tries=20)
            pos = (s.get("player") or {}).get("position")
            path = DEV + r"\town_%d.png" % i
            capture(path)
            print("点%d %s 实际 %s 截图 %s" % (i, wp, pos, os.path.basename(path)), flush=True)
            do = [(e.get("name"), e.get("tile")) for e in ents(s) if e.get("kind") in ("exit", "door")]
            if do:
                print("  门/出口:", do, flush=True)


asyncio.run(main())
