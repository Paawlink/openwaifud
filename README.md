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

# 运行（带 BLE 设备，指定地址）
uv run openwaifud --ble-address AA:BB:CC:DD:EE:FF

# 运行（不指定地址时，自动按设备名 OpenWaifu 扫描连接）
uv run openwaifud

# 自定义端口和日志
uv run openwaifud --port 9000 --log-level DEBUG

# 查看帮助
uv run openwaifud --help
```

支持环境变量配置：`OPENWAIFUD_HTTP_HOST`、`OPENWAIFUD_HTTP_PORT`、`OPENWAIFUD_BLE_ADDRESS`、`OPENWAIFUD_BLE_DEVICE_NAME`、`OPENWAIFUD_LOG_LEVEL`。CLI 参数优先级高于环境变量。

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
  "sessions": [
    {
      "session_id": "sess_abc123",
      "plugin_type": "opencode",
      "status": "coding",
      "current_task": "实现用户登录功能",
      "error_message": null,
      "elapsed_seconds": 42.0,
      "is_done": false
    }
  ],
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
| OpenCode | `~/.local/share/opencode/opencode.db`（旧版为 `history/`） | SQLite（旧版 JSONL） |

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

### 模拟 Agent

可以使用 `tools/mock_agent.py` 模拟一个 Agent 会话，方便测试页面和 BLE 屏幕效果：

```bash
# 终端 1：启动守护进程
uv run openwaifud --no-realtime

# 终端 2：循环发送 thinking/coding/testing/idle 状态
python3 tools/mock_agent.py

# 每 1 秒循环一次，只运行 2 轮
python3 tools/mock_agent.py --interval 1 --repeat 2

# 并发模拟 4 个会话，触发「火力全开」档位
python3 tools/mock_agent.py --sessions 4

# 只测试错误状态
python3 tools/mock_agent.py --status error --task "加载用户资料"
```

默认 API 地址为 `http://127.0.0.1:8765`，可使用 `--url` 修改。

### 工作原理

1. 启动时扫描各工具目录，找到当前活跃的会话文件
2. 使用 `watchdog` 监听文件系统事件（macOS 使用 FSEvents，Linux 使用 inotify）
3. 文件变化后经过防抖（默认 300ms）避免读取写入中的文件
4. 增量解析 JSONL 文件（仅读取新追加的行）
5. 根据消息内容推断 Agent 状态，注入 StateManager 队列
6. 通过 BLE 同步至 T5AI Board

> **注意**：Realtime 模式与 HTTP API 模式可同时工作。HTTP API 的显式推送优先级高于 Realtime 推断。

## BLE 协议说明

固件 `apps/openwaifu` 作为 BLE 从机（Peripheral），以名称 `OpenWaifu` 广播；守护进程作为主机（Central）连接后，维护一份「活跃会话注册表」，并把增量变化编码为一行行 UTF-8 命令写入固件的 Write 特征。固件按会话 ID 维护看板：**每个活跃会话固定占一行/一张卡片**，实时展示状态与已运行时长，而非滚动历史。

### GATT Service / Characteristic

| 项目 | 值 |
|------|------|
| 设备广播名 | `OpenWaifu` |
| Service | `0000fd50-0000-1000-0880-00805f9b34fb` |
| Write 特征 | `00000001-0000-1001-8001-00805f9b07d0` |
| 编码 | UTF-8，单条命令最大 240 字节（超长按字符边界截断） |

> 未配置 `--ble-address` 时，守护进程会按设备名 `OpenWaifu`（可用 `OPENWAIFUD_BLE_DEVICE_NAME` 覆盖）自动扫描连接，这在 macOS（地址为随机 UUID）上尤其方便。

### 会话命令行协议

每条命令以单字符命令码开头，字段用 `|` 分隔（字段内的 `|`、换行会被替换为空格）：

| 命令 | 格式 | 含义 |
|------|------|------|
| 清空 | `C` | 清空所有会话（重连后先下发，再逐个同步） |
| 移除 | `X\|<sid>` | 移除某会话（完成/中止/超时） |
| 更新 | `S\|<sid>\|<st>\|<elapsed>\|<plugin>\|<task>` | 新增或更新某会话 |

其中 `<st>` 为单字符状态码，`<elapsed>` 为已运行秒数（整数），`<plugin>` 为来源插件（空则为 `agent`），`<task>` 为当前任务文本。

### 状态码映射表

| AgentStatus | 状态码 | 屏幕标签 |
|-------------|--------|----------|
| `thinking` | `T` | 思考中 |
| `coding` | `C` | 编码中 |
| `testing` | `V` | 测试中 |
| `error` | `E` | 出错 |
| `idle` | `I` | 完成 |

> `idle` 表示会话已结束：固件会将其标记为「完成」并冻结计时，停留数秒后由守护进程下发 `X` 移除。

### 情绪状态机（按活跃会话数量）

固件根据当前活跃会话数量切换整体「情绪」与布局：

| 活跃会话数 | 情绪 | 布局 |
|------------|------|------|
| 0 | 睡觉中 | 居中大号 Zzz |
| 1 | 摸鱼中 | 单任务大卡片（大号计时器） |
| 2-3 | 认真搬砖 | 会话列表 |
| 4-6 | 火力全开 | 会话列表 |
| >6 | 要炸了 | 会话列表 |

### 会话生命周期参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `session_done_linger` | `5.0` | 会话完成后「✓完成」停留秒数，之后移除 |
| `session_idle_timeout` | `60.0` | 会话超过该秒数无更新则自动移除 |
| `session_sweep_interval` | `1.0` | 后台清扫器扫描周期（秒） |

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
│   ├── client.py        # BLE 主机连接管理（扫描/直连、重连、写入）
│   └── protocol.py      # BLE 会话命令行协议（S/X/C 命令 + UTF-8 编码）
└── state/
    ├── __init__.py
    └── manager.py       # 异步会话状态管理器（会话注册表 + 清扫器 + BLE 队列消费）
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
