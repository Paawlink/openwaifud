"""「涂鸦」语音助手的 system prompt 构建。

对话请求来自开发板上的语音交互：用户对着桌宠说话，固件经 daemon 转发到
OpenAI 兼容 API，回复再经 TTS 播报。因此 prompt 强调口语化、简短、不使用
Markdown 等任何排版符号。

每次对话时把 daemon 的实时状态快照（:class:`~openwaifud.models.DaemonState`）
渲染成自然语言注入 prompt，让涂鸦能回答"你在忙什么""编码任务跑多久了"
这类问题。若同时提供会话详情（:class:`~openwaifud.models.SessionDetail`），
还会把每个会话的元数据（工作目录等）与最近的对话摘要全量注入，使涂鸦能回答
"在哪个项目干活""刚才聊了什么"这类细节问题。
"""

from __future__ import annotations

from openwaifud.models import AgentStatus, DaemonState, SessionDetail, SessionInfo

# 状态枚举 -> 口语化中文描述
_STATUS_LABELS: dict[AgentStatus, str] = {
    AgentStatus.IDLE: "空闲",
    AgentStatus.THINKING: "思考中",
    AgentStatus.CODING: "编写代码中",
    AgentStatus.TESTING: "运行测试中",
    AgentStatus.ERROR: "出错了",
}

_PERSONA = """\
你是「小火」，一个住在桌面开发板里的桌宠语音助手。你的身后运行着 OpenWaifuD \
守护进程，它实时汇聚来自 OpenCode、Claude Code、Codex 等编程助手（Agent）的\
工作状态，并同步到你所在的屏幕上。你既是创造者的贴心陪伴，也是这些编程 Agent \
的"状态播报员"，如果你发现当前状态和历史状态不同，不是你的错，因为状态一直在更新，以最新状态为准。

对话规则：
1. 用户（创造者）通过语音与你交谈，你的回复会被语音合成朗读。回复必须使用用户的语言，\
禁止使用 Markdown、列表符号、表情符号或代码块，使用简洁、一整段对话回答问题。
2. 用户询问工作状态或细节（比如"现在在忙什么""任务跑完了吗""在哪个项目里干活"\
"刚才 Agent 聊了什么"）时，依据下方的实时状态概览如实回答，包括会话的工作目录、\
元数据和最近对话摘要；概览里没有的信息不要编造。
3. 会话出错时先安抚再简述错误；一切空闲时可以轻松地陪创造者闲聊。
4. 回复最多 64 个字符，保持亲切、活泼、简洁，像一只懂技术的小桌宠。
5. 不要交代一些对用户语音交流没有意义的文本，比如具体代码内容、日志、异常堆栈等。"""


# 常见元数据键 -> 口语化中文标签（其余键原样展示）
_METADATA_LABELS: dict[str, str] = {
    "directory": "工作目录",
    "source": "来源",
    "agent": "Agent",
}

# 消息角色 -> 口语化中文标签
_ROLE_LABELS: dict[str, str] = {
    "user": "创造者",
    "assistant": "Agent",
    "tool": "工具",
    "system": "系统",
}


def _format_session(index: int, session: SessionInfo) -> str:
    status = _STATUS_LABELS.get(session.status, session.status.value)
    parts = [
        f"{index}. 来自 {session.plugin_type} 的会话，状态：{status}",
    ]
    if session.current_task:
        parts.append(f"当前任务：{session.current_task}")
    if session.error_message:
        parts.append(f"错误信息：{session.error_message}")
    if session.is_done:
        parts.append("（已完成，即将从屏幕移除）")
    return "，".join(parts)


def _format_detail_lines(detail: SessionDetail) -> list[str]:
    """把会话详情（元数据 + 聊天上下文）渲染成缩进的补充行。"""
    lines: list[str] = []
    for key, value in detail.metadata.items():
        label = _METADATA_LABELS.get(key, key)
        lines.append(f"   {label}：{value}")
    if detail.chat_context:
        lines.append("   该会话最近的对话摘要（时间从早到晚）：")
        for msg in detail.chat_context:
            role = _ROLE_LABELS.get(msg.role, msg.role)
            lines.append(f"   [{role}] {msg.content}")
    return lines


def _render_state(state: DaemonState, details: list[SessionDetail] | None = None) -> str:
    """把 DaemonState（及可选的会话详情）渲染成注入 prompt 的状态概览。"""
    overall = _STATUS_LABELS.get(state.agent_status, state.agent_status.value)
    lines = [
        f"总体状态：{overall}",
    ]
    detail_map = {d.session_id: d for d in (details or [])}
    if state.sessions:
        lines.append(f"活跃会话（共 {len(state.sessions)} 个）：")
        for i, s in enumerate(state.sessions, start=1):
            lines.append(_format_session(i, s))
            detail = detail_map.get(s.session_id)
            if detail is not None:
                lines.extend(_format_detail_lines(detail))
    else:
        lines.append("活跃会话：暂无，所有编程 Agent 都在休息。")
    return "\n".join(lines)


def build_system_prompt(
    state: DaemonState | None = None,
    details: list[SessionDetail] | None = None,
) -> str:
    """构建注入实时状态的 system prompt。

    :param state: daemon 当前状态快照；为 None 时（如状态源不可用）
        仅返回人设部分，并提示涂鸦状态暂不可知。
    :param details: 各会话的完整详情（元数据、聊天上下文），按 session_id
        与 ``state.sessions`` 匹配后注入；缺失的会话仅展示概览行。
    """
    overview = "暂时读取不到工作状态，被问到时请如实说明。" if state is None else _render_state(state, details)
    return _PERSONA + "\n\n【实时状态概览】\n" + overview
