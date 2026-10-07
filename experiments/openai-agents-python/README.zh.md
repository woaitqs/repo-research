[English](README.md) · **中文**

# miniagents —— openai-agents-python 架构的最小复现

研究文章：[`research/openai-agents-python.zh.md`](../../research/openai-agents-python.zh.md)（[English](../../research/openai-agents-python.md)）·
上游：[openai/openai-agents-python @ 71c2da4](https://github.com/openai/openai-agents-python/tree/71c2da4de47159ccc37905b8fe781be805dbfa66)

`miniagents` 是约 1,900 行**只依赖标准库**的 Python（不含演示约 1,700 行），用到 asyncio、json、sqlite3、subprocess、urllib。
它复现的是**架构**，而不是产品：没有流式输出、tracing、MCP、realtime、voice、托管工具，也没有一大堆模型提供方。

## 复现了什么

| miniagents | 上游（固定提交中的路径） | 思想 |
|---|---|---|
| `run.py::Runner.run` | `src/agents/run.py:1026`（`while True`） | 一个循环运转在四状态的 `NextStep` 状态机上；一轮 = 一次模型调用 + 它的副作用 |
| `run.py::run_single_turn` | `src/agents/run_internal/run_loop.py:2665` | 指令、工具和 handoff 每轮重新解析；输入 = 调用方 item + 可重放的已生成 item |
| `run.py::process_model_response` | `src/agents/run_internal/turn_resolution.py:2926` | 对输出 item 分类；只按工具名识别 handoff；未知工具默认抛出，除非设置 `tool_not_found_behavior="return_error_to_model"` |
| `run.py::execute_tools_and_side_effects` | `turn_resolution.py:804` | 先规划审批，再并发执行，输出保持模型顺序；然后依次判断 handoff、`tool_use_behavior`、最终输出或再跑一轮 |
| `run.py::execute_handoff` | `turn_resolution.py:537` | 第一个 handoff 生效；默认传递完整的原始对话记录；`input_filter` 或 `nest_handoff_history` 只改变模型视图 |
| `run.py::RunState` | `src/agents/run_state.py:835` | JSON 形式的暂停点；Agent 按名称重新绑定；恢复时把暂停的那一轮执行完，不再调用模型 |
| `agent.py::Agent.as_tool` | `src/agents/agent.py:606` | 一个 `FunctionTool`，只用生成的 `input` 运行嵌套的 `Runner.run` |
| `tool.py::function_tool` | `src/agents/tool.py:2574`、`:1980` | 从函数签名生成严格 JSON schema；同步函数放进 `asyncio.to_thread`；失败变成一条固定的观察结果 |
| `session.py` | `src/agents/memory/session.py:53` | 四个方法的 session 协议；历史拼接在输入之前；`limit` 保留最新的 item |
| `trimmer.py::ToolOutputTrimmer` | `src/agents/extensions/tool_output_trimmer.py:88` | 需显式开启的 `call_model_input_filter`，只在模型视图中缩短旧的工具输出 |
| `capabilities.py` | `src/agents/sandbox/runtime_agent_preparation.py:86`、`capabilities/*.py` | 公开 Agent 与每次运行的执行克隆；`Shell`、`Skills`（索引放进提示词，正文留在磁盘上）、`Memory`（摘要放进提示词）、`Compaction`（`context_management` + 在压缩 item 处切断） |
| `model.py::ChatCompletionsModel` | `src/agents/models/chatcmpl_converter.py:534`、`:1040` | 输入 item、输出 item；handoff 在线上变成普通的函数工具 |

## 目录结构

```text
src/miniagents/
  items.py         Responses 风格的 item、RunItem 元数据、孤立调用剪除
  model.py         Model 协议、ScriptedModel、标准库实现的 Chat Completions 适配器
  tool.py          FunctionTool、@function_tool、默认错误函数
  agent.py         Agent、Handoff、handoff()、as_tool()、护栏
  run.py           Runner 循环、NextStep 状态机、RunState、审批、handoff
  session.py       InMemorySession、SQLiteSession、SessionSettings
  capabilities.py  Workspace、Capability、Shell、Skills、Memory、Compaction、CapableAgent
  trimmer.py       ToolOutputTrimmer
  demo.py          带解说的端到端演示（11 项检查）
tests/             40 个测试，每个文件对应一个架构关注点
mutation_check.py  注入 11 个架构层面的回归，每个都必须让某个测试失败
real_model/        run_ark.py 驱动真实的上游 SDK；run_mini_ark.py 驱动 miniagents
upstream_probe/    probe_openai_agents.py：对真实 SDK 的 17 个运行时探针（不需要网络）
```

## 运行

```bash
./run.sh          # venv -> pip install -e .[test] -> 构建 wheel -> pytest -> 演示
./run.sh demo     # 只跑演示，不安装任何东西
python3 mutation_check.py   # 解释器上需要有 pytest（例如 .venv/bin/python）
```

真实模型运行需要一个 OpenAI 兼容接口（本研究用的是火山引擎方舟）：

```bash
# 用真实模型运行复现
ARK_API_KEY=... ARK_MODEL=deepseek-v4-1-flash-260910 python3 real_model/run_mini_ark.py --out mini.json
# 用真实模型运行真实的上游 SDK（在固定提交的 SDK 检出目录中运行）
ARK_API_KEY=... ARK_MODEL=deepseek-v4-1-flash-260910 uv run --frozen python real_model/run_ark.py --out r.json
# 用脚本化模型运行真实的上游 SDK（不需要网络）
uv run --frozen python upstream_probe/probe_openai_agents.py out.json
```

密钥只从环境变量读取。结果文件中只有模型输出，从不包含密钥。

## 已验证的结果（本次会话，Python 3.13.16）

| 检查 | 结果 |
|---|---|
| `./run.sh` | 安装、构建 wheel `miniagents-0.1.0-py3-none-any.whl`、**40 passed**、演示 **11/11** |
| `mutation_check.py` | **11/11** 个变异被发现（每个让 1–4 个测试失败）；恢复后 40 个测试通过 |
| `real_model/run_mini_ark.py` | **6/6** 轮（deepseek-v4.1-flash ×3，doubao-seed-2.1-pro ×3）：handoff、`as_tool` 任务说明、审批暂停、JSON 往返、恢复后恰好一次模型调用 |
| `upstream_probe/probe_openai_agents.py` | 在真实 SDK（0.23.1 之后 22 个提交）上 **17/17** 个预测吻合 |
| `real_model/run_ark.py` | 6 个场景 × 2 个模型 × 3 轮；见 `real_model/results_*.json` 和研究文章的"验证"一节 |

## 已知局限

- 没有流式路径。上游还有第二个循环（`start_streaming`，`run_loop.py:969`），必须在行为上与非流式循环保持一致；miniagents 只有一个循环。
- `Workspace` 是一个临时目录加上 `subprocess`，没有任何隔离，类似 Linux 上的 `UnixLocalSandboxClient`。它不是沙箱。
- 没有 tracing、钩子、工具护栏、超时、结构化工具输出、MCP、托管工具和服务端管理的对话。
- `RunState` 只存 Agent 名称，没有上游那样更丰富的身份信息（重名 Agent、嵌套 agent-tool 状态、schema 版本）。
- `Compaction` 只演示了形态（`context_management` 参数 + 在 `compaction` item 处切断）；这里没有服务端真正执行压缩。
