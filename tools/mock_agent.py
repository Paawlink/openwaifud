#!/usr/bin/env python3
"""Push a deterministic fake agent session to the local OpenWaifuD API.

Run the daemon first, then start this script to exercise the UI/BLE display
without needing a real OpenCode, Claude Code, or Codex session.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


@dataclass(frozen=True)
class DemoStep:
    status: str
    task: str
    message: str


DEMO_STEPS = (
    DemoStep("thinking", "设计登录流程", "分析页面状态和认证流程"),
    DemoStep("coding", "实现登录流程", "修改登录表单与 API 请求"),
    DemoStep("testing", "验证登录流程", "运行登录相关测试用例"),
    DemoStep("idle", "等待下一项任务", "演示任务已完成"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8765",
        help="OpenWaifuD base URL (default: %(default)s)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Seconds between demo steps (default: %(default)s)",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=0,
        help="Number of cycles; 0 runs forever (default: %(default)s)",
    )
    parser.add_argument(
        "--sessions",
        type=int,
        default=1,
        help="并发模拟的会话数，用于触发固件情绪状态机档位 (default: %(default)s)",
    )
    parser.add_argument(
        "--task",
        default=None,
        help="Override the task text shown in every step",
    )
    parser.add_argument(
        "--status",
        choices=("idle", "thinking", "coding", "testing", "error"),
        help="Send one status update and exit",
    )
    parser.add_argument(
        "--error-message",
        default="模拟错误：用于测试错误状态展示",
        help="Error text used with --status error",
    )
    return parser.parse_args()


def post_json(base_url: str, path: str, payload: dict[str, object]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            if response.status >= 300:
                raise RuntimeError(f"HTTP {response.status}: {response.read().decode()}")
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"无法连接 {base_url}: {error.reason}") from error


def send_context(base_url: str, session_id: str, task: str, step: int) -> None:
    post_json(
        base_url,
        "/api/v1/context",
        {
            "plugin_type": "mock-agent",
            "session_id": session_id,
            "current_task": task,
            "metadata": {"demo": True, "step": step},
        },
    )


def send_status(base_url: str, status: str, session_id: str, error_message: str | None = None) -> None:
    post_json(
        base_url,
        "/api/v1/status",
        {"status": status, "error_message": error_message, "session_id": session_id},
    )


# 每个模拟会话使用不同的任务标签，便于在屏幕上区分
_SESSION_TASKS = (
    "实现登录流程",
    "重构状态管理",
    "修复列表滚动",
    "编写单元测试",
    "优化启动耗时",
    "接入蓝牙推送",
    "整理项目文档",
    "排查内存泄漏",
)


async def run_session(base_url: str, session_id: str, label: str, args: argparse.Namespace, start_delay: float) -> None:
    """模拟单个会话：错峰启动后循环 thinking/coding/testing/idle。"""
    await asyncio.sleep(start_delay)
    cycles = 0
    step_number = 0
    while args.repeat == 0 or cycles < args.repeat:
        for demo_step in DEMO_STEPS:
            step_number += 1
            task = args.task or f"{label}·{demo_step.task}"
            send_context(base_url, session_id, task, step_number)
            send_status(base_url, demo_step.status, session_id)
            print(f"[{session_id}] [{demo_step.status:8}] {task}")
            await asyncio.sleep(args.interval)
        cycles += 1


async def run(args: argparse.Namespace) -> int:
    if args.interval < 0:
        print("--interval 必须大于等于 0", file=sys.stderr)
        return 2
    if args.repeat < 0:
        print("--repeat 必须大于等于 0", file=sys.stderr)
        return 2
    if args.sessions < 1:
        print("--sessions 必须大于等于 1", file=sys.stderr)
        return 2

    try:
        if args.status:
            session_id = f"mock-{uuid4().hex[:8]}"
            send_context(args.url, session_id, args.task or "单状态展示", 1)
            send_status(
                args.url,
                args.status,
                session_id,
                args.error_message if args.status == "error" else None,
            )
            print(f"已发送状态: {args.status}")
            return 0

        print(f"模拟 agent 已启动: sessions={args.sessions}, url={args.url}")
        print("按 Ctrl-C 停止")

        # 多个会话错峰启动，可观察固件从“睡觉中”逐步升到“火力全开”
        tasks = []
        for i in range(args.sessions):
            session_id = f"mock-{uuid4().hex[:8]}"
            label = _SESSION_TASKS[i % len(_SESSION_TASKS)]
            start_delay = i * args.interval
            tasks.append(asyncio.create_task(run_session(args.url, session_id, label, args, start_delay)))
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        print("\n模拟 agent 已停止")
    except RuntimeError as error:
        print(f"错误: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(run(parse_args())))
    except KeyboardInterrupt:
        sys.exit(130)
