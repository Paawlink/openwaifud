"""即时对话服务（直连 OpenAI 兼容 API）。"""

from openwaifud.chat.service import ChatNotConfiguredError, ChatService, ChatUpstreamError

__all__ = ["ChatNotConfiguredError", "ChatService", "ChatUpstreamError"]
