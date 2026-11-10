[English](README.md) · **中文**

# minideer — DeerFlow harness 的最小复现

一个只用标准库的 Python 复现，对应
[`bytedance/deer-flow`](https://github.com/bytedance/deer-flow) 在提交
`cf8c29c`（harness `2.3.0-dev`）上的核心架构：

> DeerFlow 并不拥有 model/tool 循环。它组装一个 **lead agent**（有序中间件栈 +
> 一份静态 system prompt），然后交给 LangChain 的 `create_agent`。在这张图外面，
> 它拥有 **run 记录**、**按线程分配的沙箱**，以及一套让 system prompt 保持稳定的
> 上下文策略：记忆是一份冻结的提醒，压缩会 **删掉** 旧消息，摘要在每次模型调用时
> 重新注入，但不会写回线程。

复现的是架构，不是产品。没有真实的 LLM：`ScriptedModel` 回放确定的动作，并记录它收到的每一次请求。

研究文章：[`research/deer-flow.zh.md`](../../research/deer-flow.zh.md)

## 对应关系

| minideer | 上游 deer-flow（`cf8c29c`） | 复现的想法 |
|---|---|---|
| `loop.py::Agent` | `lead_agent/agent.py` 的 `create_agent` + `worker.py` 的 `agent.astream` | model → tools → model；每步一份新请求；列表中的第一个中间件是最外层；`after_model` 按注册的逆序执行；run 状态和对话记录是两套时钟 |
| `harness.py::assemble_lead` | `build_middlewares` + `_insert_hitl_approval_middleware` | 审批插在最前面；澄清放在末尾；system prompt 里只有技能 **元数据**；GitHub 渠道不提供 `update_agent` |
| `context.py::DynamicContextMiddleware` | `dynamic_context_middleware.py` | 日期和记忆是一条持久化的 `<system-reminder>`，只写一次；同一天是空操作；跨过午夜只追加日期，不刷新记忆 |
| `context.py::SummarizationMiddleware` | `DeerFlowSummarizationMiddleware._maybe_summarize` | 被压缩的消息从线程里 **删除**；`summary_text` 留下 |
| `context.py::DurableContextMiddleware` | `durable_context_middleware.py` | 摘要、委托账本和技能只渲染进这一次请求 |
| `context.py::ToolOutputBudgetMiddleware` | `tool_output_budget_middleware.py` | 达到阈值的输出写进沙箱，模型只看到摘要 |
| `context.py::SkillActivationMiddleware` | `skill_activation_middleware.py` | `/skill-name` 只为这一次模型调用装入全文，不写入状态 |
| `sandbox.py` | `SandboxMiddleware` | 第一次工具调用时才获取沙箱；lead 拥有 `skill_view`；子 agent 不得重建它 |
| `subagents.py` | `task_tool` + `SubagentExecutor` | 子消息是 `[任务]`；共享沙箱 id；只返回最终文本；没有 `task` 工具；`checkpointer is False` |
| `hitl.py` | `HitlApprovalMiddleware` + `ClarificationMiddleware` | 审批是可序列化的暂停；非交互运行直接拒绝；澄清结束本回合（`goto=END`），不能 resume |
| `memory.py` | `MemoryMiddleware` | 事实在 `after_agent` 入队，所以暂停中的 run 还没有写记忆 |

有意 **没有** 复现的部分：LangGraph 检查点、SSE 流、真实的 DeerMem 抽取器、IM 渠道适配器、MCP、provisioner/K8s 沙箱、扩展加载和授权提供者。

## 运行

```bash
./run.sh          # venv -> pip install -e .[test] -> 构建 wheel -> pytest -> demo
./run.sh demo     # 只跑 demo，不安装（python3 >= 3.10）
python3 mutation_check.py   # 注入 8 个架构回归，每一个都必须让测试失败
```

Demo 走一轮脚本化的 `/triage`：冻结的记忆提醒、只注入一次的技能正文、被外置的日志、一个通过共享沙箱写文件的隔离子 agent，以及只存在于请求里的 durable 块。它检查 14 条不变量，任何一条失败就以非零退出。

## 测试（20）

| 文件 | 钉住的行为 |
|---|---|
| `tests/test_loop.py` | 循环退出、工具结果重新进入模型、system prompt 稳定、先限制再审批 |
| `tests/test_context.py` | 冻结提醒、午夜日期行、破坏性摘要、悬空工具调用、外置、懒加载沙箱 |
| `tests/test_subagents.py` | 消息隔离、只返回最终文本、共享沙箱、技能视图、并发上限、子 agent 没有 checkpointer |
| `tests/test_hitl_skills_runs.py` | 审批暂停/恢复、非交互拒绝、澄清与 resume 的区别、斜杠技能只注入一次、GitHub 不给 `update_agent`、tool 模式记忆 |

## 局限

- 同一轮里的工具调用是 **串行** 的。上游在 tools 节点里把它们扇出。
- 长度按字符计。上游的外置阈值是 12,000 个字符（`externalize_min_chars`）；demo 把阈值调低，好让脚本保持短小。
- “线程”就是传回 `run` 的状态字典。lead 的 `checkpointer` 是一个空字典，代表 worker 稍后挂上的对象；子 agent 明确是 `False`。
- `bash` 是内存里的桩，不是 shell。沙箱是按线程 id 索引的文件字典。
- 悬空工具调用是靠在末尾追加一条合成结果来补上的。上游在 `wrap_model_call` 里把结果插到那条 `AIMessage` 的紧后面（`dangling_tool_call_middleware.py:16-18`），因为 `before_model` 的更新会被追加到列表末尾。
