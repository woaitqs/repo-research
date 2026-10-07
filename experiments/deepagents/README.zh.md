[English](README.md) · **中文**

# minideep —— deepagents harness 的最小复现

这是一个约 1,200 行（含 docstring，不含演示）、**只依赖标准库**的 Python 包，复现了
[`langchain-ai/deepagents`](https://github.com/langchain-ai/deepagents)（研究基于提交 `16e84d9`，SDK `0.7.22`）的核心架构思想：

> deep agent 不是一个新的运行时。它是一个**普通的工具调用循环**，加上一个**有序的中间件栈**：
> 中间件改写每次模型请求、包裹每次工具调用，并把大块内容移进一个**可插拔的虚拟文件系统**，让上下文保持在上限之内。

它复现的是架构，而不是产品。这里没有真实的 LLM：`ScriptedModel` 按脚本回放确定的动作，并记录收到的每一个请求，
因此每一条架构结论都能被精确检查。

研究文章：[`research/deepagents.zh.md`](../../research/deepagents.zh.md)（[English](../../research/deepagents.md)）

## 对应关系

| minideep | 上游 deepagents（提交 `16e84d9`） | 复现的思想 |
|---|---|---|
| `loop.py::Agent` | `langchain/agents/factory.py::create_agent`（deepagents 调用的 LangChain 函数） | 模型 → 工具 → 模型的循环；每一步新建 `ModelRequest`；洋葱式组合的 `wrap_model_call`/`wrap_tool_call`；没有工具调用时退出 |
| `middleware.py` | `langchain.agents.middleware.types.AgentMiddleware` | `before_agent`、`wrap_model_call`、`wrap_tool_call`、中间件贡献的 `tools`；单次调用内的修改与显式 `state_update` 的区别 |
| `graph.py::create_deep_agent` | `libs/deepagents/deepagents/graph.py::create_deep_agent` | 栈顺序；自动加入 `general-purpose` 子 Agent；子 Agent 没有 `task` 工具 |
| `backends.py` | `deepagents/backends/{protocol,state,filesystem,local_shell,composite}.py` | `StateBackend` 通过运行时写入图状态；`CompositeBackend` 按最长前缀路由；`execute` 能力探测 |
| `filesystem.py` | `deepagents/middleware/filesystem.py::FilesystemMiddleware` | 没有 shell 时隐藏 `execute`；超过 2 万 token 的结果卸载到 `/large_tool_results/<id>`，留下首尾预览；同路径修改冲突检查 |
| `summarization.py` | `deepagents/middleware/summarization.py` | 通过 `_summarization_event` 实现**非破坏性**压缩；历史卸载到 `/conversation_history/<session>.md`；安全切分点；`ContextOverflowError` 回退 |
| `subagents.py` | `deepagents/middleware/subagents.py::_build_task_tool` | `task` 工具；隔离的子 Agent 上下文；只返回最终文本；共享的 `files` 回写到父 Agent |
| `memory.py` | `deepagents/middleware/{memory,skills}.py` | AGENTS.md 每个线程只加载一次，注入系统提示词；skills 只展示元数据（渐进披露） |
| `graph.py::PatchToolCallsMiddleware` | `deepagents/middleware/patch_tool_calls.py` | 修补被中断的运行留下的、没有结果的工具调用 |

**有意不复现**的部分：LangGraph 的检查点、流式输出和中断（HITL）、异步/远程子 Agent、fork 模式子 Agent、
harness/provider profile、提示词缓存、文件系统权限、多模态处理，以及沙箱中 `execute` 的源头捕获卸载。

## 运行

```bash
./run.sh          # venv -> pip install -e .[test] -> 构建 wheel -> pytest -> 演示
./run.sh demo     # 只跑演示，不安装任何东西（python3 >= 3.10）
```

演示按脚本排查一套失败的测试。过程中它会：卸载一个 170 KB 的 `execute` 输出，用 grep 搜索被卸载的文件，
按需读取一个 skill，委派给一个隔离的子 Agent，分页读取一份日志直到摘要压缩触发两次，
再把一条偏好写进路由到状态里的记忆。最后检查 10 条不变式，任何一条不成立都会以非零状态码退出。

## 测试（21 个）

| 文件 | 固定下来的行为 |
|---|---|
| `tests/test_loop.py` | 循环的退出条件、并行工具调用、错误作为观察结果、洋葱顺序、单次调用内的提示词修改从不写入状态 |
| `tests/test_filesystem.py` | `execute` 的启用条件、卸载与分页读回、`read_file` 从不被卸载、并行修改冲突检查、组合后端路由与产物根目录 |
| `tests/test_summarization.py` | 状态中保留完整历史、视图有上限、连续写入的卸载文件、安全切分点、被动的超长处理路径 |
| `tests/test_subagents.py` | 隔离、只返回最终文本、`files` 回写、不递归、私有状态不共享 |
| `tests/test_memory_and_skills.py` | 记忆注入、**同一线程内记忆过期**、skill 只展示元数据、悬空工具调用的修补 |

## 上游探针（用*真实* SDK 验证研究结论）

`upstream_probe/probe_deepagents.py` 用一个脚本化的假 `BaseChatModel` 驱动真实的 `deepagents.create_deep_agent`。
它不需要 API key，也不需要网络，共执行 7 项检查。`upstream_probe/probe_output.json` 是记录下来的输出。

```bash
python3 -m venv /tmp/da && /tmp/da/bin/pip install "deepagents==0.7.22"   # 或 -e <clone>/libs/deepagents
ANTHROPIC_API_KEY=dummy /tmp/da/bin/python upstream_probe/probe_deepagents.py
```

| 检查项 | 观察结果（deepagents 0.7.22） |
|---|---|
| P1 默认主中间件栈 | `Filesystem, SubAgent, Summarization, PatchToolCalls, AnthropicPromptCaching, UnsupportedContent` |
| P2 `StateBackend` 下的 `execute` | 对模型隐藏 |
| P3 17 万字符的工具结果 | 写入 `/large_tool_results/call_big_1`；模型看到的是一个 1,812 字符的存根 |
| P4 子 Agent 的输入/输出 | 输入 `[SystemMessage, HumanMessage(description)]`；输出只有 `"Report written to /report.md"`；子 Agent 写的 `/report.md` 出现在父 Agent 的 `files` 中；子 Agent 没有 `task` 工具 |
| P5 摘要压缩 | 状态保留 44 条消息；`_summarization_event.cutoff_index = 39`；最后一次模型调用只看到 6 条消息 |
| P6 记忆 | `edit_file` 把 AGENTS.md 改成 "likes rust" 之后，第二轮的系统提示词仍然写着 "likes python" |
| P7 在隔离的子 Agent 中删除文件 | 子 Agent 报告 `Deleted /old.md`，但 `/old.md` 仍在父 Agent 的 `files` 中 |

## 真实模型运行

`real_model/run_ark.py` 通过任意 OpenAI 兼容接口，用真实模型驱动真实 SDK。
实际运行时用的是火山引擎方舟的 plan 接口和 `deepseek-v4.1-flash`。API key 只从环境变量读取，脚本不会把它写到磁盘上。

```bash
export ARK_API_KEY=...                                                  # 切勿提交
export ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/plan/v3       # 默认值
export ARK_MODEL=deepseek-v4.1-flash
python real_model/run_ark.py --check       # 验证 key，并发一次很小的补全请求
python real_model/run_ark.py --scenarios   # S1 卸载指针、S2 委派说明、S3 写记忆、S4 摘要后的回忆
```

`results_run{1,2,3}.json` 是三轮运行的记录，每一轮 4 个场景都通过。详情见研究文章的
[验证](../../research/deepagents.zh.md#验证verification)一节。

## 局限

- 同一轮模型输出中的多个工具调用是**顺序**执行的。上游会并行扇出，每个调用对应一个 LangGraph `Send`。
- token 计数用的是 `字符数 / 4`。上游用 `count_tokens_approximately`，并且会把工具 schema 也算进去。
- 所谓"线程"只是把返回的状态再传回 `invoke`，没有 checkpointer。
- `ShellBackend` 在宿主机上执行命令，和上游的 `LocalShellBackend` 一样。它的路径检查不是沙箱。
- 摘要压缩的阈值是绝对的 token 数或消息数。上游根据模型的上下文窗口按比例计算（触发 85% / 保留 10%）。
  演示还展示了保留窗口太接近触发阈值时会出什么问题：每次调用都会执行压缩。
