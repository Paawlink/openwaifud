"""BLE 会话协议：将 Agent 会话状态编码为固件可解析的命令行，写入 Write 特征。

设计目标（与 OpenWaifu 固件一致）：固件屏幕不再是“历史消息滚动列表”，而是一块
**实时会话看板**——每个活跃会话固定占用一行/一张卡片，随会话状态机变化更新，
会话结束后整行移除。为此本模块把守护进程侧的会话变化编码为紧凑的单行命令：

命令格式（UTF-8，单行，不含换行，长度 <= :data:`MAX_PAYLOAD` 字节）：

======  =====================================  ================================
命令    格式                                    含义
======  =====================================  ================================
清空    ``C``                                  清空固件端所有会话（连接/重连时下发）
移除    ``X|<sid>``                             移除某个会话行（完成 / 中止）
更新    ``S|<sid>|<st>|<elapsed>|<plugin>|<task>``  新增或更新一个会话
开始    ``B``                                  快照同步开始（固件把现有会话标记为“未见”）
结束    ``E``                                  快照同步结束（固件移除本轮未再出现的会话）
======  =====================================  ================================

其中 ``<st>`` 为单字符状态码（见 :data:`STATUS_CHARS`），``<elapsed>`` 为该会话
已运行的整数秒数（固件收到后在本地按秒继续跳动）。字段以 ``|`` 分隔，因此 ``sid``
与 ``task`` 中的 ``|``、换行等字符会在编码前被替换为空格。

``B``/``E`` 用于**周期性快照对账**：守护进程按 ``B`` -> 逐个 ``S`` 全量会话 -> ``E``
的顺序下发当前 :func:`~openwaifud.state.manager.StateManager.get_current_state` 的
活跃会话，固件据此把屏幕列表**收敛到与 api/v1/state 完全一致**，无需依赖增量
``X`` 的可靠送达即可删除已消失的旧会话（且不会像 ``C`` 那样清屏闪烁）。
"""

from __future__ import annotations

from openwaifud.models import AgentStatus

# ---------------------------------------------------------------------------
# 设备与 GATT 标识（须与固件 apps/openwaifu 保持一致）
# ---------------------------------------------------------------------------

DEVICE_NAME = "OpenWaifu"
SERVICE_UUID = "0000fd50-0000-1000-0880-00805f9b34fb"
WRITE_CHAR_UUID = "00000001-0000-1001-8001-00805f9b07d0"

# 单条命令 UTF-8 编码后的最大字节数（与固件 OPENWAIFU_BLE_MAX_MSG_LEN 对齐）
MAX_PAYLOAD: int = 240

# 字段分隔符与命令前缀
FIELD_SEP = "|"
CMD_UPSERT = "S"
CMD_REMOVE = "X"
CMD_CLEAR = "C"
CMD_SYNC_BEGIN = "B"
CMD_SYNC_END = "E"

# AgentStatus -> 单字符状态码（固件据此显示中文标签与配色）
STATUS_CHARS: dict[AgentStatus, str] = {
    AgentStatus.THINKING: "T",
    AgentStatus.CODING: "C",
    AgentStatus.TESTING: "V",
    AgentStatus.ERROR: "E",
    AgentStatus.IDLE: "I",
}


# ---------------------------------------------------------------------------
# Exceptions（保留以兼容既有调用方）
# ---------------------------------------------------------------------------


class BLEProtocolError(Exception):
    """BLE 协议编码错误。"""


class BLEConnectionError(Exception):
    """BLE 连接失败。"""


class BLEWriteError(Exception):
    """BLE 特征写入失败。"""


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _sanitize(text: str) -> str:
    """去除会破坏命令行解析的字符（分隔符、换行、回车），并压缩两端空白。"""
    if not text:
        return ""
    cleaned = text.replace(FIELD_SEP, " ").replace("\r", " ").replace("\n", " ")
    return cleaned.strip()


def status_char(status: AgentStatus) -> str:
    """返回状态对应的单字符命令码，未知状态回退为空闲 ``I``。"""
    return STATUS_CHARS.get(status, "I")


def _encode_line(text: str) -> bytes:
    """将整行命令编码为 UTF-8，超长时按字符边界安全截断。"""
    payload = text.encode("utf-8")
    if len(payload) <= MAX_PAYLOAD:
        return payload
    return payload[:MAX_PAYLOAD].decode("utf-8", errors="ignore").encode("utf-8")


# ---------------------------------------------------------------------------
# 命令编码
# ---------------------------------------------------------------------------


def encode_clear() -> bytes:
    """编码“清空全部会话”命令。"""
    return _encode_line(CMD_CLEAR)


def encode_sync_begin() -> bytes:
    """编码“快照同步开始”命令：固件把现有会话标记为“未见”。"""
    return _encode_line(CMD_SYNC_BEGIN)


def encode_sync_end() -> bytes:
    """编码“快照同步结束”命令：固件移除本轮未再出现（仍为“未见”）的会话。"""
    return _encode_line(CMD_SYNC_END)


def encode_session_remove(session_id: str) -> bytes:
    """编码“移除某个会话”命令。"""
    sid = _sanitize(session_id)
    return _encode_line(f"{CMD_REMOVE}{FIELD_SEP}{sid}")


def encode_session_upsert(
    session_id: str,
    status: AgentStatus,
    elapsed_seconds: int,
    plugin_type: str,
    task: str,
) -> bytes:
    """编码“新增/更新会话”命令。

    先构造固定字段前缀，再用剩余字节预算容纳（可能较长的）任务描述，确保整行
    UTF-8 编码后不超过 :data:`MAX_PAYLOAD`。
    """
    sid = _sanitize(session_id)
    plugin = _sanitize(plugin_type) or "agent"
    st = status_char(status)
    elapsed = max(0, int(elapsed_seconds))

    prefix = f"{CMD_UPSERT}{FIELD_SEP}{sid}{FIELD_SEP}{st}{FIELD_SEP}{elapsed}{FIELD_SEP}{plugin}{FIELD_SEP}"
    budget = MAX_PAYLOAD - len(prefix.encode("utf-8"))
    if budget <= 0:
        # 极端情况下 sid/plugin 已占满预算，直接截断整行
        return _encode_line(prefix)

    task_bytes = _sanitize(task).encode("utf-8")
    if len(task_bytes) > budget:
        task_bytes = task_bytes[:budget].decode("utf-8", errors="ignore").encode("utf-8")
    return prefix.encode("utf-8") + task_bytes
