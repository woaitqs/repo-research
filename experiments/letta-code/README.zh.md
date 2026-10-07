[English](README.md) · **中文**

# minilc —— letta-code 架构的最小复现

这是一个约 1,100 行（含 docstring，不含演示）、**只依赖标准库**的 Python 包，复现了
[`letta-ai/letta-code`](https://github.com/letta-ai/letta-code)（研究基于提交 `4b028fa`，v0.34.4）的核心架构思想：

> agent 循环被**拆在一个契约的两侧**。有状态的*后端*每个 run **只执行一步模型调用**，
> 模型一想调用工具就以 `requires_approval` 停下。*客户端 harness* 对权限分类、在本地执行工具，
> 再用一条 `approval` 消息开始下一个 run。记忆是一个**从 HEAD 编译进 system prompt 的 git 仓库**，
> 为了提示词缓存保持字节不变；新提交到来时只下发一次增量。

它复现的是架构，而不是产品。`ScriptedModel` 扮演 LLM 并记录收到的每个请求，因此每一条结论都能被精确检查。
`OpenAICompatModel` 可以对接任意 OpenAI 兼容的端点。

研究文章：[`research/letta-code.zh.md`](../../research/letta-code.zh.md)（[English](../../research/letta-code.md)）

## 对应关系

| minilc | 上游 letta-code（`4b028fa`） | 复现的思想 |
|---|---|---|
| `backend.py::Backend`、`LocalBackend` | `src/backend/backend.ts`（`Backend`）、`local/local-backend.ts`、`dev/fake-headless-backend.ts` | 一个契约、可替换的后端；每个会话只有一个活动 run；每个 run 一步模型调用；工具调用 → `approval_request_message` + `requires_approval`；悬空的工具调用以"Turn did not complete"收尾；有上限的溢出压缩（≤3）和暂时性错误重试（≤3） |
| `transcript.py` | `local/local-store.ts`、`local/local-transcript.ts` | 只追加的 JSONL；压缩追加一行，只替换 `in_context_ids` |
| `compaction.py` | `dev/provider-turn-executor.ts:241-272`、`local/compaction.ts` | 阈值 `窗口 − min(16384, 20%)`；滑动窗口（从 30% 开始，每次 +10%），只在 assistant 消息处切分；摘要以 user 角色的 `system_alert` 回到上下文 |
| `memfs.py` | `local/system-prompt-compilation.ts` | v2 布局：根目录 `*.md` = 核心记忆，`MEMORY.md` 索引，子目录延迟加载；**只读取 `git show HEAD:`**；`<memory_update>` 增量 |
| `harness.py::Harness` | `src/headless.ts:2081-2667`、`agent/message.ts`、`cli/helpers/stream.ts` | 客户端回合循环；每个请求都带 `client_tools`/`client_skills`；带审批项的 `end_turn` 被改判为 `requires_approval`；最大回合数不计审批续接 |
| `permissions.py` | `permissions/checker.ts`、`mode.ts`、`cli/helpers/approval-classification.ts` | 执行前做 allow/deny/ask 判定；deny 规则和跨 agent 守卫优先于 `unrestricted`（上游默认）；一次性 headless 拒绝 `ask` |
| `tools.py` | `tools/manager.ts`、`agent/approval-execution.ts`、`tools/impl/truncation.ts` | 错误作为观察结果返回；PreToolUse hook 可阻止或改写；密钥脱敏；32k 截断加溢出文件；只读工具并行，`Edit`/`Write` 按文件加锁，shell 共用全局锁 |
| `reminders.py` | `reminders/engine.ts` | 易变上下文作为 user 消息上的 `<system-reminder>` 片段，从不进入 system prompt；子 agent 没有 reminder |
| `subagents.py` | `agent/subagents/manager.ts`、`tools/impl/task.ts`、`subagent-depth.ts` | `Agent` 工具；全新子 agent = 只看到任务说明的无记忆新 agent；`fork` = 父会话的拷贝 + "NOT the primary agent"；只返回报告；深度上限 2 |

刻意**没有**复现的部分：流式 token 增量、run 的续接/重放、Letta Cloud、pi-ai 的模型提供方适配器、PreToolUse 以外的 hook、
操作系统沙箱、reflection（"dreaming"）、memory worker 与 worktree、skill 发现、MCP、mod、渠道和 TUI。
`minilc` 在进程内运行子 agent；上游会启动一个独立的 `letta` 进程。

## 运行

```bash
./run.sh          # venv -> pip install -e .[test] -> 构建 wheel -> pytest -> 演示
./run.sh demo     # 只跑演示，不安装（需要 python3 >= 3.10，PATH 上有 git）
python3 mutation_check.py   # 注入 9 种架构回归；每一种都必须让某个测试失败
```

## 上游检查（需要一个位于 `4b028fa` 并已 `bun install` 的 letta-code 克隆）

```bash
./run.sh probe /path/to/letta-code        # 驱动真实的 LocalBackend（不联网）
```

`upstream_probe/probe_letta_code.ts` 用一个记录请求的执行器驱动真实的 `LocalBackend`，
检查下面这些结论，输出在 `upstream_probe/probe_output.json`。

| 探针 | 结论 | 结果 |
|---|---|---|
| P1 | 工具调用以 `requires_approval` 结束 run；审批变成 `toolResult` | 成立 |
| P2 | 未提交的记忆修改不会到达模型 | 成立 |
| P3a | 记忆提交后的第一次调用带 `<memory_update>`；system prompt 字节不变 | 成立 |
| P3b | 同一会话中之后的调用仍能看到已提交的记忆 | **不成立** |
| P4a | 压缩追加一行；之前的 transcript 行不变；视图缩小 | 成立 |
| P4b | 压缩后：摘要是 user 角色的 `system_alert`，提示词按新记忆重新渲染 | 成立 |
| P5 | 悬空的工具调用以合成的错误结果收尾 | 成立 |
| P6 | 活动 run 期间的第二个回合被拒绝 | 成立 |
| P7 | 阈值 = 窗口 − min(16384, 20%) | 成立 |

## 真实模型（`real_model/`）

`run_letta_real.py` 以 headless 模式、`--backend local` 驱动**发布用的构建产物**（从 `4b028fa` 构建的 `letta.js`）
对接真实模型。它通过 `ark_shim.py` 使用火山引擎 Ark 上的 `deepseek-v4-1-flash`；`ark_shim.py` 是一个记录请求的
OpenAI 兼容代理。需要这个代理，是因为 Ark 的 `/models` 列表为空，letta-code 的模型发现找不到该模型。
代理同时记录每个请求体，从而能看到每次调用实际进入模型上下文的内容。`ARK_API_KEY` 从环境变量读取，从不写入日志。

```bash
bun install && bun run build                  # 在 letta-code 克隆中执行 -> letta.js
ARK_API_KEY=... python3 real_model/run_letta_real.py --letta-js /path/to/letta.js --out results.json
```

三次完整运行的结果保存在 `results_run{1,2,3}.json`。`sample_request_S1.json` 是一个完整的首个请求：
system prompt、`<system-reminder>` 片段和工具 schema 的大小。`provider_usage.json` 按顺序列出三次运行的每一次模型调用：
请求形状、system prompt 哈希，以及模型提供方报告的提示词、缓存和最大补全 token。它由 `extract_usage.py`
从代理日志中提取，不包含任何消息正文。

`provider_usage.json` 显示了两点：

- **缓存命中。** 每次 S1 调用至少 98% 的提示词 token 命中缓存。
- **前缀被打破。** S2 中唯一携带 `<memory_update>` 的那次调用只有约 22% 命中缓存。对这个模型，pi-ai 把更新并进了 system 消息，前缀因此改变。

| 场景 | 检查（3 次运行） |
|---|---|
| S1 工具循环（Read → Write） | 5/5 项检查 3/3 通过：每步模型调用一个 run，提示词字节相同，每次调用都带 schema，reminder 在 user 消息上 |
| S2 记忆（保存 → 同一会话回忆 → 新会话回忆） | 5/5，3/3：提交只在下一次调用中出现一次；同一会话之后的调用**不**带它；新会话的提示词包含它 |
| S3a 子 agent，一次性 `-p` | 6/6，3/3：子 agent 是独立的无记忆 agent，只看到任务说明；父 agent 在报告到达前结束回合 |
| S3b 子 agent，双向 stream-json | 4/4，3/3：报告作为 `<task-notification>` 回合返回；子 agent 的上下文从不进入父 agent |
| S4a 跨回合压缩（45k 窗口） | 压缩 + 只追加的 transcript + 摘要作为 `system_alert`：3/3；第 C 回合同时回忆起两个事实：2/3 |
| S4b 压缩压力测试（一步内读两个文件，40k 窗口） | 压缩触发 3/3；答案丢失 3/3（`max_tokens_exceeded`） |

## 已知局限

- 只有一个模型（`deepseek-v4-1-flash`）、小任务、每个场景 3 次运行。这是证据，不是基准测试。
- S4 通过在两次 CLI 运行之间修改存储的会话记录，把会话窗口降到 40–45k token（`letta model set --model-settings` 只更新 `model_settings`，而顶层的 `context_window_limit` 优先）。这是刻意给设计加压；128k 以上的窗口很少走到这条路径。
- `minilc` 用线程执行工具批次，没有流式输出、续接和操作系统沙箱。
