from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import as_public_summary, doctor, load_config
from .pipeline import run_pipeline
from .privacy import scan_repository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="internship-agent",
        description="本地监督式实习投递准备助手；只生成待确认材料，不发送邮件。",
    )
    parser.add_argument("--config", help="本地 TOML 配置文件路径")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run", help="读取来源并生成待确认申请材料")
    subparsers.add_parser("doctor", help="检查配置与本地文件")
    privacy = subparsers.add_parser("privacy-check", help="检查代码仓库是否混入敏感材料")
    privacy.add_argument("--repo", default=".", help="代码仓库路径")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "privacy-check":
        findings = scan_repository(Path(args.repo).resolve())
        print(json.dumps({"ok": not findings, "findings": findings}, ensure_ascii=False, indent=2))
        return 0 if not findings else 2
    if not args.config:
        raise SystemExit("--config is required for this command")
    config = load_config(args.config)
    if args.command == "doctor":
        problems = doctor(config)
        result = as_public_summary(config)
        result["ok"] = not problems
        result["problems"] = problems
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if not problems else 2
    summary = run_pipeline(config)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not summary["errors"] else 1
