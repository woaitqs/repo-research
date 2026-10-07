[English](README.md) · **中文**

# mini_openhands —— OpenHands V1 Agent 内核的最小复现

这是一个很小的 Python 包（约 1.3k 非空行，含 docstring 和演示），没有运行时依赖，重建了 OpenHands 的**架构内核**
（即 [`OpenHands/software-agent-sdk`](https://github.com/OpenHands/software-agent-sdk) 中的 `openhands-sdk` 包，
`OpenHands/OpenHands` / Agent Canvas 底层运行的就是它）。它复现的是架构，而不是产品：没有界面、没有 Docker、没有 LiteLLM。

复现了什么，以及每个思路出自哪里：

| 思路 | mini_openhands | 上游（software-agent-sdk @ v1.53.0） |
|---|---|---|
| 事件溯源的会话，每个事件一个 JSON 文件 | `event_log.py`、`state.py` | `conversation/event_store.py`、`conversation/state.py`、`persistence_const.py` |
| 很小的可变快照（`base_state.json`），字段变化时自动保存 | `state.py`（`__setattr__`） | `ConversationState.__setattr__` / `_save_base_state` |
| 无状态 agent；每轮循环一次 `step()`；只通过 `on_event` 产生副作用 | `agent.py` | `agent/agent.py`（`Agent.step`、`_step`） |
| Conversation 掌管循环、锁、状态机、最大迭代次数 | `conversation.py` | `conversation/impl/local_conversation.py`（`_run`） |
| LLM 上下文 = 日志的投影（`View`），而不是日志本身 | `view.py` | `context/view/view.py`、`ConversationState.view` |
| 压缩是一个追加的事件（被遗忘的 id + 摘要 + 偏移） | `condenser.py`、`events.Condensation` | `context/condenser/llm_summarizing_condenser.py`、`event/condenser.py` |
| 切分点永远不把工具调用和它的结果拆开 | `View.manipulation_indices` | `context/view/properties/*` |
| 上下文溢出 -> `CondensationRequest` -> 强制压缩 | `agent.py` | `Agent._step` 中的 `except LLMContextWindowExceedError` |
| 错误的工具调用变成模型可见的 `AgentErrorEvent`（循环继续） | `agent._to_action_events` | `Agent._get_action_event`、`_emit_tool_error` |
| 通过未匹配动作实现确认模式；第二次 `run()` = 批准 | `state.get_unmatched_actions`、`agent.step` | `ConversationState.get_unmatched_actions`、`Agent._requires_user_confirmation` |
| 工具规格（数据）-> 注册表工厂 -> 可执行定义 | `tools.py` | `tool/spec.py`、`tool/registry.py`、`tool/tool.py` |
| 大输出：保留头部 + 尾部，截掉中间，全文卸载到 `observations/` | `tools.bound_output` | `utils/truncate.py` 的 `maybe_truncate(save_dir=...)`、TerminalObservation.to_llm_content |
| 工作区边界（目前是本地，之后可以是远程/docker） | `workspace.py` | `workspace/base.py`、`workspace/remote/base.py` |
| 从磁盘恢复；工具只能增加，不能删除 | `Conversation.__init__`、`Agent.verify` | `ConversationState.create`、`AgentBase.verify` |
| 基于重复动作/观察结果的卡死检测 | `stuck.py` | `conversation/stuck_detector.py` |
| 单一模型边界、重试、规范化的溢出错误 | `llm.py` | `llm/llm.py`、`llm/utils/retry_mixin.py` |

## 目录结构

```text
experiments/openhands/
├── README.md
├── README.zh.md
├── pyproject.toml
├── run.sh                      # 安装 + 构建 + 测试 + 离线演示（+ --live）
├── src/mini_openhands/
│   ├── events.py               # 带类型的不可变事件 + events_to_messages
│   ├── event_log.py            # 基于文件的只追加日志
│   ├── state.py                # ConversationState：base_state.json + 日志 + view
│   ├── view.py                 # 投影 + manipulation indices（可切分点）
│   ├── condenser.py            # 滚动式摘要压缩器
│   ├── tools.py                # ToolSpec / 注册表 / ToolDefinition / 内置工具
│   ├── workspace.py            # 执行边界
│   ├── llm.py                  # LLM 协议、ScriptedLLM、OpenAI 兼容客户端
│   ├── agent.py                # 无状态的 Agent.step()
│   ├── conversation.py         # 循环、锁、状态、持久化、恢复
│   ├── stuck.py                # 卡死检测
│   └── demo.py                 # 端到端演示（脚本化或 --live）
├── tests/                      # 19 个行为测试（pytest）
└── probe/
    ├── probe_real_sdk.py       # 对*真实* SDK 的运行时探针，使用脚本化 TestLLM
    └── probe_real_sdk_live.py  # 同上，但使用真实的 OpenAI 兼容模型
```

## 运行

```bash
./run.sh            # 创建 .venv、pip install -e .[test]、compileall、pytest、脚本化演示
./run.sh --live     # 另外用真实的 OpenAI 兼容模型运行演示
```

`--live` 从环境变量读取 `ARK_API_KEY`（必填）、`ARK_BASE_URL`（默认
`https://ark.cn-beijing.volces.com/api/coding/v3`，火山引擎方舟 Coding Plan，OpenAI 兼容）
和 `ARK_MODEL`（默认 `doubao-seed-2-1-pro-260915`）。任何 OpenAI 兼容端点都可以。不要提交 key；`.env` 已被 git 忽略。

注意：方舟的 `ark-code-latest` 路由模型对完全相同的请求间歇性返回
`400 InvalidParameter: messages.tool_calls.type`（2026-10-07 重放 4 次中 1 次），因此默认值固定为一个具体模型。

## 脚本化演示展示了什么

1. `file_editor create` → `terminal python3 fib.py` → 一个 JSON 格式错误的工具调用
   （变成 `AgentErrorEvent`，循环继续）→ `seq 1 50000`（观察结果被限制在约 2 KB，完整输出写入 `observations/`）
   → 触发压缩（`forgot=8 offset=2`）→ `finish`。
2. 一个使用相同 id 的**新** `Conversation` 对象从磁盘恢复，看到 15 个事件，不会重新发出系统提示词，并继续对话。

## 对真实 SDK 的探针

`probe/probe_real_sdk.py` 用 SDK 自带的脚本化 `TestLLM` 驱动真实的 `openhands-sdk` v1.53.0（`LocalConversation`
+ `TerminalTool` + `FileEditorTool` + `LLMSummarizingCondenser`），并打印持久化下来的内容。需要在 `v1.53.0` 的
`software-agent-sdk` 检出目录中运行：

```bash
uv sync --frozen
OPENHANDS_SUPPRESS_BANNER=1 uv run --frozen python /path/to/probe_real_sdk.py /tmp/probe-out
```

我们记录的脱敏输出在
[`assets/openhands/real-sdk-probe-report.json`](../../assets/openhands/real-sdk-probe-report.json)。

`probe/probe_real_sdk_live.py` 做同样的事，但通过 LiteLLM（`openai/<model>` 加 `base_url`）接入真实的 OpenAI 兼容模型，
从环境变量读取 `ARK_API_KEY` / `ARK_BASE_URL` / `ARK_MODEL` 以及 `PROBE_MAX_SIZE`（压缩器的 `max_size`）。

## 验证（2026-10-07）

| 检查项 | 命令 | 结果 |
|---|---|---|
| 安装 + 构建 | `./run.sh`（venv、`pip install -e ".[test]"`、`compileall`） | 通过 |
| 测试 | `python -m pytest -q` | **19 passed** |
| 离线演示 | `python -m mini_openhands.demo` | finished；`Condensation forgot=8 offset=2`；从磁盘恢复出 15 个事件，第二轮后为 19 个 |
| 在线演示 | `ARK_API_KEY=... python -m mini_openhands.demo --live` | `doubao-seed-2-1-pro-260915`：finished，42 个事件，4 次真实压缩，模型从自己无效的 `file_editor` 调用中恢复（`AgentErrorEvent`；日志截取的尾部中至少 2 个），`fib.py` + `test_fib.py` 已创建（[日志](../../assets/openhands/mini-openhands-live-run.txt)） |
| 真实 SDK，离线 | `probe/probe_real_sdk.py` | 见[报告](../../assets/openhands/real-sdk-probe-report.json) |
| 真实 SDK，在线 | `probe/probe_real_sdk_live.py`（`PROBE_MAX_SIZE=12` 和 `8`） | 见[报告](../../assets/openhands/real-sdk-live-ark-report.json) |

## 已知局限（有意为之）

* 只有同步版本；没有 `arun()`、流式输出、中断或并行工具执行。
* 没有会话树 / 分叉（`parent_id`、`leaf_event_id`）——日志是线性的。
* 校验只实现了很小的 JSON Schema 子集，而不是 Pydantic Action 模型。
* 基于 token 的压缩被替换为基于事件数量的阈值。
* 没有安全分析器、钩子、技能、MCP、子代理或 agent-server；这些在研究文章中说明。

研究文章：[English](../../research/openhands.md) · [中文](../../research/openhands.zh.md)
