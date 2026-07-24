# OpenWaifuD

运行于电脑端的 Python 异步守护进程，将 AI Agent 工作状态通过 BLE 实时同步至涂鸦 T5AI Board。

让 OpenCode / ClaudeCode / Codex 等 Agent 的工作状态（思考中、编码中、测试中……）可视化到桌面硬件设备，随时感知 AI 的工作进度。

## 技术栈

| 类别 | 技术 |
|------|------|
| 语言 & 运行时 | Python 3.11+ / asyncio |
| 包管理 | UV |
| HTTP 服务 | aiohttp |
| BLE 通信 | bleak |
| 日志 | loguru |
| 数据模型 | pydantic v2 |
| 代码规范 | Ruff |

## 快速开始

```bash
# 安装依赖
uv sync

# 运行（仅 HTTP API）
uv run openwaifud

# 运行（带 BLE 设备）
uv run openwaifud --ble-address AA:BB:CC:DD:EE:FF

# 自定义端口和日志
uv run openwaifud --port 9000 --log-level DEBUG

# 查看帮助
uv run openwaifud --help
```

支持环境变量配置：`OPENWAIFUD_HTTP_HOST`、`OPENWAIFUD_HTTP_PORT`、`OPENWAIFUD_BLE_ADDRESS`、`OPENWAIFUD_LOG_LEVEL`。CLI 参数优先级高于环境变量。

## HTTP API 文档

默认监听 `127.0.0.1:8765`。

### POST /api/v1/status

上报 Agent 工作状态。

**请求：**

```json
{
  "status": "coding",
  "error_message": null
}
```

**响应 200：**

```json
{
  "success": true,
  "status": "coding"
}
```

`status` 可选值：`idle`、`thinking`、`coding`、`testing`、`error`。

### POST /api/v1/context

上报当前会话上下文。

**请求：**

```json
{
  "plugin_type": "opencode",
  "session_id": "sess_abc123",
  "current_task": "实现用户登录功能",
  "metadata": {}
}
```

**响应 200：**

```json
{
  "success": true,
  "session_id": "sess_abc123"
}
```

### GET /api/v1/state

获取守护进程当前状态快照。

**响应 200：**

```json
{
  "agent_status": "coding",
  "context": {
    "plugin_type": "opencode",
    "session_id": "sess_abc123",
    "current_task": "实现用户登录功能",
    "metadata": {},
    "timestamp": "2026-07-24T10:00:00Z"
  },
  "ble_connected": true,
  "uptime_seconds": 3600.15,
  "timestamp": "2026-07-24T10:00:00Z"
}
```

### GET /api/v1/health

健康检查。

**响应 200：**

```json
{
  "status": "ok",
  "ble_connected": true,
  "uptime_seconds": 3600.15
}
```

## Realtime 文件监控

OpenWaifuD 内置 Realtime 模块，自动监控本地 AI Agent 工具的会话文件，实时提取工作状态并同步至 BLE 设备。无需手动调用 HTTP API，守护进程会自动检测 Agent 活动。

### 支持的工具

| 工具 | 存储路径 | 格式 |
|------|----------|------|
| Claude Code | `~/.claude/projects/<project>/<session>.jsonl` | JSONL |
| Codex CLI | `~/.codex/sessions/YYYY/MM/DD/<session>.jsonl` | JSONL |
| OpenCode | `~/.local/share/opencode/history/` | JSONL（best-effort） |

### 状态推断规则

| 检测条件 | 推断状态 |
|----------|----------|
| 消息含 write_file/edit_file 等工具调用 | CODING |
| 消息含 run_command/bash + test 关键词 | TESTING |
| Assistant 纯文本消息（无工具调用） | THINKING |
| 消息含 error/failed/exception | ERROR |
| 超过 60 秒无新消息 | IDLE |

### 配置

| 参数 | 环境变量 | CLI 参数 | 默认值 | 说明 |
|------|----------|----------|--------|------|
| 启用监控 | `OPENWAIFUD_REALTIME_ENABLED` | `--no-realtime` | `true` | 设为 false 或使用 --no-realtime 禁用 |
| 防抖延迟 | `OPENWAIFUD_REALTIME_DEBOUNCE_MS` | `--realtime-debounce` | `300` | 文件变化后等待时间（毫秒） |
| 空闲超时 | — | — | `60` | 无新消息后标记为 IDLE 的秒数 |

### 使用示例

```bash
# 默认启用 realtime（自动检测已安装的 Agent 工具）
uv run openwaifud --ble-address AA:BB:CC:DD:EE:FF

# 禁用 realtime，仅使用 HTTP API 模式
uv run openwaifud --no-realtime

# 自定义防抖延迟
uv run openwaifud --realtime-debounce 500
```

### 工作原理

1. 启动时扫描各工具目录，找到当前活跃的会话文件
2. 使用 `watchdog` 监听文件系统事件（macOS 使用 FSEvents，Linux 使用 inotify）
3. 文件变化后经过防抖（默认 300ms）避免读取写入中的文件
4. 增量解析 JSONL 文件（仅读取新追加的行）
5. 根据消息内容推断 Agent 状态，注入 StateManager 队列
6. 通过 BLE 同步至 T5AI Board

> **注意**：Realtime 模式与 HTTP API 模式可同时工作。HTTP API 的显式推送优先级高于 Realtime 推断。

## BLE 协议说明

### GATT Service / Characteristic

| 项目 | UUID |
|------|------|
| Service | `0000ff01-0000-1000-8000-00805f9b34fb` |
| 状态特征 (Write) | `0000ff02-0000-1000-8000-00805f9b34fb` |
| 上下文特征 (Write) | `0000ff03-0000-1000-8000-00805f9b34fb` |

### 状态包格式（固定 4 字节）

```
[1B: protocol_version] [1B: status_code] [1B: error_code] [1B: reserved=0x00]
```

| 字节 | 含义 | 说明 |
|------|------|------|
| 0 | 协议版本 | 当前为 `0x01` |
| 1 | 状态码 | 见下方映射表 |
| 2 | 错误码 | 正常为 `0x00` |
| 3 | 保留 | 固定 `0x00` |

### 上下文包格式（TLV 变长，最大 240 字节 payload）

```
[1B: protocol_version] [1B: msg_type=0x02] [2B: payload_length (big-endian)] [payload: UTF-8 JSON]
```

Payload 为 JSON 字符串，包含 `plugin`、`session_id`、`task` 字段。超过 240 字节时自动截断 `task` 内容。

### 状态码映射表

| AgentStatus | 字节码 |
|-------------|--------|
| `idle` | `0x00` |
| `thinking` | `0x01` |
| `coding` | `0x02` |
| `testing` | `0x03` |
| `error` | `0x04` |

## 项目结构

```
src/openwaifud/
├── __init__.py          # 包初始化
├── __main__.py          # CLI 入口，参数解析与信号处理
├── config.py            # 配置管理（环境变量 + CLI 参数）
├── daemon.py            # 守护进程主逻辑，编排 HTTP 和 BLE
├── models.py            # Pydantic 数据模型（状态、上下文、快照）
├── api/
│   ├── __init__.py
│   ├── handlers.py      # HTTP 路由处理器（4 个 API 端点）
│   └── server.py        # aiohttp 服务器启动与生命周期
├── ble/
│   ├── __init__.py
│   ├── client.py        # BLE 连接管理（重连、写入）
│   └── protocol.py      # GATT 协议编解码（状态包 & 上下文包）
└── state/
    ├── __init__.py
    └── manager.py       # 异步状态管理器（队列 + BLE 回调消费）
```

## 开发指南

```bash
# 安装开发依赖
uv sync --all-extras

# 运行测试
uv run pytest tests/ -v

# 代码检查
uv run ruff check src/

# 代码格式化
uv run ruff format src/
```

## 设计特点

- **优雅降级** — 未配置 BLE 地址或设备离线时，HTTP API 正常工作不受影响
- **异步队列解耦** — HTTP 处理器通过异步队列投递消息，BLE 写入由独立消费协程处理，互不阻塞
- **指数退避重连** — BLE 断线后自动尝试重连，避免频繁连接风暴
- **信号处理与优雅关闭** — 监听 SIGINT/SIGTERM，按序停止消费者、断开 BLE、关闭 HTTP 服务器
