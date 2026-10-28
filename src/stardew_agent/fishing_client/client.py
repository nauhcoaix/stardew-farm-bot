"""独立钓鱼 Mod 的文件邮箱客户端（来源 ``stardew_valley_dfy/skills/stardew-fishing/scripts/fish.py``）。

保留：``read_status`` / ``submit_command`` / ``send`` / ``wait_run`` / ``validate_limits``
提供的通信能力——读取状态并检查格式与心跳新鲜度、原子写命令避免覆盖未处理命令、
匹配命令确认并识别拒绝/未确认/任务被替换、按 runId 等任务结束、核验预算参数。

未迁移：检查旧 ``farm_service.py`` 进程的 CLI 规则、与旧执行器接线的一竿适配、旧 CLI 入口。

本模块不寻找岸边、不决定目的地、也不开启钓鱼：它只把模型已经决定的委托交给
游戏里的独立 Mod，并把真实状态读回来。命令写入是同步文件操作，运行时可放进线程。
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from ..contracts.errors import AgentError
from ..contracts.version import FISHING_STATUS_VERSION

MOD_ID = "StardewFarmBot.StandaloneFishingSkill"
DEFAULT_GAME = Path(
    os.environ.get("STARDEW_GAME_PATH") or r"C:\Program Files (x86)\Steam\steamapps\common\Stardew Valley"
)

#: 预算参数允许范围；与 Mod 侧保持一致
LIMIT_RANGES = {
    "maxCasts": (1, 100),
    "targetFish": (0, 100),
    "maxSeconds": (10, 1800),
    "minEnergy": (15, 270),
    "stopAt": (600, 2300),
}
FACING = ("up", "right", "down", "left")


class FishingError(AgentError, ValueError):
    """钓鱼通信/参数错误。保留 ``ValueError`` 语义，便于调用方按旧习惯处理。"""

    code = "FISHING"


def read_status(
    runtime: Path | str,
    *,
    max_age: float = 5,
    attempts: int = 5,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """读取 Mod 发布的状态；过期状态不算“当前成功”。"""
    runtime = Path(runtime)
    status, last = None, None
    for _ in range(attempts):
        try:
            status = json.loads((runtime / "status.json").read_text(encoding="utf-8-sig"))
            break
        except (OSError, json.JSONDecodeError) as exc:
            last = exc
            sleep(0.04)
    if status is None:
        raise FishingError("无法读取钓鱼现场状态；先用 SMAPI 启动游戏并加载 Mod") from last
    try:
        if status.get("version") != FISHING_STATUS_VERSION or status.get("mode") != "game":
            raise FishingError("不支持的钓鱼状态格式")
        observed = datetime.fromisoformat(str(status["observedUtc"]).replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - observed).total_seconds()
        if not -2 <= age <= max_age:
            raise FishingError("钓鱼状态已过期，游戏可能关闭、暂停更新或未加载 Mod")
        return status
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, FishingError):
            raise
        raise FishingError("无法读取钓鱼现场状态；先用 SMAPI 启动游戏并加载 Mod") from exc


def submit_command(
    runtime: Path | str,
    action: str,
    limits: Mapping[str, Any] | None = None,
    excluded: Any = None,
    run_id: str | None = None,
    *,
    command_id: str | None = None,
) -> dict:
    """原子提交指令；已有待处理指令时拒绝覆盖。"""
    runtime = Path(runtime)
    command = {
        "id": command_id or uuid.uuid4().hex,
        "action": action,
        "issuedAt": datetime.now(timezone.utc).isoformat(),
        "limits": dict(limits or {}),
    }
    if excluded is not None:
        command["excluded"] = list(excluded)
    if run_id is not None:
        command["runId"] = run_id
    path = runtime / "command.json"
    lock = runtime / "command.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise FishingError("另一个客户端正在写指令，请稍后查看状态") from exc
    temporary = runtime / ("command-" + command["id"] + ".tmp")
    try:
        os.close(fd)
        if path.exists():
            raise FishingError("已有待处理指令；不要覆盖或重复 start")
        temporary.write_text(json.dumps(command, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)
    return command


def send(
    runtime: Path | str,
    action: str,
    limits: Mapping[str, Any] | None = None,
    ack_timeout: float = 5,
    *,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict:
    """提交指令并等待游戏确认；未确认时报“结果未知”，不重发。"""
    runtime = Path(runtime)
    status = read_status(runtime, sleep=sleep)
    if action == "start" and status.get("active"):
        raise FishingError("已有钓鱼任务在运行；查看状态或停止后再启动")
    command = submit_command(runtime, action, limits)
    deadline = monotonic() + ack_timeout
    while monotonic() < deadline:
        status = read_status(runtime, sleep=sleep)
        ack = status.get("ack") or {}
        if ack.get("id") == command["id"]:
            if not ack.get("accepted"):
                raise FishingError("钓鱼指令被拒绝：" + str(ack.get("reason")))
            return status
        sleep(0.1)
    raise FishingError("游戏没有确认指令，结果未知；先查询 status，不要重复 start")


def wait_run(
    runtime: Path | str,
    run_id: str,
    timeout: float,
    *,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    report: Callable[[str], None] | None = None,
) -> dict:
    """等待指定 ``runId`` 的任务结束；任务被替换时报错而不是当成自己的成功。"""
    runtime = Path(runtime)
    deadline = monotonic() + timeout
    previous = None
    while monotonic() < deadline:
        status = read_status(runtime, sleep=sleep)
        if status.get("runId") != run_id:
            raise FishingError("游戏中的任务已被替换或 Mod 已重启，停止等待旧任务")
        phase = (status.get("state"), status.get("phase"), status.get("casts"), status.get("fish"))
        if phase != previous:
            if report is not None:
                report("钓鱼：状态=%s 阶段=%s 竿=%s 鱼=%s" % phase)
            previous = phase
        if not status.get("active"):
            return status
        sleep(0.5)
    raise FishingError("等待任务超时；查询 status 或执行 stop，未确认完成")


def validate_limits(limits: Mapping[str, Any]) -> None:
    """校验预算参数；缺少必需参数也要报错，避免用默认值兜底。"""
    for key, (low, high) in LIMIT_RANGES.items():
        if key not in limits:
            raise FishingError("缺少参数 %s" % key)
        value = limits[key]
        if not isinstance(value, int) or isinstance(value, bool):
            raise FishingError("参数 %s 必须是整数" % key)
        if not low <= value <= high:
            raise FishingError("参数 %s 应在 %d～%d 之间" % (key, low, high))
    if limits["stopAt"] % 100 >= 60:
        raise FishingError("stop-at 使用游戏 HHMM 时间，例如 2200")
    facing = limits.get("facing", -1)
    if facing is not None and (not isinstance(facing, int) or not -1 <= facing < len(FACING)):
        raise FishingError("facing 应为 -1（沿用当前朝向）或 0～3")


def is_installed(game_path: Path | str = DEFAULT_GAME) -> bool:
    """Mod 目录、manifest 与 dll 都在才算安装。"""
    mod = Path(game_path) / "Mods" / "StandaloneFishingSkill"
    manifest = mod / "manifest.json"
    if not manifest.exists() or not (mod / "StandaloneFishingSkill.dll").exists():
        return False
    try:
        return json.loads(manifest.read_text(encoding="utf-8-sig")).get("UniqueID") == MOD_ID
    except (OSError, ValueError):
        return False


def doctor(game_path: Path | str = DEFAULT_GAME, *, runtime: Path | str | None = None) -> dict:
    """只读自检：游戏、SMAPI、Mod 与状态文件是否就绪。"""
    game = Path(game_path)
    result: dict[str, Any] = {
        "gamePath": str(game),
        "installed": is_installed(game),
        "gameDll": (game / "Stardew Valley.dll").exists(),
        "smapiDll": (game / "StardewModdingAPI.dll").exists(),
    }
    mailbox = Path(runtime) if runtime else game / "Mods" / "StandaloneFishingSkill" / "runtime"
    try:
        result["status"] = read_status(mailbox)
    except FishingError as exc:
        result["statusReason"] = str(exc)
    return result

