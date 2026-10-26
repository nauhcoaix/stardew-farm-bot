"""独立钓鱼技能客户端：只与钓鱼 Mod 的文件邮箱通信，不修改农务调度器。"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


MOD_ID = "StardewFarmBot.StandaloneFishingSkill"
DEFAULT_GAME = Path(os.environ.get("STARDEW_GAME_PATH") or
                    r"C:\Program Files (x86)\Steam\steamapps\common\Stardew Valley")


def read_status(runtime, max_age=5):
    # Mod 用「写临时文件再替换」发布状态，读取那一瞬间可能正好撞上替换：短暂重试。
    status, last = None, None
    for _ in range(5):
        try:
            status = json.loads((runtime / "status.json").read_text(encoding="utf-8-sig"))
            break
        except (OSError, json.JSONDecodeError) as exc:
            last = exc
            time.sleep(0.04)
    if status is None:
        raise ValueError("无法读取钓鱼现场状态；先用 SMAPI 启动游戏并加载 Mod") from last
    try:
        if status.get("version") != 1 or status.get("mode") != "game":
            raise ValueError("不支持的钓鱼状态格式")
        observed = datetime.fromisoformat(status["observedUtc"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - observed).total_seconds()
        if not -2 <= age <= max_age:
            raise ValueError("钓鱼状态已过期，游戏可能关闭、暂停更新或未加载 Mod")
        return status
    except (KeyError, TypeError) as exc:
        raise ValueError("无法读取钓鱼现场状态；先用 SMAPI 启动游戏并加载 Mod") from exc


def ensure_farmer_stopped():
    if os.name != "nt":
        return
    # 只读查询；不结束进程、不写 STOP。原农务服务由已有控制方式停稳。
    query = ("Get-CimInstance Win32_Process -Filter \"Name = 'python.exe' OR Name = 'pythonw.exe'\" "
             "| Where-Object { $_.CommandLine -match 'farm_service\\.py' } "
             "| Select-Object -ExpandProperty ProcessId")
    found = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", query],
                           capture_output=True, text=True, timeout=10,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if found.returncode != 0:
        raise ValueError("无法检查农务服务状态，请先确认 farm_service.py 已停止")
    if found.stdout.strip():
        raise ValueError("farm_service.py 仍在运行；先用原 STOP 机制停稳，钓鱼需要独占角色")


def submit_command(runtime, action, limits=None, excluded=None, run_id=None):
    """原子提交。服务协调器用它异步等确认；stop 可在心跳过期时排队取消。"""
    command = {"id": uuid.uuid4().hex, "action": action,
               "issuedAt": datetime.now(timezone.utc).isoformat(), "limits": limits or {}}
    if excluded is not None:
        command["excluded"] = list(excluded)
    if run_id is not None:
        command["runId"] = run_id
    path = runtime / "command.json"
    lock = runtime / "command.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ValueError("另一个客户端正在写指令，请稍后查看状态") from exc
    temporary = runtime / ("command-" + command["id"] + ".tmp")
    try:
        os.close(fd)
        if path.exists():
            raise ValueError("已有待处理指令；不要覆盖或重复 start")
        temporary.write_text(json.dumps(command, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)
    return command


def send(runtime, action, limits=None, ack_timeout=5):
    status = read_status(runtime)
    if action == "start" and status.get("active"):
        raise ValueError("已有钓鱼任务在运行；查看状态或停止后再启动")
    command = submit_command(runtime, action, limits)
    deadline = time.monotonic() + ack_timeout
    while time.monotonic() < deadline:
        status = read_status(runtime)
        ack = status.get("ack") or {}
        if ack.get("id") == command["id"]:
            if not ack.get("accepted"):
                raise ValueError("钓鱼指令被拒绝：" + str(ack.get("reason")))
            return status
        time.sleep(0.1)
    raise ValueError("游戏没有确认指令，结果未知；先查询 status，不要重复 start")


def wait_run(runtime, run_id, timeout):
    deadline = time.monotonic() + timeout
    previous = None
    while time.monotonic() < deadline:
        status = read_status(runtime)
        if status.get("runId") != run_id:
            raise ValueError("游戏中的任务已被替换或 Mod 已重启，停止等待旧任务")
        phase = (status.get("state"), status.get("phase"), status.get("casts"), status.get("fish"))
        if phase != previous:
            print("钓鱼：状态=%s 阶段=%s 竿=%s 鱼=%s" % phase, file=sys.stderr, flush=True)
            previous = phase
        if not status.get("active"):
            return status
        time.sleep(0.5)
    raise ValueError("等待任务超时；查询 status 或执行 stop，未确认完成")


def validate_limits(limits):
    for key, low, high in (("maxCasts", 1, 100), ("targetFish", 0, 100),
                           ("maxSeconds", 10, 1800), ("minEnergy", 15, 270), ("stopAt", 600, 2300)):
        if not low <= limits[key] <= high:
            raise ValueError("参数 %s 应在 %d～%d 之间" % (key, low, high))
    if limits["stopAt"] % 100 >= 60:
        raise ValueError("stop-at 使用游戏 HHMM 时间，例如 2200")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("doctor", "start", "status", "stop"))
    parser.add_argument("--game-path", type=Path, default=DEFAULT_GAME)
    parser.add_argument("--max-casts", type=int, default=5)
    parser.add_argument("--target-fish", type=int, default=0)
    parser.add_argument("--max-seconds", type=int, default=300)
    parser.add_argument("--min-energy", type=int, default=30)
    parser.add_argument("--stop-at", type=int, default=2200)
    parser.add_argument("--facing", choices=("up", "right", "down", "left"))
    parser.add_argument("--wait", action="store_true")
    args = parser.parse_args(argv)
    mod = args.game_path.resolve() / "Mods" / "StandaloneFishingSkill"
    runtime = mod / "runtime"
    try:
        manifest_path = mod / "manifest.json"
        installed = (manifest_path.exists() and json.loads(manifest_path.read_text(encoding="utf-8-sig")).get("UniqueID") == MOD_ID
                     and (mod / "StandaloneFishingSkill.dll").exists())
        if args.command == "doctor":
            result = {"gamePath": str(args.game_path), "installed": installed,
                      "gameDll": (args.game_path / "Stardew Valley.dll").exists(),
                      "smapiDll": (args.game_path / "StardewModdingAPI.dll").exists()}
            try:
                result["status"] = read_status(runtime)
            except ValueError as exc:
                result["statusReason"] = str(exc)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if installed else 2
        if not installed:
            raise ValueError("独立钓鱼 Mod 未安装；先运行本技能 scripts/install.ps1")
        if args.command == "status":
            result = read_status(runtime)
        elif args.command == "stop":
            result = send(runtime, args.command)
        else:
            limits = {"maxCasts": args.max_casts, "targetFish": args.target_fish,
                      "maxSeconds": args.max_seconds, "minEnergy": args.min_energy,
                      "stopAt": args.stop_at,
                      "facing": ("up", "right", "down", "left").index(args.facing) if args.facing else -1}
            validate_limits(limits)
            ensure_farmer_stopped()
            result = send(runtime, "start", limits)
            if args.wait:
                try:
                    result = wait_run(runtime, result["runId"], args.max_seconds + 10)
                except KeyboardInterrupt:
                    result = send(runtime, "stop")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if args.command != "start" or result.get("active") or result.get("state") == "completed" else 3
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print("钓鱼受阻：" + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
