"""命令行入口：配置、组装、启动/停止/状态与证据导出。

用法示例（预算与限频必须显式给出）::

    stardew-agent run --config config.json
    stardew-agent status --config config.json
    stardew-agent stop --config config.json
    stardew-agent doctor --config config.json
    stardew-agent export --config config.json --out evidence.json
    stardew-agent skills list --config config.json
    stardew-agent skills retire --config config.json --id skill:xxx --reason "前提失效"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from ..contracts.errors import AgentError, ConfigError
from ..experience import ExperienceStore
from ..runtime import StopFile
from . import wiring
from .config import AgentConfig, load_config


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, help="运行配置 JSON（含预算与限频）")
    parser.add_argument("--db", help="经验库路径")
    parser.add_argument("--state", help="状态文件路径")
    parser.add_argument("--stop-file", help="停止文件路径")


def _add_budget(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--seconds", type=float, help="本次运行墙钟上限（秒）")
    parser.add_argument("--requests", type=int, help="本次运行模型请求上限")
    parser.add_argument("--repeat-window", type=float, help="同一动作限频窗口（秒）")
    parser.add_argument("--repeat-max", type=int, help="同一动作累计执行上限")
    parser.add_argument(
        "--model-interval",
        type=float,
        help="两次模型请求之间的最小间隔（秒），0 表示不限制",
    )
    parser.add_argument(
        "--model-completion",
        action="store_true",
        help="采用模型提出的 completion 判据（默认不使用，仅由能力验证成败）",
    )
    parser.add_argument(
        "--expose-mechanics",
        action="store_true",
        help="把 use/select_item 等底层机械动作也暴露给模型（默认只给 farm_routine 用）",
    )
    parser.add_argument(
        "--repeat-exempt",
        help="限频白名单：逗号分隔的能力 id（例如 select_item），这些能力不参与限频",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stardew-agent", description="学习型星露谷机器人")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="按配置实时运行一次")
    _add_common(run)
    _add_budget(run)
    run.add_argument("--goal", help="覆盖长期目标文字（不自动变成固定日程）")
    run.add_argument("--radius", type=int, help="观察半径")
    run.add_argument("--poll", type=float, help="无委托时的轮询间隔（秒）")
    run.add_argument("--max-actions", type=int, help="最多下发多少个委托（工程验收用）")
    run.add_argument("--endpoint", help="游戏桥地址")
    run.add_argument("--model-config", type=Path, help="模型配置 JSON")
    run.add_argument("--embedding-dir", type=Path, help="本地向量模型目录")
    run.add_argument("--no-fishing", action="store_true", help="不注册钓鱼能力（Mod 未安装时）")
    run.add_argument("--quiet", action="store_true", help="不在控制台实时打印目标/动作/结果")

    status = sub.add_parser("status", help="查看上次运行、经验库与技能状态")
    _add_common(status)
    _add_budget(status)

    stop = sub.add_parser("stop", help="请求停止正在运行的 Agent")
    _add_common(stop)
    _add_budget(stop)

    doctor = sub.add_parser("doctor", help="只读自检：配置、模型、Mod、向量与存储")
    _add_common(doctor)
    _add_budget(doctor)
    doctor.add_argument("--no-probe", action="store_true", help="不连接游戏桥")
    doctor.add_argument("--game-path", help="星露谷安装目录")
    doctor.add_argument("--endpoint", help="游戏桥地址")

    export = sub.add_parser("export", help="导出脱敏证据")
    _add_common(export)
    _add_budget(export)
    export.add_argument("--out", type=Path, required=True, help="输出 JSON 路径")
    export.add_argument("--event-limit", type=int, default=5000)

    stats = sub.add_parser("stats", help="统计历史行动的拦截/失败占比")
    _add_common(stats)
    _add_budget(stats)
    stats.add_argument("--save-key", help="只统计某个存档（默认全部存档）")
    stats.add_argument("--days", type=float, help="只统计最近 N 天（默认全部历史）")

    skills = sub.add_parser("skills", help="查看或停用组合技能")
    _add_common(skills)
    _add_budget(skills)
    skills_sub = skills.add_subparsers(dest="action", required=True)
    skills_sub.add_parser("list", help="列出所有技能版本")
    retire = skills_sub.add_parser("retire", help="停用一个技能版本")
    retire.add_argument("--id", required=True)
    retire.add_argument("--version", type=int)
    retire.add_argument("--reason", required=True)
    return parser


def _split_exempt(value: str | None) -> list[str] | None:
    if not value:
        return None
    names = [part.strip() for part in value.split(",") if part.strip()]
    return names or None


def _overrides(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "budget_seconds": getattr(args, "seconds", None),
        "budget_requests": getattr(args, "requests", None),
        "repeat_window_seconds": getattr(args, "repeat_window", None),
        "repeat_max_runs": getattr(args, "repeat_max", None),
        "model_request_interval_seconds": getattr(args, "model_interval", None),
        "model_completion_enabled": True if getattr(args, "model_completion", False) else None,
        "expose_mechanics": True if getattr(args, "expose_mechanics", False) else None,
        "repeat_exempt_abilities": _split_exempt(getattr(args, "repeat_exempt", None)),
        "long_term_goal": getattr(args, "goal", None),
        "snapshot_radius": getattr(args, "radius", None),
        "poll_seconds": getattr(args, "poll", None),
        "bridge_endpoint": getattr(args, "endpoint", None),
        "experience_db": getattr(args, "db", None),
        "model_config": getattr(args, "model_config", None),
        "embedding_dir": getattr(args, "embedding_dir", None),
        "state_path": getattr(args, "state", None),
        "stop_path": getattr(args, "stop_file", None),
        "game_path": getattr(args, "game_path", None),
        "console_output": False if getattr(args, "quiet", False) else None,
    }


def _config(args: argparse.Namespace) -> AgentConfig:
    return load_config(getattr(args, "config", None), overrides=_overrides(args))


def _print(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = _config(args)
        if args.command == "run":
            payload = asyncio.run(
                wiring.run_agent(
                    config,
                    max_actions=getattr(args, "max_actions", None),
                    include_fishing=False if getattr(args, "no_fishing", False) else None,
                )
            )
            _print(payload)
            return 0
        if args.command == "status":
            _print(wiring.status_report(config))
            return 0
        if args.command == "stop":
            written = StopFile(config.stop_path).request("cli-stop")
            _print({"stop_file": str(config.stop_path), "written": written})
            return 0
        if args.command == "doctor":
            payload = asyncio.run(wiring.doctor_report(config, probe_bridge=not getattr(args, "no_probe", False)))
            _print(payload)
            return 0
        if args.command == "export":
            _print(wiring.export_evidence(config, args.out, event_limit=args.event_limit))
            return 0
        if args.command == "stats":
            _print(_stats_payload(config, args))
            return 0
        if args.command == "skills":
            return _skills_command(config, args)
        raise ConfigError("未知命令：%s" % args.command)
    except (ConfigError, AgentError) as exc:
        print("配置/运行错误：%s" % exc, file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print("文件不存在：%s" % exc, file=sys.stderr)
        return 2


def _stats_payload(config: AgentConfig, args: argparse.Namespace) -> dict:
    """聚合历史行动的拦截/失败占比；不启动游戏、不请求模型。"""
    if not Path(config.experience_db).exists():
        return {
            "attempts": 0,
            "executed": 0,
            "blocked": 0,
            "reason": "经验库还不存在",
            "summary_line": "行动 0 次",
        }
    store = ExperienceStore(config.experience_db)
    try:
        since = time.time() - args.days * 86400 if getattr(args, "days", None) else None
        return store.action_stats(save_key=getattr(args, "save_key", None), since_ts=since)
    finally:
        store.close()


def _skills_command(config: AgentConfig, args: argparse.Namespace) -> int:
    if not Path(config.experience_db).exists():
        _print({"skills": [], "reason": "经验库还不存在"})
        return 0
    store = ExperienceStore(config.experience_db)
    try:
        if args.action == "list":
            _print(
                [
                    {
                        "id": row["id"],
                        "version": row["version"],
                        "status": row["status"],
                        "claim": (row["definition"] or {}).get("claim"),
                        "retired_reason": row["retired_reason"],
                    }
                    for row in store.list_skills()
                ]
            )
            return 0
        store.retire_skill(args.id, args.reason, version=args.version)
        _print({"retired": args.id, "version": args.version, "reason": args.reason})
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
