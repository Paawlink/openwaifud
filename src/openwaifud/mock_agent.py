"""In-process fake Agent sessions controlled by the web console."""

from __future__ import annotations

import asyncio
import random
from contextlib import suppress
from dataclasses import dataclass
from uuid import uuid4

from loguru import logger

from openwaifud.models import (
    AgentStatus,
    ChatMessage,
    ConversationContext,
    DetailUpdate,
    GlobalEventKind,
    MockAgentConfig,
    StatusUpdate,
)
from openwaifud.state.manager import StateManager


@dataclass(frozen=True)
class DemoStep:
    status: AgentStatus
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


def _step(status: str, activity: str, role: str, message: str, duration: float = 1.0) -> DemoStep:
    return DemoStep(AgentStatus(status), activity, role, message, duration)


SCENARIOS = (
    DemoScenario(
        "修复登录态过期后重复跳转",
        "登录态失效时页面会连续跳转两次，请定位原因、修复并补上回归测试。",
        "~/workspace/nebula-console",
        "OpenCode",
        "fix/auth-redirect-loop",
        (
            _step(
                "thinking",
                "梳理认证回调与路由守卫",
                "assistant",
                "我先检查 token 刷新、路由守卫和 401 响应的调用关系。",
                1.1,
            ),
            _step("thinking", "检索重复导航的触发路径", "tool", "Search `router.replace` · 7 matches in 4 files", 0.8),
            _step(
                "coding",
                "合并并发的登录失效处理",
                "assistant",
                "发现两个入口会同时跳转，准备让失效处理只执行一次。",
                1.4,
            ),
            _step("coding", "补充认证回归测试", "tool", "Edit src/auth/session.ts, tests/auth/session.test.ts", 1.1),
            _step("testing", "运行认证模块测试", "tool", "pnpm vitest run tests/auth/session.test.ts · 12 passed", 1.3),
            _step("idle", "登录跳转问题已修复", "assistant", "已修复重复跳转并补充并发 401 的回归测试。", 0.8),
        ),
    ),
    DemoScenario(
        "优化大列表首屏渲染性能",
        "订单列表首次打开很卡，请分析瓶颈并优化，不能改变筛选和排序行为。",
        "~/workspace/atlas-admin",
        "ClaudeCode",
        "perf/order-list",
        (
            _step(
                "thinking",
                "分析订单列表渲染链路",
                "assistant",
                "我先看列表数据流、行组件和筛选条件，确认是否存在重复计算。",
            ),
            _step(
                "thinking", "定位高频重渲染组件", "tool", "Read OrderTable.tsx → OrderRow.tsx → useOrderFilters.ts", 0.9
            ),
            _step(
                "coding",
                "下沉稳定的行数据映射",
                "assistant",
                "瓶颈来自每行重复格式化完整订单对象，将映射移到数据入口。",
                1.5,
            ),
            _step("coding", "减少不可见行的渲染工作", "tool", "Edit src/orders/OrderTable.tsx · +34 -21", 1.2),
            _step("testing", "验证筛选排序行为", "tool", "pnpm test OrderTable · 18 passed", 1.2),
            _step("idle", "列表性能优化完成", "assistant", "首屏由 420ms 降至 96ms，筛选和排序结果保持一致。", 0.8),
        ),
    ),
    DemoScenario(
        "为配置接口增加参数校验",
        "配置更新接口会接受无效地址，请加严格校验和清晰的错误响应。",
        "~/workspace/orbit-service",
        "Codex",
        "feat/config-validation",
        (
            _step("thinking", "确认配置模型和接口约束", "assistant", "我会先检查请求模型、持久化逻辑和现有 API 测试。"),
            _step(
                "thinking",
                "读取配置接口测试",
                "tool",
                "Read src/api/config.py, src/models/config.py, tests/test_config_api.py",
                0.9,
            ),
            _step(
                "coding",
                "实现 URL 与端口校验",
                "assistant",
                "在请求模型层校验协议、主机和端口，让所有入口共享规则。",
                1.4,
            ),
            _step("coding", "补充无效输入用例", "tool", "Edit src/models/config.py, tests/test_config_api.py"),
            _step("testing", "运行配置接口测试", "tool", "uv run pytest tests/test_config_api.py -q · 21 passed", 1.3),
            _step("idle", "参数校验已完成", "assistant", "已增加统一参数校验，无效地址会返回具体的 422 错误。", 0.8),
        ),
    ),
    DemoScenario(
        "修复 BLE 断线重连后状态丢失",
        "设备重连后屏幕偶尔是空的，请排查状态同步时序并修复。",
        "~/workspace/openwaifud",
        "OpenCode",
        "fix/ble-resync",
        (
            _step(
                "thinking",
                "追踪 BLE 重连与快照同步",
                "assistant",
                "我先确认连接回调、写队列和全量快照之间的时序。",
                1.1,
            ),
            _step(
                "thinking",
                "检查重连日志与调用链",
                "tool",
                "Search `resync_ble` → connect → start_notify → snapshot",
                0.9,
            ),
            _step(
                "coding",
                "调整重连后的同步时机",
                "assistant",
                "快照在写特征尚未就绪时入队，将同步移到通知订阅完成之后。",
                1.5,
            ),
            _step(
                "coding", "增加重连场景测试", "tool", "Edit src/openwaifud/ble/client.py, tests/test_ble_client.py", 1.2
            ),
            _step(
                "testing", "运行 BLE 客户端测试", "tool", "uv run pytest tests/test_ble_client.py -q · 16 passed", 1.4
            ),
            _step("idle", "重连状态同步已修复", "assistant", "已修复重连时序，并覆盖断线后恢复快照的测试。", 0.8),
        ),
    ),
)


class MockAgentService:
    """Own a configurable in-process mock workload for the daemon lifetime."""

    def __init__(self, state_manager: StateManager) -> None:
        self._state_manager = state_manager
        self._config = MockAgentConfig()
        self._task: asyncio.Task[None] | None = None
        self._session_ids: set[str] = set()
        self._lock = asyncio.Lock()

    def get_config(self) -> dict[str, object]:
        return {**self._config.model_dump(), "running": self._task is not None and not self._task.done()}

    async def configure(self, config: MockAgentConfig) -> None:
        async with self._lock:
            await self._stop_locked()
            self._config = config
            if config.enabled:
                self._task = asyncio.create_task(self._run(config), name="mock-agent")
                logger.info(
                    f"Mock Agent enabled: sessions={config.sessions}, "
                    f"interval={config.interval}, repeat={config.repeat}"
                )

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()
            self._config = self._config.model_copy(update={"enabled": False})

    async def _stop_locked(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if self._session_ids:
            await self._state_manager.remove_sessions(self._session_ids)
            self._session_ids.clear()

    async def _run(self, config: MockAgentConfig) -> None:
        try:
            if config.status is not None:
                await self._run_status(config)
            else:
                await asyncio.gather(*(self._run_session(index, config) for index in range(config.sessions)))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Mock Agent stopped after an unexpected error")
        finally:
            if self._task is asyncio.current_task():
                self._task = None
                self._config = config.model_copy(update={"enabled": False})

    async def _run_status(self, config: MockAgentConfig) -> None:
        scenario = SCENARIOS[0]
        session_id = f"mock-{uuid4().hex[:8]}"
        self._session_ids.add(session_id)
        status = config.status or AgentStatus.THINKING
        task = config.task or ("处理接口请求失败" if status == AgentStatus.ERROR else "检查当前工作区")
        error_message = config.error_message if status == AgentStatus.ERROR else None
        message = error_message or f"Agent 当前状态：{status.value}"
        await self._state_manager.update_context(
            ConversationContext(
                plugin_type="opencode",
                session_id=session_id,
                current_task=task,
                metadata={"demo": True, "step": 1},
            )
        )
        await self._state_manager.update_status(
            StatusUpdate(status=status, session_id=session_id, error_message=error_message)
        )
        await self._state_manager.update_session_detail(
            DetailUpdate(
                session_id=session_id,
                metadata={"source": scenario.agent, "directory": scenario.directory, "status": status.value},
                chat_context=[
                    ChatMessage(role="user", content=task),
                    ChatMessage(role="assistant", content=message),
                ],
                error_message=error_message,
            )
        )

    async def _run_session(self, index: int, config: MockAgentConfig) -> None:
        await asyncio.sleep(index * config.interval * 0.7)
        rng = random.Random(None if config.seed is None else config.seed + index)
        completed = 0
        while config.repeat == 0 or completed < config.repeat:
            scenario = SCENARIOS[(index + completed) % len(SCENARIOS)]
            session_id = f"mock-{uuid4().hex[:8]}"
            self._session_ids.add(session_id)
            chat_context = [{"role": "user", "content": config.task or scenario.request}]
            try:
                for step_number, step in enumerate(scenario.steps, start=1):
                    chat_context.append({"role": step.role, "content": step.message})
                    await self._publish_step(
                        session_id, config.task or step.activity, step_number, scenario, step, chat_context
                    )
                    await asyncio.sleep(config.interval * step.duration * rng.uniform(0.82, 1.22))
            finally:
                active_ids = {session.session_id for session in self._state_manager.get_current_state().sessions}
                self._session_ids.intersection_update(active_ids)
            completed += 1
            if config.repeat == 0 or completed < config.repeat:
                await asyncio.sleep(config.interval * rng.uniform(0.8, 1.5))

    async def _publish_step(
        self,
        session_id: str,
        task: str,
        step_number: int,
        scenario: DemoScenario,
        step: DemoStep,
        chat_context: list[dict[str, str]],
    ) -> None:
        await self._state_manager.update_context(
            ConversationContext(
                plugin_type=scenario.agent.lower().replace(" ", "-"),
                session_id=session_id,
                current_task=task,
                metadata={"demo": True, "step": step_number},
            )
        )
        await self._state_manager.update_status(StatusUpdate(status=step.status, session_id=session_id))
        await self._state_manager.update_session_detail(
            DetailUpdate(
                session_id=session_id,
                metadata={
                    "source": scenario.agent,
                    "directory": scenario.directory,
                    "branch": scenario.branch,
                    "model": "gpt-5.2-codex",
                    "step": f"{step_number}/{len(scenario.steps)}",
                    "status": step.status.value,
                },
                chat_context=[
                    ChatMessage(role=message["role"], content=message["content"]) for message in chat_context[-12:]
                ],
            )
        )
        if step.status == AgentStatus.IDLE:
            await self._state_manager.emit_global_event(GlobalEventKind.DONE, scenario.task)
