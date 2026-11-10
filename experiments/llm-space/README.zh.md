[English](README.md) · **中文**

# minispace — LLM Space 工作台的最小复现

对 [`deer-flow/llm-space`](https://github.com/deer-flow/llm-space) 核心架构的纯标准库 Python 复现（研究提交 `342c9f5`，桌面包 `4.20.0`）：

> LLM Space 是一个桌面工作台，不是一个在模型循环内部把工具跑完的 agent。它向 Pi Agent Core 只要 **一步模型调用**。每个工具执行器都立刻带着 terminate 返回，于是 Pi 停下。然后由工作台决定：留给人来填、把可执行的工具跑一次，或者在上限内继续交替。Prompt 模板留在线程文件里。快照冻住上一次运行已经渲染出来的内容。压缩写出的是 **一份新的** 消息列表。Run 的正文放在工作区旁边，评估是人打的分。

复现的是架构，不是产品。没有真实的 LLM：`ScriptedModel` 回放确定的动作，并记录每一次请求。

研究文章：[`research/llm-space.zh.md`](../../research/llm-space.zh.md)

## 对应关系

| minispace | 上游 llm-space（`342c9f5`） | 复现的想法 |
|---|---|---|
| `loop.py::PiStep` | `packages/core/src/server/agent/stream.ts` `_convertToAgentTools` | 工具调用结束借来的循环；这一步不执行工具 |
| `workbench.py::Workbench.run` | `packages/ui/.../stores/thread-store.ts` `run` | `step` 停下；`auto` 执行一次；`react` 继续，最多 50 次模型回合 |
| `tools.py` | `isExecutableTool`、`isDangerousBashCommand` | function 工具、`ask_user_question`、`spawn_agent` 和危险的 `bash` 留给人 |
| `render.py` | `renderThreadPromptVariables` | 存档里的模板不变；已有的快照位置优先 |
| `compaction.py` | `planCompaction` / `applyCompactionPreview` / `nextCompactedThreadPath` | 保留最近的用户回合；检查点是一条新的用户消息；源列表不被修改；文件名沿着同一序列 |
| `storage.py::Home` | `LocalFileSystem` + `RunHistoryStore` | run 正文离开工作区文件；删除后仍保留；孤儿到期回收 |
| `subagent.py` | `buildSubagentThread` | 一个兄弟线程，保留 system 和 meta，丢掉父对话，子线程不运行 |
| `evaluation.py` | `saveEvaluation` | 分数是 1–5，rubric 被复制，不调用模型 |
| 远程 plugin 路径 | `apps/desktop/src/client/tool-execution.ts` | 非本地 runtime 上的 plugin 工具变成错误结果，plugin 正文不会被调用 |

有意 **不** 复现：Electrobun、真实的 Pi 流、MCP 传输、plugin 子进程、Langfuse 导入、LangGraph 项目生成器，以及 provider-hosted 工具。

## 运行

```bash
./run.sh          # venv -> pip install -e .[test] -> 构建 wheel -> pytest -> demo
./run.sh demo     # 只跑 demo，不安装（python3 >= 3.10）
python3 mutation_check.py   # 9 个架构回归；每一个都必须让测试失败
```

Demo 走一轮脚本化的分拣：冻住的日期占位符、`skill` 然后 `bash` 的 ReAct、工作区之外归档的 run、一个压缩副本，以及一个没有被执行的子线程。它断言 21 条不变量，任一失败就以非零退出。

## 测试（25）

| 文件 | 钉住的行为 |
|---|---|
| `tests/test_loop.py` | step / auto / react、人工关卡、危险 bash、远程 plugin、50 回合上限、什么样的对话可以开跑 |
| `tests/test_context.py` | 存档模板、快照复用、新位置用新值渲染 |
| `tests/test_compaction_storage.py` | 逐字保留的尾巴、渐进检查点、兄弟文件名、截断的工具文本、删除后仍在的历史 |
| `tests/test_subagent_eval.py` | 子线程隔离、按角色过滤工具、rubric 副本、1–5 之外的分数 |

## 局限

- 一个脚本模型同时代替 Pi 和供应商。上游的模型调用是 `agentLoopContinue` 里面的 `models.streamSimple`。
- 本复现里的占位符是应用使用的双花括号形式。Nunjucks 的文件包含和条件没有复现。
- 一个 “thread” 是 dict。上游把 JSON 放在 `~/.llm-space/workspace`，把 run 文件按哈希放在 `history/`。
- 工具执行器是普通函数。上游用 `Promise.all` 展开一批，并把 builtin、MCP、plugin 分到不同客户端。
- 上限和工作台一致：50 次模型回合，第 50 回合的工具仍然会执行。
