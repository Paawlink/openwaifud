#!/usr/bin/env python3
"""Push realistic fake agent sessions to the local OpenWaifuD API.

Run the daemon first, then start this script to exercise the UI/BLE display
without needing a real OpenCode, Claude Code, or Codex session.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


@dataclass(frozen=True)
class DemoStep:
    status: str
    activity: str
    role: str
    message: str
    duration: float = 1.0


@dataclass(frozen=True)
class DemoScenario:
    task: str
    request: str
    directory: str
    agent: str
    branch: str
    steps: tuple[DemoStep, ...]


SCENARIOS = (
    DemoScenario(
        task="修复登录态过期后重复跳转",
        request="登录态失效时页面会连续跳转两次，请定位原因、修复并补上回归测试。",
        directory="~/workspace/nebula-console",
        agent="OpenCode",
        branch="fix/auth-redirect-loop",
        steps=(
            DemoStep(
                "thinking",
                "梳理认证回调与路由守卫",
                "assistant",
                "我先检查 token 刷新、路由守卫和 401 响应的调用关系。",
                1.1,
            ),
            DemoStep(
                "thinking", "检索重复导航的触发路径", "tool", "Search `router.replace` · 7 matches in 4 files", 0.8
            ),
            DemoStep(
                "coding",
                "合并并发的登录失效处理",
                "assistant",
                "发现响应拦截器和路由守卫会同时发起跳转，准备让失效处理只执行一次。",
                1.4,
            ),
            DemoStep("coding", "补充认证回归测试", "tool", "Edit src/auth/session.ts, tests/auth/session.test.ts", 1.1),
            DemoStep(
                "testing", "运行认证模块测试", "tool", "pnpm vitest run tests/auth/session.test.ts · 12 passed", 1.3
            ),
            DemoStep(
                "thinking",
                "检查改动影响范围",
                "assistant",
                "测试通过，类型检查也没有新增错误；改动仅影响登录失效后的导航去重。",
                0.7,
            ),
            DemoStep("idle", "登录跳转问题已修复", "assistant", "已修复重复跳转并补充并发 401 的回归测试。", 0.8),
        ),
    ),
    DemoScenario(
        task="优化大列表首屏渲染性能",
        request="订单列表首次打开很卡，请分析瓶颈并优化，不能改变筛选和排序行为。",
        directory="~/workspace/atlas-admin",
        agent="ClaudeCode",
        branch="perf/order-list",
        steps=(
            DemoStep(
                "thinking",
                "分析订单列表渲染链路",
                "assistant",
                "我先看列表数据流、行组件和筛选条件，确认是否存在重复计算。",
                1.0,
            ),
            DemoStep(
                "thinking", "定位高频重渲染组件", "tool", "Read OrderTable.tsx → OrderRow.tsx → useOrderFilters.ts", 0.9
            ),
            DemoStep(
                "coding",
                "下沉稳定的行数据映射",
                "assistant",
                "瓶颈来自每行重复格式化完整订单对象，将映射移到数据进入列表的位置。",
                1.5,
            ),
            DemoStep("coding", "减少不可见行的渲染工作", "tool", "Edit src/orders/OrderTable.tsx · +34 -21", 1.2),
            DemoStep("testing", "验证筛选排序行为", "tool", "pnpm test OrderTable · 18 passed", 1.2),
            DemoStep(
                "testing",
                "对比首屏渲染耗时",
                "assistant",
                "本地 1,000 条数据首屏由 420ms 降至 96ms，筛选和排序结果保持一致。",
                0.9,
            ),
            DemoStep("idle", "列表性能优化完成", "assistant", "优化已完成，相关组件测试全部通过。", 0.8),
        ),
    ),
    DemoScenario(
        task="为配置接口增加参数校验",
        request="配置更新接口会接受无效地址，请加严格校验和清晰的错误响应。",
        directory="~/workspace/orbit-service",
        agent="Codex",
        branch="feat/config-validation",
        steps=(
            DemoStep(
                "thinking",
                "确认配置模型和接口约束",
                "assistant",
                "我会先检查请求模型、持久化逻辑和现有 API 测试。",
                1.0,
            ),
            DemoStep(
                "thinking",
                "读取配置接口测试",
                "tool",
                "Read src/api/config.py, src/models/config.py, tests/test_config_api.py",
                0.9,
            ),
            DemoStep(
                "coding",
                "实现 URL 与端口校验",
                "assistant",
                "在请求模型层校验协议、主机和端口，这样所有入口会共享同一规则。",
                1.4,
            ),
            DemoStep("coding", "补充无效输入用例", "tool", "Edit src/models/config.py, tests/test_config_api.py", 1.0),
            DemoStep(
                "testing", "运行配置接口测试", "tool", "uv run pytest tests/test_config_api.py -q · 21 passed", 1.3
            ),
            DemoStep(
                "testing", "执行静态检查", "tool", "uv run ruff check src/models/config.py · All checks passed", 0.7
            ),
            DemoStep(
                "idle", "参数校验已完成", "assistant", "已增加统一参数校验，无效地址现在返回具体的 422 错误。", 0.8
            ),
        ),
    ),
    DemoScenario(
        task="修复 BLE 断线重连后状态丢失",
        request="设备重连后屏幕偶尔是空的，请排查状态同步时序并修复。",
        directory="~/workspace/openwaifud",
        agent="OpenCode",
        branch="fix/ble-resync",
        steps=(
            DemoStep(
                "thinking",
                "追踪 BLE 重连与快照同步",
                "assistant",
                "我先确认连接回调、写队列和全量快照之间的时序。",
                1.1,
            ),
            DemoStep(
                "thinking",
                "检查重连日志与调用链",
                "tool",
                "Search `resync_ble` → connect → start_notify → snapshot",
                0.9,
            ),
            DemoStep(
                "coding",
                "调整重连后的同步时机",
                "assistant",
                "快照在写特征尚未就绪时入队，准备将同步移到通知订阅完成之后。",
                1.5,
            ),
            DemoStep(
                "coding", "增加重连场景测试", "tool", "Edit src/openwaifud/ble/client.py, tests/test_ble_client.py", 1.2
            ),
            DemoStep(
                "testing", "运行 BLE 客户端测试", "tool", "uv run pytest tests/test_ble_client.py -q · 16 passed", 1.4
            ),
            DemoStep(
                "thinking",
                "复核队列与异常路径",
                "assistant",
                "重连快照会在连接完全就绪后发送，写入失败仍保留原有重试行为。",
                0.8,
            ),
            DemoStep("idle", "重连状态同步已修复", "assistant", "已修复重连时序，并覆盖断线后恢复快照的测试。", 0.8),
        ),
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765", help="OpenWaifuD base URL (default: %(default)s)")
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Base seconds between updates; each step varies naturally (default: %(default)s)",
    )
    parser.add_argument(
        "--repeat", type=int, default=0, help="Number of scenarios; 0 runs forever (default: %(default)s)"
    )
    parser.add_argument(
        "--sessions",
        type=int,
        default=1,
        help="并发模拟的会话数，用于触发固件情绪状态机档位 (default: %(default)s)",
    )
    parser.add_argument("--task", default=None, help="Override the task text shown in every step")
    parser.add_argument(
        "--status",
        choices=("idle", "thinking", "coding", "testing", "error"),
        help="Send one status update and exit",
    )
    parser.add_argument(
        "--error-message", default="请求上游接口超时，重试 3 次后仍未恢复", help="Error text used with --status error"
    )
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducible timing and scenario order")
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


def send_context(base_url: str, session_id: str, task: str, step: int, scenario: DemoScenario) -> None:
    post_json(
        base_url,
        "/api/v1/context",
        {
            "plugin_type": scenario.agent.lower().replace(" ", "-"),
            "session_id": session_id,
            "current_task": task,
            "metadata": {"demo": True, "step": step},
        },
    )


def send_detail(
    base_url: str,
    session_id: str,
    step: int,
    status: str,
    scenario: DemoScenario,
    chat_context: list[dict[str, str]],
    error_message: str | None = None,
) -> None:
    post_json(
        base_url,
        "/api/v1/session/detail",
        {
            "session_id": session_id,
            "metadata": {
                "source": scenario.agent,
                "directory": scenario.directory,
                "branch": scenario.branch,
                "model": "gpt-5.2-codex",
                "step": f"{step}/{len(scenario.steps)}",
                "status": status,
            },
            "chat_context": chat_context[-12:],
            "error_message": error_message,
        },
    )


def send_status(base_url: str, status: str, session_id: str, error_message: str | None = None) -> None:
    post_json(base_url, "/api/v1/status", {"status": status, "error_message": error_message, "session_id": session_id})


def send_event(base_url: str, event: str, session_id: str, message: str) -> None:
    post_json(base_url, "/api/v1/event", {"event": event, "session_id": session_id, "message": message})


def publish_step(
    base_url: str,
    session_id: str,
    task: str,
    step_number: int,
    scenario: DemoScenario,
    demo_step: DemoStep,
    chat_context: list[dict[str, str]],
) -> None:
    send_context(base_url, session_id, task, step_number, scenario)
    send_status(base_url, demo_step.status, session_id)
    send_detail(base_url, session_id, step_number, demo_step.status, scenario, chat_context)
    if demo_step.status == "idle":
        send_event(base_url, "done", session_id, scenario.task)


async def run_session(base_url: str, index: int, args: argparse.Namespace, start_delay: float) -> None:
    """Run independent scenarios with accumulated context and varied pacing."""
    await asyncio.sleep(start_delay)
    rng = random.Random(None if args.seed is None else args.seed + index)
    completed = 0
    scenario_offset = index % len(SCENARIOS)

    while args.repeat == 0 or completed < args.repeat:
        scenario = SCENARIOS[(scenario_offset + completed) % len(SCENARIOS)]
        session_id = f"mock-{uuid4().hex[:8]}"
        chat_context = [{"role": "user", "content": args.task or scenario.request}]

        for step_number, demo_step in enumerate(scenario.steps, start=1):
            chat_context.append({"role": demo_step.role, "content": demo_step.message})
            task = args.task or demo_step.activity
            await asyncio.to_thread(
                publish_step,
                base_url,
                session_id,
                task,
                step_number,
                scenario,
                demo_step,
                chat_context,
            )
            print(f"[{session_id}] [{demo_step.status:8}] {task}")
            jitter = rng.uniform(0.82, 1.22)
            await asyncio.sleep(args.interval * demo_step.duration * jitter)

        completed += 1
        if args.repeat == 0 or completed < args.repeat:
            await asyncio.sleep(args.interval * rng.uniform(0.8, 1.5))


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
            scenario = SCENARIOS[0]
            session_id = f"mock-{uuid4().hex[:8]}"
            task = args.task or ("处理接口请求失败" if args.status == "error" else "检查当前工作区")
            message = args.error_message if args.status == "error" else f"Agent 当前状态：{args.status}"
            chat_context = [
                {"role": "user", "content": task},
                {"role": "assistant", "content": message},
            ]
            send_context(args.url, session_id, task, 1, scenario)
            send_status(args.url, args.status, session_id, args.error_message if args.status == "error" else None)
            send_detail(
                args.url,
                session_id,
                1,
                args.status,
                scenario,
                chat_context,
                args.error_message if args.status == "error" else None,
            )
            print(f"已发送状态: {args.status} ({session_id})")
            return 0

        print(f"模拟 agent 已启动: sessions={args.sessions}, url={args.url}, seed={args.seed}")
        print("按 Ctrl-C 停止")
        tasks = [
            asyncio.create_task(run_session(args.url, index, args, index * args.interval * 0.7))
            for index in range(args.sessions)
        ]
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
