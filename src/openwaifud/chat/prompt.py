"""「涂鸦」语音助手的 system prompt 构建。

对话请求来自开发板上的语音交互：用户对着桌宠说话，固件经 daemon 转发到
OpenAI 兼容 API，回复再经 TTS 播报。因此 prompt 强调口语化、简短、不使用
Markdown 等任何排版符号。

每次对话时把 daemon 的实时状态快照（:class:`~openwaifud.models.DaemonState`）
与存活的 OpenCode 实例列表渲染成自然语言注入 prompt，让涂鸦既能回答
"你在忙什么""编码任务跑多久了"这类问题，也能在用户想新建会话时
引导其确认目标实例并调用创建会话技能。
"""

from __future__ import annotations

from pathlib import PurePath

from openwaifud.models import AgentStatus, DaemonState, PluginInstanceInfo, SessionInfo

# 状态枚举 -> 口语化中文描述
_STATUS_LABELS: dict[AgentStatus, str] = {
    AgentStatus.IDLE: "空闲",
    AgentStatus.THINKING: "思考中",
    AgentStatus.CODING: "编写代码中",
    AgentStatus.TESTING: "运行测试中",
    AgentStatus.ERROR: "出错了",
}

_PERSONA = """\
你是「涂鸦」，一个住在桌面开发板里的桌宠语音助手。你的身后运行着 OpenWaifuD \
守护进程，它实时汇聚来自 OpenCode、Claude Code、Codex 等编程助手（Agent）的\
工作状态，并同步到你所在的屏幕上。你既是主人的贴心陪伴，也是这些编程 Agent \
的"状态播报员"。

对话规则：
1. 用户通过语音与你交谈，你的回复会被语音合成朗读。回复必须是口语化的简体中文短句，\
通常不超过两三句话，禁止使用 Markdown、列表符号、表情符号或代码块。
2. 用户询问工作状态（比如"现在在忙什么""任务跑完了吗"）时，依据下方的实时状态\
概览如实回答；概览里没有的信息不要编造。
3. 会话出错时先安抚再简述错误；一切空闲时可以轻松地陪主人闲聊。
4. 保持亲切、活泼、简洁，像一只懂技术的小桌宠。

新建会话技能（create_opencode_session）：
1. 当用户表达出想新开一个聊天/会话/任务、想让编程助手帮忙干活的意愿时，\
使用该技能在 OpenCode 中创建新会话。
2. 目标实例必须由用户确认，不要自作主张选某一个：下方列出了存活的 OpenCode 实例，\
有多个时先用项目名称口头列举并询问用户要用哪个，得到明确答复后再调用技能；\
只有一个实例时可直接使用，但要在回复里说明在哪个项目创建。
3. 调用时 instance_id 必须取自实例列表，不得编造；若用户顺带说了想让助手做的事，\
把它作为 prompt 参数一并传入。
4. 实例列表为空时不要调用技能，告诉用户先在电脑上打开 OpenCode。"""


def _format_elapsed(seconds: float) -> str:
    """把秒数转成口语化时长（如"45秒""3分钟""1小时12分"）。"""
    total = int(seconds)
    if total < 60:
        return f"{total}秒"
    minutes, _ = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}分钟"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}小时{minutes}分"


def _format_session(index: int, session: SessionInfo) -> str:
    status = _STATUS_LABELS.get(session.status, session.status.value)
    parts = [
        f"{index}. 来自 {session.plugin_type} 的会话，状态：{status}",
        f"已运行 {_format_elapsed(session.elapsed_seconds)}",
    ]
    if session.current_task:
        parts.append(f"当前任务：{session.current_task}")
    if session.error_message:
        parts.append(f"错误信息：{session.error_message}")
    if session.is_done:
        parts.append("（已完成，即将从屏幕移除）")
    return "，".join(parts)


def _render_state(state: DaemonState) -> str:
    """把 DaemonState 渲染成注入 prompt 的自然语言状态概览。"""
    overall = _STATUS_LABELS.get(state.agent_status, state.agent_status.value)
    lines = [
        f"总体状态：{overall}",
        f"与开发板的蓝牙连接：{'已连接' if state.ble_connected else '未连接'}",
        f"守护进程已运行：{_format_elapsed(state.uptime_seconds)}",
    ]
    if state.sessions:
        lines.append(f"活跃会话（共 {len(state.sessions)} 个）：")
        lines.extend(_format_session(i, s) for i, s in enumerate(state.sessions, start=1))
    else:
        lines.append("活跃会话：暂无，所有编程 Agent 都在休息。")
    return "\n".join(lines)


def _render_instances(instances: list[PluginInstanceInfo]) -> str:
    """把存活实例列表渲染成注入 prompt 的自然语言清单。"""
    if not instances:
        return "当前没有存活的 OpenCode 实例，无法创建新会话。"
    lines = [f"当前存活的 OpenCode 实例（共 {len(instances)} 个）："]
    for i, inst in enumerate(instances, start=1):
        project = PurePath(inst.directory).name if inst.directory else "未知项目"
        lines.append(
            f"{i}. 项目「{project}」，目录 {inst.directory or '未上报'}，"
            f"instance_id：{inst.instance_id}"
        )
    return "\n".join(lines)


def build_system_prompt(
    state: DaemonState | None = None,
    instances: list[PluginInstanceInfo] | None = None,
) -> str:
    """构建注入实时状态与实例列表的 system prompt。

    :param state: daemon 当前状态快照；为 None 时（如状态源不可用）
        仅返回人设部分，并提示涂鸦状态暂不可知。
    :param instances: 存活的 OpenCode 实例列表，供新建会话技能确认目标；
        为 None 时视为暂无实例。
    """
    overview = (
        "暂时读取不到工作状态，被问到时请如实说明。" if state is None else _render_state(state)
    )
    return (
        _PERSONA
        + "\n\n【实时状态概览】\n"
        + overview
        + "\n\n【OpenCode 实例列表】\n"
        + _render_instances(instances or [])
    )
